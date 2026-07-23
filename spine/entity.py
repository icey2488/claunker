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

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional


class SpineError(ValueError):
    """Validation error raised by the spine data layer on malformed entity fields."""


# Optional DISPATCH-PROVENANCE sub-keys carried INSIDE created_by (additive since the
# provenance amendment). They describe HOW an agent-minted card was produced — the
# reasoning ``model``, the reasoning ``effort`` budget, and the originating ``job_id``
# — NOT the work. Homing them here (rather than at the Task top level) is LOAD-BEARING:
# the Task already owns ``effort``/``impact`` as its Matrix work-sizing axes, and a
# top-level dispatch ``effort`` would COLLIDE with that mutable work-size field. Inside
# created_by there is no collision and the shape stays write-once with the rest of the
# mint attribution. Each is an OPTIONAL string; a human card carries none.
# The provenance sub-keys THIS server models. Documentary: validation no longer keys off
# this list — EVERY non-identity value is string-checked (see ``_validate_created_by``),
# so a foreign key is held to the same shape rule as a modeled one.
_PROVENANCE_STR_KEYS = ("model", "effort", "job_id")

# ── created_by ADMISSION CAPS (write-boundary policy; see ``check_created_by_limits``) ──
# Unknown-key tolerance (interop) + write-once immutability (audit) together mean the
# spine accepts arbitrary keys into ``created_by`` and then offers NO API to clean them
# up. Byte size must therefore be bounded at the CREATE boundary — otherwise a
# hallucinating or hostile agent could store a multi-megabyte string that is immutable
# forever. These caps are the prevention-at-admission analogue of the MI-1 zombie-append
# guard: reject the bad write, never relax immutability or add a cleanup path.
#
#   MAX_PROVENANCE_KEYS   — non-identity keys allowed alongside type/id. This server
#                           models 3 (model/effort/job_id); 12 leaves generous headroom
#                           for a foreign server's provenance dialect while bounding
#                           key fan-out. A real mint uses 3–4.
#   MAX_PROVENANCE_VALUE_LEN — chars per provenance value. Model ids, effort words, and
#                           job uuids are all < 100 chars; 512 admits a long vendor trace
#                           or URL while rejecting prose / base64 blobs stuffed into a key.
#   MAX_CREATED_BY_BYTES  — hard ceiling on the serialized whole object (the backstop that
#                           also bounds long KEY NAMES, which the per-value cap does not).
#                           A real created_by serializes to ~150 bytes; 4 KiB is ~25×
#                           headroom yet blocks any multi-megabyte payload outright.
MAX_PROVENANCE_KEYS = 12
MAX_PROVENANCE_VALUE_LEN = 512
MAX_CREATED_BY_BYTES = 4096


def _validate_created_by(v: Any) -> None:
    """Raise SpineError if v is not a valid created_by SHAPE (identity + value types).

    IDENTITY (``type`` + ``id``) is REQUIRED and unchanged: ``type`` ∈ {human, agent},
    ``id`` a non-empty string. Every OTHER key is dispatch provenance and its VALUE MUST
    be a string — this holds for the modeled keys (``model``/``effort``/``job_id``) AND
    for any unknown foreign key alike. Unknown KEYS are still tolerated (additive-only
    forward-compat / MCP interop: a foreign server may carry keys we do not model), but a
    non-string VALUE is rejected: it closes the nesting/depth hole (a nested object or
    array under an unknown key was previously admitted, since only the three modeled keys
    were type-checked) and keeps every value length-boundable by ``check_created_by_limits``.

    This is SHAPE validation only — it runs on construct AND on load (``from_dict``). The
    SIZE caps are admission policy and live in ``check_created_by_limits``, called at the
    write boundary so restore/load never re-polices already-admitted data."""
    if (
        not isinstance(v, dict)
        or v.get("type") not in ("human", "agent")
        or not isinstance(v.get("id"), str)
        or not v["id"]
    ):
        raise SpineError(
            f"created_by must be {{\"type\": \"human\"|\"agent\", \"id\": <non-empty string>}}, got {v!r}"
        )
    for key, val in v.items():
        if key in ("type", "id"):
            continue
        if not isinstance(val, str):
            raise SpineError(
                f"created_by.{key} provenance must be a string, got {val!r} "
                "(non-string values — including nested objects and arrays — are not permitted)"
            )


def check_created_by_limits(v: Any) -> None:
    """Enforce the created_by ADMISSION CAPS at the write boundary (→ SpineError, which
    the server maps to ``validation_failed``). Fails CLOSED and names the specific limit
    exceeded — the whole create is rejected, never silently truncated (silent truncation
    is the ``description``-drop failure mode we are not duplicating).

    Assumes ``_validate_created_by`` has already established the shape (every non-identity
    value is a string); this layer bounds SIZE only. Non-dict ``v`` is a no-op — shape
    validation owns that rejection. Documented in kanbantt-mcp-spec §created_by so a
    foreign MCP agent knows the contract."""
    if not isinstance(v, dict):
        return
    provenance_keys = [k for k in v if k not in ("type", "id")]
    if len(provenance_keys) > MAX_PROVENANCE_KEYS:
        raise SpineError(
            f"created_by carries too many provenance keys "
            f"({len(provenance_keys)} > {MAX_PROVENANCE_KEYS} max)"
        )
    for key in provenance_keys:
        val = v[key]
        if isinstance(val, str) and len(val) > MAX_PROVENANCE_VALUE_LEN:
            raise SpineError(
                f"created_by.{key} provenance value too long "
                f"({len(val)} > {MAX_PROVENANCE_VALUE_LEN} char max)"
            )
    serialized_bytes = len(json.dumps(v, default=str, sort_keys=True).encode("utf-8"))
    if serialized_bytes > MAX_CREATED_BY_BYTES:
        raise SpineError(
            f"created_by serialized size exceeds cap "
            f"({serialized_bytes} > {MAX_CREATED_BY_BYTES} byte max)"
        )


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
    effort: Optional[str] = None
    impact: Optional[str] = None
    due: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    order: str = ""
    created_at: Optional[str] = None
    version: Optional[str] = None
    deleted_at: Optional[str] = None
    archived_at: Optional[str] = None
    # WHO/HOW this task was minted, or ``None`` when unattributed. Identity is
    # ``{type, id}``; an agent mint MAY additionally carry dispatch provenance
    # (``model``/``effort``/``job_id``) plus tolerated foreign keys — see
    # ``_validate_created_by``. WRITE-ONCE: set at create, never mutated (no
    # ``update_task`` path touches it — the audit value is "what actually ran").
    created_by: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.created_by is not None:
            _validate_created_by(self.created_by)
        if self.depends_on is None:
            self.depends_on = []


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
