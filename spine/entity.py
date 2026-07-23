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
# The provenance sub-keys THIS server MODELS. These three are OUR contract, so their
# VALUES are string-validated (see ``_validate_created_by``). Every OTHER (unknown/foreign)
# key is NOT ours to shape: its value may be any JSON-serializable value — string, number,
# boolean, null, object, or array — to honour the spec's unknown-key interop promise. The
# abuse surface that opens (an unbounded / deeply-nested foreign value) is closed by SIZE
# admission caps, not by a value-type rule: a serialized-byte ceiling and a nesting-depth
# ceiling (see ``check_created_by_limits``), not by forcing every value flat to a string.
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
#   MAX_PROVENANCE_VALUE_LEN — chars per provenance STRING value. Model ids, effort words,
#                           and job uuids are all < 100 chars; 512 admits a long vendor trace
#                           or URL while rejecting prose / base64 blobs stuffed into a key.
#                           Applies to string values only; NON-string values (numbers, bools,
#                           null, nested objects/arrays) are measured solely by the byte cap on
#                           the serialized whole (below), which is the true guard on their size.
#   MAX_PROVENANCE_DEPTH  — max nesting depth of the serialized created_by, the created_by
#                           object itself counting as level 1 (so foreign values may nest up to
#                           MAX_PROVENANCE_DEPTH − 1 containers below the top). Now that unknown
#                           keys admit any JSON value again, this stops a deeply-recursive payload
#                           being used as a parser bomb — and is enforced with an ITERATIVE walk
#                           that bails at the limit, so the check itself can never blow the stack.
#                           A real vendor trace ({"span":…,"duration":…}) sits at depth 2.
#   MAX_CREATED_BY_BYTES  — hard ceiling on the serialized whole object (the PRIMARY defense: it
#                           bounds long KEY NAMES the per-value cap misses AND the total size of
#                           any non-string values the per-value cap no longer measures).
#                           A real created_by serializes to ~150 bytes; 4 KiB is ~25×
#                           headroom yet blocks any multi-megabyte payload outright.
MAX_PROVENANCE_KEYS = 12
MAX_PROVENANCE_VALUE_LEN = 512
MAX_PROVENANCE_DEPTH = 3
MAX_CREATED_BY_BYTES = 4096


