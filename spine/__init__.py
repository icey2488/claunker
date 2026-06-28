"""Claunker Spine — orchestration-state data core (read-slice foundation).

Foundation 04 §5.9 names the *spine* as the durable orchestration-state tier
(distinct from code-on-GitHub and the RAG corpus). This package is the DATA CORE
of that tier ONLY — no MCP server, no transport, no network. It is the in-process
truth from which the Kanbantt board projection is rendered.

Shape (event-sourced, single source of truth = the event log):

    events.py      append-only SQLite event log (``task_events``) + the narrow
                   write/read interface. NOT a CRUD state table.
    entity.py      ``TaskEntity`` — the canonical reduced entity, plus the
                   lifecycle→reserved-column and actor→ref mappings.
    reducer.py     fold an entity's events into its current ``TaskEntity``.
    version.py     opaque version token ``{seq}:{content_hash}`` (collision-safe,
                   equality-only by contract — never parsed by consumers).
    ordering.py    LexoRank string ordering: append-at-end seeding, ``rank_between``
                   for out-of-band inserts, and an out-of-band ``rebalance``.
    projection.py  one-way lens ``TaskEntity → Card`` conforming to the Kanbantt
                   MCP Card schema (soft-deleted entities omitted; ``gate_status``
                   stamped COMMITTED on every card).
    spine.py       ``Spine`` — a thin facade tying the log + reducer + ordering +
                   projection together with semantic write paths.

Kept import-light (stdlib only: sqlite3/json/hashlib/uuid/datetime/math). The
spine deliberately does NOT import ``hermes_cli`` or the classifier — task STATE
and tool-call GATING are separate concerns that must not couple.
"""

from .entity import (  # noqa: F401
    Actor,
    Lifecycle,
    RESERVED_COLUMNS,
    TaskEntity,
    actor_ref,
    lifecycle_to_column,
)
from .events import Event, EventStore, EventType  # noqa: F401
from .ordering import (  # noqa: F401
    MAX_RANK_LENGTH,
    append_rank,
    needs_rebalance,
    rank_between,
    rebalance,
)
from .projection import GATE_STATUS_COMMITTED, project, to_card  # noqa: F401
from .reducer import apply_event, reduce, reduce_all  # noqa: F401
from .spine import Spine  # noqa: F401
from .version import content_hash, make_version  # noqa: F401
