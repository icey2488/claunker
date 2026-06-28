"""The locked v1 Claunker Spine store: a 4-table SQLite JSON-blob store.

This REPLACES the old event-log core (events.py + reducer.py are gone). There is
no event log, no reducer, no event playback — each entity is stored and read as a
JSON blob in its own table:

    <table> ( id TEXT PRIMARY KEY, data TEXT NOT NULL )   -- data = the entity blob

one table per entity kind (``projects``, ``tasks``, ``artifacts``, ``escalations``).
The connection is opened WAL (``PRAGMA journal_mode=WAL``) so reads never block the
single writer. The ``.db`` file (and its ``-wal``/``-shm`` siblings) is gitignored.

Each table is fronted by an ``EntityStore`` exposing the per-entity ops
``get / put / list_live / list_all / soft_delete``. ``put`` is the *only* place a
version token is minted: it bumps the store's monotonic ``seq`` and stamps
``version = make_version(seq, entity.content())`` before writing, so every put
yields a fresh equality-only token.

``dump()`` / ``load()`` are the whole-blob seam for a *future* Google-Drive sync
(``sync-merge`` is out of scope this slice — this is just the seam): ``dump``
returns ``{schema_version, seq, projects[], tasks[], artifacts[], escalations[]}``
and ``load`` writes each collection straight back via INSERT OR REPLACE, preserving
the stored version tokens (no re-stamp) and restoring ``seq``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .entity import Artifact, Escalation, Project, Task, _Entity
from .version import make_version

# Schema version for the dump/load envelope; bump on a breaking blob-shape change.
SCHEMA_VERSION = 1

# Default on-disk location for a real spine (tests use ":memory:"). Gitignored.
DB_PATH = os.path.join(os.path.dirname(__file__), "spine.db")

# One table per entity kind. Order is the canonical dump/load order.
TABLES = ("projects", "tasks", "artifacts", "escalations")


def utcnow_iso() -> str:
    """ISO-8601 UTC timestamp. Timestamps are display/audit metadata only — never a
    sync or ordering primitive (``seq`` orders changes; ``order`` orders the board)."""
    return datetime.now(timezone.utc).isoformat()


class EntityStore:
    """Typed facade over one ``(id, data)`` table. Holds a back-reference to the
    owning ``Store`` for the shared connection and the monotonic ``seq``."""

    def __init__(self, store: "Store", table: str, from_dict: Callable[[Dict[str, Any]], _Entity]) -> None:
        self._store = store
        self._table = table
        self._from_dict = from_dict

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._store._conn

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, entity_id: str) -> Optional[_Entity]:
        row = self._conn.execute(
            f"SELECT data FROM {self._table} WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._from_dict(json.loads(row[0])) if row else None

    def list_all(self) -> List[_Entity]:
        """Every row, tombstones included."""
        rows = self._conn.execute(f"SELECT data FROM {self._table}").fetchall()
        return [self._from_dict(json.loads(r[0])) for r in rows]

    def list_live(self) -> List[_Entity]:
        """Only rows with ``deleted_at is None`` (filtered in Python so the store
        carries no dependency on the SQLite JSON1 extension)."""
        return [e for e in self.list_all() if getattr(e, "deleted_at", None) is None]

    # ── writes ───────────────────────────────────────────────────────────────
    def put(self, entity: _Entity) -> _Entity:
        """INSERT OR REPLACE the entity, stamping a fresh version token first. The
        bumped ``seq`` guarantees the token changes on every put; the content hash
        makes it content-addressable. Returns the (now-versioned) entity."""
        entity.version = make_version(self._store._next_seq(), entity.content())  # type: ignore[attr-defined]
        self._conn.execute(
            f"INSERT OR REPLACE INTO {self._table} (id, data) VALUES (?, ?)",
            (entity.id, json.dumps(entity.to_dict(), default=str)),  # type: ignore[attr-defined]
        )
        self._conn.commit()
        return entity

    def soft_delete(self, entity_id: str) -> _Entity:
        """Tombstone the entity (set ``deleted_at``, re-put). The re-put bumps the
        version — a soft delete is a real change. Raises ``KeyError`` if absent."""
        entity = self.get(entity_id)
        if entity is None:
            raise KeyError(f"{self._table[:-1]} {entity_id!r} does not exist")
        entity.deleted_at = utcnow_iso()  # type: ignore[attr-defined]
        return self.put(entity)


class Store:
    """The four-table SQLite store. ``path=":memory:"`` for tests; ``DB_PATH`` (or
    any file path) for a real spine. Exposes the four ``EntityStore``s as
    attributes plus the dump/load sync seam."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")  # no-op ('memory') on :memory:
        for table in TABLES:
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
        self._conn.commit()

        # Monotonic change counter — the version-token prefix and the merge clock.
        # In-memory (fresh open starts at 0); carried across the sync seam by
        # dump()/load(), not by the raw .db file (see module docstring).
        self.seq = 0

        self.projects = EntityStore(self, "projects", Project.from_dict)
        self.tasks = EntityStore(self, "tasks", Task.from_dict)
        self.artifacts = EntityStore(self, "artifacts", Artifact.from_dict)
        self.escalations = EntityStore(self, "escalations", Escalation.from_dict)

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    # ── lifecycle ────────────────────────────────────────────────────────────
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── whole-blob sync seam (Drive sync itself is OUT of scope this slice) ────
    def dump(self) -> Dict[str, Any]:
        """Snapshot the whole store as a JSON-serializable envelope. Each table's
        entries are the parsed entity blobs (version tokens intact)."""
        out: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "seq": self.seq}
        for table in TABLES:
            rows = self._conn.execute(f"SELECT data FROM {table}").fetchall()
            out[table] = [json.loads(r[0]) for r in rows]
        return out

    def load(self, blob: Dict[str, Any]) -> None:
        """Write a dumped envelope back, preserving each blob's stored version
        token (raw INSERT OR REPLACE — NOT a re-stamping put) and restoring ``seq``."""
        self.seq = blob.get("seq", self.seq)
        for table in TABLES:
            for entity_blob in blob.get(table, []):
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {table} (id, data) VALUES (?, ?)",
                    (entity_blob["id"], json.dumps(entity_blob, default=str)),
                )
        self._conn.commit()