def _validate_created_by(v: Any) -> None:
    """Raise SpineError if v is not a valid created_by SHAPE (identity + modeled-key types).

    IDENTITY (``type`` + ``id``) is REQUIRED and unchanged: ``type`` ∈ {human, agent},
    ``id`` a non-empty string. The three MODELED provenance keys — ``model``/``effort``/
    ``job_id`` — are OUR contract, so each MUST be a string WHEN PRESENT.

    Every OTHER (unknown/foreign) key is tolerated with ANY JSON-serializable value —
    string, number, boolean, null, object, or array. This honours the spec's unknown-key
    interop promise: a foreign MCP server may carry a structured provenance dialect (e.g.
    ``{"vendor_trace": {"span": "abc", "duration": 12}}``) and we must not hard-reject its
    whole ``card_create``. The abuse surface that opens (an unbounded or deeply-recursive
    foreign value) is closed at admission by SIZE caps — a serialized-byte ceiling and a
    nesting-depth ceiling in ``check_created_by_limits`` — NOT by forcing values flat here.

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
    for key in _PROVENANCE_STR_KEYS:
        if key in v and not isinstance(v[key], str):
            raise SpineError(
                f"created_by.{key} provenance must be a string, got {v[key]!r} "
                "(the modeled keys model/effort/job_id are our contract and must be strings)"
            )


def _exceeds_depth(value: Any, limit: int) -> bool:
    """True iff any object/array in ``value`` nests deeper than ``limit`` levels, the
    top ``value`` counting as level 1. Only CONTAINERS (dict/list) add a level — a scalar
    leaf sits at its parent's depth, so ``{"a": {"b": 1}}`` is depth 2, not 3.

    ITERATIVE with an explicit stack, so a maliciously deep payload can never drive the
    check itself into a RecursionError: an over-depth container is found and returned on
    in O(depth) work, before ``json.dumps`` (recursive) is ever handed the payload."""
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, (dict, list)):
            continue
        if depth > limit:
            return True
        children = node.values() if isinstance(node, dict) else node
        for child in children:
            stack.append((child, depth + 1))
    return False


def check_created_by_limits(v: Any) -> None:
    """Enforce the created_by ADMISSION CAPS at the write boundary (→ SpineError, which
    the server maps to ``validation_failed``). Fails CLOSED and names the specific limit
    exceeded — the whole create is rejected, never silently truncated (silent truncation
    is the ``description``-drop failure mode we are not duplicating).

    Assumes ``_validate_created_by`` has already established the shape (identity present,
    modeled keys are strings). Unknown keys may hold any JSON value, so this layer bounds
    SIZE and DEPTH — the real guards now that value-type is no longer forced flat:
      * key-count cap — bounds fan-out;
      * per-value length cap — STRING values only (non-strings are size-bounded by bytes);
      * DEPTH cap — checked BEFORE serialization so a parser-bomb payload is rejected by the
        iterative walk, never by the recursive ``json.dumps`` below;
      * serialized-byte cap — the PRIMARY defense: bounds long key names AND the total size
        of any non-string values.
    Non-dict ``v`` is a no-op — shape validation owns that rejection. Documented in
    kanbantt-mcp-spec §created_by so a foreign MCP agent knows the contract."""
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
    # DEPTH before bytes: the iterative walk safely rejects a deeply-nested payload that
    # would otherwise recurse through json.dumps.
    if _exceeds_depth(v, MAX_PROVENANCE_DEPTH):
        raise SpineError(
            f"created_by nests deeper than the allowed limit "
            f"(max nesting depth {MAX_PROVENANCE_DEPTH})"
        )
    serialized_bytes = len(json.dumps(v, default=str, sort_keys=True).encode("utf-8"))
    if serialized_bytes > MAX_CREATED_BY_BYTES:
        raise SpineError(
            f"created_by serialized size exceeds cap "
            f"({serialized_bytes} > {MAX_CREATED_BY_BYTES} byte max)"
        )


# ── description ADMISSION CAP (write-boundary policy; see ``check_description_limits``) ──
# ``description`` is the spec-conformant, agent-agnostic narrative Card BODY (Markdown).
# Unlike a provenance VALUE (a model id / effort word / uuid, capped at 512 chars), it is
# free-form prose and needs a far larger ceiling — but it is still bounded at the CREATE/
# UPDATE boundary, the same prevention-at-admission discipline the ``created_by`` caps
# apply: an unbounded body is a storage-abuse vector. 16 KiB (16384 chars) admits a rich
# multi-paragraph Markdown body — headings, a list, a fenced code block, ~8 pages of prose
# — while rejecting a megabyte paste. It is ~32× the per-provenance-value cap and is
# counted in CHARACTERS, matching ``MAX_PROVENANCE_VALUE_LEN``'s unit (a body is a single
# string, so a char cap is the natural measure; a byte cap would penalise non-ASCII prose).
MAX_DESCRIPTION_LEN = 16384


def check_description_limits(v: Any) -> None:
    """Enforce the ``description`` length cap at the write boundary (→ SpineError, which
    the server maps to ``validation_failed``). ``None`` is a no-op (absent body — the
    write-once-vs-mutable distinction is the caller's, not this cap's). A NON-string is a
    SHAPE error owned by the Task constructor (``_validate_description``); this size layer
    measures strings only. Fails CLOSED and NAMES the limit — never truncates: silent
    truncation of the body is exactly the failure mode this whole contract exists to end."""
    if v is None:
        return
    if isinstance(v, str) and len(v) > MAX_DESCRIPTION_LEN:
        raise SpineError(
            f"description exceeds the {MAX_DESCRIPTION_LEN}-character limit "
            f"({len(v)} chars); trim the body or link out to external storage"
        )


def _validate_description(v: Any) -> None:
    """Raise SpineError unless ``v`` is a string or ``None``. SHAPE only — runs on
    construct AND load (``__post_init__``); the SIZE cap is admission policy
    (``check_description_limits``), so restore/load never re-polices an admitted body."""
    if v is not None and not isinstance(v, str):
        raise SpineError(f"description must be a string or null, got {v!r}")


# ── metadata (PRESERVED CARD KEYS) ADMISSION CAPS ────────────────────────────────────
# The spec's unknown-field rule (§Schema Versioning & Forward Compatibility: "unknown
# fields are preserved and round-tripped, never stripped, never an error") applies to the
# whole Card body, not just to ``created_by``. Two classes of key route through this typed
# map — stored intact, echoed on projection — instead of being silently flattened away at
# the write boundary (the old ``description``-drop failure mode, generalized):
#   1. genuinely-FOREIGN keys — a newer client's field or a foreign server's extension
#      (the forward-compat case: a v0.9 field survives a round trip through a v0.8 server);
#   2. spec-DEFINED-but-unmodeled Card fields — ``priority`` / ``checklist`` / ``attachments``
#      (v0.8.0): the spine has no first-class column for them, but the spec DEFINES them, so
#      dropping the client's value while preserving a random foreign key would punish spec
#      compliance and reward deviation. They route through the SAME preservation path.
#
# UNCOUPLED FROM THE PROVENANCE BUDGET (v0.8.0). Metadata FORMERLY aliased the ``created_by``
# interop budget (12 keys / 512 chars / depth 3 / 4096 bytes). That was defensible when
# metadata held only stray scalar foreign keys — the same shape class as a ``created_by``
# provenance dialect. It is NOT defensible now that ``checklist`` and ``attachments`` —
# legitimately larger COLLECTIONS — route through it: a realistic checklist alone blows a
# 4096-byte ceiling. So these caps are now DEFINED INDEPENDENTLY (never aliased) and RAISED
# to fit real Card payloads. The provenance caps above are UNCHANGED — the two budgets no
# longer move together. Same admission discipline: bounded at create/update, fail-closed,
# name the specific limit exceeded, never truncate.
#
# SIZED AGAINST a realistic worst-case Card body: a ~40-item checklist
# (``[{"text": <~120 chars>, "done": bool}]`` ≈ 40×145 ≈ 5.8 KB) + a ~15-item attachments
# list (``[{"id": <uuid>, "ref": <~200-char url>}]`` ≈ 15×265 ≈ 4.0 KB) + ``priority`` + a
# handful of genuinely-foreign keys ⇒ ~10-12 KB realistic. Each cap justified inline:
#
#   MAX_METADATA_KEYS = 24 — PER-OBJECT key fan-out, enforced on EVERY object at EVERY depth
#       (NOT top-level-only). A collection is ONE key holding many items, so item COUNT does not
#       spend this. Room for the 3 known-unmodeled Card fields + ~21 foreign-dialect keys while
#       still bounding fan-out. RECURSIVE because the top-level-only check left a hole: a nested
#       object stuffed with 500 keys sailed past the granular cap and hit only the byte backstop
#       — a vague "too big" error for a specific "too wide" abuse. (Provenance stays at 12 and
#       top-level-only — its 4 KiB byte cap makes the same nested-fan-out hole immaterial there.)
#   MAX_METADATA_VALUE_LEN = 2048 — chars per STRING value at ANY depth (a foreign note/URL,
#       ``priority``, or a checklist item's ``text``). Enforced RECURSIVELY: the long strings that
#       matter live INSIDE ``checklist``/``attachments`` arrays, not as top-level string values, so
#       a top-level-only check was near-useless (only the byte TOTAL bounded them, with a vague
#       error). Now every string the walk reaches is length-checked and NAMES its locus; the byte
#       total remains the backstop. Raised 512→2048 for a longer foreign string/URL. (Provenance
#       stays at 512 and top-level-only.)
#   MAX_METADATA_DEPTH = 4 — metadata dict (level 1) → ``checklist``/``attachments`` array
#       (2) → item object (3) → one container of HEADROOM (4) for a nested foreign value or a
#       slightly richer item. A ``[{text,done}]`` checklist reaches depth 3. (Provenance
#       stays at 3.) Enforced by the same iterative ``_exceeds_depth`` walk (no parser bomb).
#   MAX_METADATA_BYTES = 32768 — serialized-byte ceiling, the PRIMARY guard (it bounds the
#       collections the per-value cap cannot see). 32 KiB is ~2.5-3× the ~10-12 KB realistic
#       worst case above — comfortable headroom for a rich checklist + attachments + foreign
#       keys — while still rejecting a multi-megabyte paste outright. The TOTAL rises (not just
#       the per-value cap), which is the point: no single value could exceed the total anyway.
#       (Provenance stays at 4096.)
MAX_METADATA_KEYS = 24
MAX_METADATA_VALUE_LEN = 2048
MAX_METADATA_DEPTH = 4
MAX_METADATA_BYTES = 32768


def _walk_metadata_granular_caps(v: Any) -> None:
    """Enforce the GRANULAR metadata caps RECURSIVELY, at EVERY depth, in one iterative walk:
      * per-object key-count (``MAX_METADATA_KEYS``) on EVERY dict — not just the top one;
      * per-string-value length (``MAX_METADATA_VALUE_LEN``) on EVERY string the walk reaches;
      * nesting depth (``MAX_METADATA_DEPTH``) — only CONTAINERS occupy a level, a scalar leaf
        sits at its parent's depth (matching ``_exceeds_depth``).

    Why recursive: the OLD top-level-only checks left two holes now that ``checklist``/
    ``attachments`` route through metadata — nested strings are the NORMAL case, not the
    exception. A 30 KB string inside an array, or a 500-key object nested one level down, both
    slipped past the granular caps and hit only the 32 KiB byte backstop: a vague "too big"
    error instead of the specific limit that was actually blown. This walk closes both.

    ITERATIVE with an explicit stack (never recurses through the payload), so a maliciously
    deep/wide value can't drive the CHECK into a RecursionError — it is rejected in O(nodes)
    before the recursive ``json.dumps`` byte-count ever sees it. Each SpineError names the
    specific limit AND the locus (a JSONPath-ish ``metadata.checklist[3].text``) so a client
    can act. Fails CLOSED; never truncates."""
    stack = [(v, 1, "metadata")]
    while stack:
        node, depth, path = stack.pop()
        if isinstance(node, dict):
            if depth > MAX_METADATA_DEPTH:
                raise SpineError(
                    f"metadata nests deeper than the allowed limit at {path} "
                    f"(max nesting depth {MAX_METADATA_DEPTH})"
                )
            if len(node) > MAX_METADATA_KEYS:
                raise SpineError(
                    f"metadata carries too many keys at {path} "
                    f"({len(node)} > {MAX_METADATA_KEYS} max)"
                )
            for key, child in node.items():
                stack.append((child, depth + 1, f"{path}.{key}"))
        elif isinstance(node, list):
            if depth > MAX_METADATA_DEPTH:
                raise SpineError(
                    f"metadata nests deeper than the allowed limit at {path} "
                    f"(max nesting depth {MAX_METADATA_DEPTH})"
                )
            for i, child in enumerate(node):
                stack.append((child, depth + 1, f"{path}[{i}]"))
        elif isinstance(node, str):
            if len(node) > MAX_METADATA_VALUE_LEN:
                raise SpineError(
                    f"metadata string value too long at {path} "
                    f"({len(node)} > {MAX_METADATA_VALUE_LEN} char max)"
                )


def check_metadata_limits(v: Any) -> None:
    """Enforce the metadata ADMISSION CAPS at the write boundary (→ SpineError →
    ``validation_failed``). Unlike ``check_created_by_limits`` (whose 4 KiB byte cap makes a
    nested-fan-out hole immaterial, so it checks the top level only), metadata legitimately
    carries larger nested COLLECTIONS (``checklist``/``attachments``), so its granular caps —
    per-object key-count AND per-string length — are enforced RECURSIVELY at every depth by
    ``_walk_metadata_granular_caps`` (which also does the depth check, before serialization,
    so a parser-bomb payload never reaches the recursive ``json.dumps`` below). The serialized-
    byte ceiling remains the backstop for total size and long key names. Fails CLOSED, naming
    the specific limit AND its locus; the whole write is rejected, never truncated. Non-dict
    ``v`` is a no-op (shape is owned by the Task constructor)."""
    if not isinstance(v, dict):
        return
    # Granular caps + depth, recursively — the iterative walk rejects a deeply-nested or
    # widely-fanned payload BEFORE the recursive json.dumps byte-count is ever handed it.
    _walk_metadata_granular_caps(v)
    serialized_bytes = len(json.dumps(v, default=str, sort_keys=True).encode("utf-8"))
    if serialized_bytes > MAX_METADATA_BYTES:
        raise SpineError(
            f"metadata serialized size exceeds cap "
            f"({serialized_bytes} > {MAX_METADATA_BYTES} byte max)"
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
    # The spec-conformant, agent-agnostic narrative BODY (Markdown), or ``None`` when
    # the card has no body. Additive nullable field (the ``archived_at`` precedent: no
    # blob-shape break, old rows load with ``description=None`` via ``from_dict``).
    # MUTABLE — unlike write-once ``created_by``, a body is edited over a card's life.
    # Shape validated on construct/load (``_validate_description``); SIZE bounded at the
    # write boundary (``check_description_limits``).
    description: Optional[str] = None
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
    # UNMODELED FOREIGN Card keys the spine has no first-class field for, PRESERVED here
    # rather than flattened away (spec §Schema Versioning: unknown fields round-trip).
    # The server fills this at the write boundary (keys outside the known Card schema) and
    # the projection echoes it back. MUTABLE via ``card_update`` (RFC 7386 merge-patch).
    # Bounded at admission (``check_metadata_limits``) so preservation is not unbounded.
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.created_by is not None:
            _validate_created_by(self.created_by)
        _validate_description(self.description)
        if self.depends_on is None:
            self.depends_on = []
        if self.metadata is None:
            self.metadata = {}


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
