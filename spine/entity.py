"""The four canonical Claunker Spine entities + the fixed enums they range over.

The locked v1 data core is a plain entity store, NOT an event log: each entity is
its own JSON blob, stored and read directly (see ``storage.py``). There is no
reducer and no derived ``TaskEntity`` — these dataclasses ARE the stored shape.

Every entity carries three universal fields:

    id          stable identity (client-minted UUIDv4)
    version     opaque ``{seq}:{content_hash}`` token (``version.py``); equality-
                only by contract — consumers compare it, never parse or order it.
                Stamped by the store on every ``put`` (``storage.py``).
    deleted_at  soft-delete tombstone (ISO-8601 string) or ``None`` while live.

plus their own semantic fields. ``content()`` is the slice hashed into the version
token (everything EXCEPT ``version`` itself, which would be circular); ``to_dict``/
``from_dict`` are the JSON-blob (de)serialization the store round-trips.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Optional


# ── state / kind vocabularies ────────────────────────────────────────────────
class State:
    """The Task lifecycle. Pipeline: created → tiered → dispatched → judged →
    delivered, with ``failed`` a terminal sibling of ``delivered``. There is NO
    'escalated' state — escalation is a separate entity, orthogonal to the column.
    Each state projects one-to-one onto a board column of the same id."""

    CREATED = "created"
    TIERED = "tiered"
    DISPATCHED = "dispatched"
    JUDGED = "judged"
    DELIVERED = "delivered"
    FAILED = "failed"


# Full state set (validation) and the happy-path pipeline order (documentation;
# transitions are not guarded in this slice — any state in STATES is accepted).
STATES = (
    State.CREATED,
    State.TIERED,
    State.DISPATCHED,
    State.JUDGED,
    State.DELIVERED,
    State.FAILED,
)
PIPELINE_STATES = (State.CREATED, State.TIERED, State.DISPATCHED, State.JUDGED, State.DELIVERED)
TERMINAL_STATES = (State.DELIVERED, State.FAILED)


class ArtifactKind:
    """What an Artifact points at. ``kind`` is constrained to this set."""

    DIFF = "diff"
    FILE = "file"
    VERDICT = "verdict"
    DELIVERY = "delivery"


ARTIFACT_KINDS = (ArtifactKind.DIFF, ArtifactKind.FILE, ArtifactKind.VERDICT, ArtifactKind.DELIVERY)


# ── shared (de)serialization + version-content behavior ──────────────────────
class _Entity:
    """Mixin giving every entity dataclass blob (de)serialization and the version
    content slice. Not a dataclass itself — methods only."""

    def to_dict(self) -> Dict[str, Any]:
        """Full JSON-serializable blob (the ``data`` column), version included."""
        return asdict(self)  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "_Entity":
        """Rebuild from a stored blob, ignoring any unknown keys (forward-compat:
        a blob written by a newer schema still loads its known fields)."""
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in d.items() if k in known})

    def content(self) -> Dict[str, Any]:
        """The semantic content hashed into the version token: the whole blob
        EXCEPT ``version`` (circular). Any real field change moves the token; the
        store's monotonic ``seq`` prefix moves it on every put regardless."""
        d = self.to_dict()
        d.pop("version", None)
        return d


# ── the four entities ────────────────────────────────────────────────────────
@dataclass
class Project(_Entity):
    """A unit of work that owns Tasks."""

    id: str
    name: str
    created_at: Optional[str] = None
    version: Optional[str] = None
    deleted_at: Optional[str] = None


@dataclass
class Task(_Entity):
    """A single piece of work in the pipeline. ``tier`` aligns with the
    classifier's tier space (1=self-accept .. 4=human) but is a plain int here —
    the spine does not import the classifier. ``order`` is the spine-assigned
    LexoRank board position (``ordering.py``); it is not one of the spec's core
    semantic fields but is required for the Card ``order`` passthrough and for the
    retained LexoRank ordering.

    ``archived_at`` is an ORTHOGONAL nullable flag mirroring ``deleted_at``'s shape
    exactly (ISO-8601 string while archived, ``None`` while active) — NOT a lifecycle
    state: an archived task keeps its ``state`` column and is merely filtered from
    the default ``card_list`` view. Set/cleared ONLY through the governed
    ``Spine.archive_task`` / ``unarchive_task`` pair (each write is audited in the
    append-only ``archive_audit`` ledger). Being a dataclass field, it rides
    ``content()`` into the version token automatically — archiving moves the token."""

    id: str
    project_id: str
    title: str
    state: str = State.CREATED
    tier: Optional[int] = None
    acceptance_criteria: Optional[Any] = None
    order: str = ""
    created_at: Optional[str] = None
    version: Optional[str] = None
    deleted_at: Optional[str] = None
    archived_at: Optional[str] = None


@dataclass
class Artifact(_Entity):
    """A produced output attached to a Task. ``kind`` ∈ ARTIFACT_KINDS; ``ref`` is
    an opaque pointer (path / url / blob id) the spine does not interpret."""

    id: str
    task_id: str
    kind: str
    ref: str
    created_at: Optional[str] = None
    version: Optional[str] = None
    deleted_at: Optional[str] = None


@dataclass
class Escalation(_Entity):
    """A request for human/governance attention on a Task, orthogonal to the
    Task's state column. ``resolved_at`` is ``None`` while pending (an unresolved
    escalation drives the projected approval badge). ``control_diff`` describes a
    proposed control change, or is ``None`` when the escalation proposes none:

        control_diff = { control_id, old_value, new_value, reduces_control } | None

    ``reduces_control`` flags a change that *weakens* a guardrail — the field an
    approval queue prioritizes on.

    The resolution triad is set together, in a single write, when the escalation
    is resolved (``Spine.resolve_escalation``); all three are ``None`` while
    pending:

        resolution            the human decision — ``'approve'`` or ``'deny'``.
        resolution_rationale  the operator's justification (a semantic floor of
                              >=10 non-whitespace chars is enforced on write).
        actor                 WHO resolved it — the resolving credential's
                              identity. Operator-only today; this field IS the
                              override receipt's attribution, so it is derived
                              from the authenticated credential, NEVER from a
                              client payload (see ``Spine.resolve_escalation`` and
                              the ``escalation_resolve`` tool's actor invariant).

    ``status`` is NOT a stored field: it stays derived from ``resolved_at``
    (pending while ``None``, resolved once set). The projection additionally reads
    ``resolution`` to choose the badge's three-state discriminator
    (unresolved / denied / none).
    """

    id: str
    task_id: str
    reason: str
    control_diff: Optional[Dict[str, Any]] = None
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None
    resolution_rationale: Optional[str] = None
    actor: Optional[str] = None
    created_at: Optional[str] = None
    version: Optional[str] = None
    deleted_at: Optional[str] = None
