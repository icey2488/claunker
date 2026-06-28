"""Append-only event log for the Claunker Spine (SQLite).

The persistence model is an event log, NOT a CRUD state table: the current
``TaskEntity`` is always *derived* by folding its events (see ``reducer.py``).
This module is the narrow interface the design calls for — ``append_event`` plus
two read paths (by entity, and all) — and one annotation call,
``set_event_version``, used by the write path to stamp the just-appended row with
the entity version it produced (an append-only annotation of a row we just wrote,
never a state mutation).

Schema (``task_events``) is the converged design verbatim:

    seq         INTEGER PRIMARY KEY AUTOINCREMENT   -- global monotonic order
    entity_id   TEXT                                -- which entity the event is about
    version     TEXT                                -- resulting entity version token
    actor       TEXT                                -- claude | ollama | gemini | operator
    event_type  TEXT                                -- see EventType
    payload     JSON                                -- event-specific fields (json text)
    created_at  TIMESTAMP                           -- ISO 8601 string (display only)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class EventType:
    """The fixed event vocabulary the reducer folds (converged design)."""

    CREATED = "CREATED"
    TITLE_CHANGED = "TITLE_CHANGED"
    COLUMN_CHANGED = "COLUMN_CHANGED"          # move: lifecycle_state and/or order
    TIER_ASSIGNED = "TIER_ASSIGNED"
    ESCALATION_RAISED = "ESCALATION_RAISED"
    ESCALATION_RESOLVED = "ESCALATION_RESOLVED"
    DELETED = "DELETED"                         # soft delete (sets deleted_at)


@dataclass(frozen=True)
class Event:
    """One immutable row of the log. ``version`` is the entity version token AFTER
    this event (stamped post-insert by the write path; ``None`` until stamped)."""

    seq: int
    entity_id: str
    event_type: str
    payload: Dict[str, Any]
    actor: Optional[str]
    created_at: str
    version: Optional[str] = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   TEXT      NOT NULL,
    version     TEXT,
    actor       TEXT,
    event_type  TEXT      NOT NULL,
    payload     JSON,
    created_at  TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_task_events_entity ON task_events(entity_id, seq);
"""


def utcnow_iso() -> str:
    """ISO-8601 UTC timestamp. Per the Card spec, timestamps are display metadata
    only — never a synchronization or ordering primitive (that is ``seq``/``order``)."""
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    """Thin SQLite-backed append-only log. ``path=":memory:"`` for tests; a file
    path for a real spine. The file lives outside git (``*.db`` is gitignored)."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── write path (narrow) ──────────────────────────────────────────────────
    def append_event(
        self,
        entity_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        actor: Optional[str] = None,
        created_at: Optional[str] = None,
        version: Optional[str] = None,
    ) -> int:
        """Append one event and return its assigned ``seq`` (the global order). The
        ``version`` token is normally stamped afterward via ``set_event_version``
        once the post-event entity has been reduced."""
        created_at = created_at or utcnow_iso()
        cur = self._conn.execute(
            "INSERT INTO task_events (entity_id, version, actor, event_type, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entity_id, version, actor, event_type, json.dumps(payload or {}), created_at),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def set_event_version(self, seq: int, version: str) -> None:
        """Stamp a just-appended row with the entity version it produced. This is
        an annotation of an immutable historical row, not a state mutation."""
        self._conn.execute("UPDATE task_events SET version = ? WHERE seq = ?", (version, seq))
        self._conn.commit()

    # ── read paths ───────────────────────────────────────────────────────────
    def read_events(self, entity_id: str) -> List[Event]:
        """All events for one entity, in ``seq`` (apply) order."""
        rows = self._conn.execute(
            "SELECT * FROM task_events WHERE entity_id = ? ORDER BY seq", (entity_id,)
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def read_all_events(self) -> List[Event]:
        """The entire log, in global ``seq`` order."""
        rows = self._conn.execute("SELECT * FROM task_events ORDER BY seq").fetchall()
        return [self._row_to_event(r) for r in rows]

    def entity_ids(self) -> List[str]:
        """Distinct entity ids that have at least one event."""
        rows = self._conn.execute(
            "SELECT DISTINCT entity_id FROM task_events ORDER BY entity_id"
        ).fetchall()
        return [r["entity_id"] for r in rows]

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_event(r: sqlite3.Row) -> Event:
        return Event(
            seq=int(r["seq"]),
            entity_id=r["entity_id"],
            event_type=r["event_type"],
            payload=json.loads(r["payload"]) if r["payload"] else {},
            actor=r["actor"],
            created_at=r["created_at"],
            version=r["version"],
        )
