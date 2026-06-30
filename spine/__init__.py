"""Claunker Spine — orchestration-state data core (locked v1 architecture).

Foundation 04 §5.9 names the *spine* as the durable orchestration-state tier
(distinct from code-on-GitHub and the RAG corpus). This package is the DATA CORE
of that tier ONLY — no MCP server, no transport, no network. It is the in-process
truth from which the Kanbantt board projection is rendered.

Shape (a plain 4-table entity store — NOT event-sourced):

    storage.py     ``Store`` over four ``(id, data)`` SQLite tables (WAL), one per
                   entity kind, each fronted by an ``EntityStore``
                   (get/put/list_live/list_all/soft_delete). ``put`` mints the
                   version token. ``dump``/``load`` are the future-Drive-sync seam.
    entity.py      the four entities — ``Project``, ``Task``, ``Artifact``,
                   ``Escalation`` — plus the ``State`` and ``ArtifactKind`` enums.
    version.py     opaque version token ``{seq}:{content_hash}`` (equality-only by
                   contract — never parsed by consumers).
    ordering.py    LexoRank string ordering: append-at-end seeding, ``rank_between``
                   for out-of-band inserts, and an out-of-band ``rebalance``.
    projection.py  one-way lens ``Task → Card`` (state→column 1:1, tier→tag,
                   unresolved escalation→badge, gate_status COMMITTED; soft-deleted
                   tasks omitted).
    spine.py       ``Spine`` — facade over the store with semantic write paths and
                   the server write-admission checks (MI-1, MI-2).

Kept import-light (stdlib only: sqlite3/json/hashlib/uuid/datetime/math). The
spine deliberately does NOT import ``hermes_cli`` or the classifier — task STATE
and tool-call GATING are separate concerns that must not couple.
"""

from .entity import (  # noqa: F401
    ARTIFACT_KINDS,
    PIPELINE_STATES,
    STATES,
    TERMINAL_STATES,
    Artifact,
    ArtifactKind,
    Escalation,
    Project,
    State,
    Task,
)
from .ordering import (  # noqa: F401
    MAX_RANK_LENGTH,
    append_rank,
    needs_rebalance,
    rank_between,
    rebalance,
)
from .projection import (  # noqa: F401
    DEFAULT_PRIORITY,
    GATE_STATUS_COMMITTED,
    project,
    to_card,
)
from .spine import RETIER_ACTOR, ConflictError, Spine  # noqa: F401
from .storage import (  # noqa: F401
    DB_PATH,
    SCHEMA_VERSION,
    TABLES,
    TIER_AUDIT_TABLE,
    EntityStore,
    Store,
    utcnow_iso,
)
from .version import content_hash, make_version  # noqa: F401
