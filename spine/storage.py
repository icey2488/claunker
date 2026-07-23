"""The locked v1 Claunker Spine store: a SQLite JSON-blob store — four versioned
entity tables plus two append-only governance ledgers.

This REPLACES the old event-log core (events.py + reducer.py are gone). There is
no event log, no reducer, no event playback — each entity is stored and read as a
JSON blob in its own table:

    <table> ( id TEXT PRIMARY KEY, data TEXT NOT NULL )   -- data = the entity blob

one table per entity kind (``projects``, ``tasks``, ``artifacts``, ``escalations``),
plus ``tier_audit`` — a FIFTH table holding the append-only re-tier governance ledger
(same ``(id, data)`` JSON-blob shape, but INSERT-only: no version token, no soft
delete, no update/delete path — see ``TIER_AUDIT_TABLE`` / ``append_tier_audit``) —
and ``archive_audit``, the SIXTH table, the archive governance ledger mirroring the
same idiom (see ``ARCHIVE_AUDIT_TABLE`` / ``append_archive_audit``).
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
the stored version tokens (no re-stamp) and restoring ``seq``. The append-only
``tier_audit`` ledger is local-only this slice: like sync-merge itself, replicating
the ledger across the seam is deferred — it rides neither ``dump`` nor ``load`` yet.
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

# The append-only re-tier governance ledger (kanbantt-mcp-spec v0.3.0 §Re-tier). NOT
# an entity kind — no version token, no soft delete, no ``EntityStore`` — so it lives
# OUTSIDE ``TABLES`` (the versioned kinds the dump/load seam carries) and is created
# and queried directly here. Rows are only ever INSERTed (append-only ledger).
TIER_AUDIT_TABLE = "tier_audit"

# The append-only archive governance ledger (kanbantt-mcp-spec v0.4.0 §Archive) — the
# SIXTH table, mirroring ``tier_audit``'s idiom precisely: same ``(id, data)`` JSON-blob
# shape, INSERT-only, no version token, no soft delete, no ``EntityStore``, outside
# ``TABLES`` (it rides neither ``dump`` nor ``load`` this slice). One row per
# archive/unarchive, written atomically with the flag change — see
# ``append_archive_audit`` / ``list_archive_audit``.
ARCHIVE_AUDIT_TABLE = "archive_audit"

# The append-only card-edit audit ledger (amendment 2026-07-06) — the SEVENTH table.
# Records one row per set/change/clear of ``due``, ``effort``, ``impact``, or
# ``depends_on``, written atomically with the mutation (same ``commit=False`` / put idiom
# as ``tier_audit`` and ``archive_audit``). Schema: ``{id, card_id, field, old, new,
# actor, ts}``. No MCP tool reads it (record-now-render-later, v1).
EDIT_AUDIT_TABLE = "edit_audit"


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
        """Every row, tombstones included, in a DEFINED scan order — ``ORDER BY rowid``.
        SQLite leaves scan order UNDEFINED without an ORDER BY, so relying on the implicit
        rowid scan was undefined behaviour; pinning ``rowid`` is defined-beats-undefined
        hygiene — a stable, repeatable scan within a single database file.

        NON-LOAD-BEARING for cross-store ordering. ``rowid`` is a physical storage artifact:
        it is reassigned on ``INSERT OR REPLACE`` and does NOT survive ``dump``/``load``,
        restore, or a replica merge, so it CANNOT be trusted as a durable ordering primitive
        across those seams. Callers that need a deterministic order MUST sort on row CONTENTS
        (e.g. ``project_list`` sorts on ``(created_at, id)``, intrinsic to the data). An earlier
        comment here claimed this scan supplied ``project_list``'s same-tick tiebreak — that was
        a lie waiting to mislead: the tiebreak now lives in the data-bound sort key, not here."""
        rows = self._conn.execute(f"SELECT data FROM {self._table} ORDER BY rowid").fetchall()
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

    def hard_delete(self, entity_id: str) -> None:
        """Permanently remove the row — unlike ``soft_delete``, there is no
        tombstone and no version token left behind; the id is simply gone from the
        table. Raises ``KeyError`` if absent, so callers can't silently no-op on a
        typo'd id."""
        if self.get(entity_id) is None:
            raise KeyError(f"{self._table[:-1]} {entity_id!r} does not exist")
        self._conn.execute(f"DELETE FROM {self._table} WHERE id = ?", (entity_id,))
        self._conn.commit()


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
        # The FIFTH table: the append-only re-tier governance ledger. Same (id, data)
        # JSON-blob shape as the entity tables (mirroring the repo's storage pattern),
        # but it carries no EntityStore and is never updated or deleted — see
        # ``append_tier_audit`` / ``list_tier_audit``.
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {TIER_AUDIT_TABLE} (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        # The SIXTH table: the append-only archive governance ledger, same idiom.
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {ARCHIVE_AUDIT_TABLE} (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        # The SEVENTH table: the append-only card-edit audit ledger (amendment 2026-07-06).
        # Same (id, data) JSON-blob shape; INSERT-only; no version token, no soft delete.
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {EDIT_AUDIT_TABLE} (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
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

    # ── append-only governance ledger (tier_audit) ────────────────────────────
    def append_tier_audit(self, row: Dict[str, Any], *, commit: bool = True) -> None:
        """Append one row to the append-only ``tier_audit`` ledger. INSERT-only —
        the ledger is never updated or deleted (no such code path exists).

        ``commit=False`` STAGES the insert on the shared connection without committing,
        so a following ``EntityStore.put`` commits BOTH in a single transaction — this
        is how ``Spine.retier_task`` writes the tier change and its audit row
        atomically (the ledger can never diverge from the tier it records, and a failed
        put leaves no orphan ledger row)."""
        self._conn.execute(
            f"INSERT INTO {TIER_AUDIT_TABLE} (id, data) VALUES (?, ?)",
            (row["id"], json.dumps(row, default=str)),
        )
        if commit:
            self._conn.commit()

    def list_tier_audit(self) -> List[Dict[str, Any]]:
        """Every ledger row as a parsed blob, in insert order (``rowid``). No MCP tool
        exposes this in v1 (RECORD now, render later — the read/history surface is a
        later slice); it is the audit read path for tests and a future history tool."""
        rows = self._conn.execute(
            f"SELECT data FROM {TIER_AUDIT_TABLE} ORDER BY rowid"
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ── append-only governance ledger (archive_audit) ─────────────────────────
    def append_archive_audit(self, row: Dict[str, Any], *, commit: bool = True) -> None:
        """Append one row to the append-only ``archive_audit`` ledger — INSERT-only,
        mirroring ``append_tier_audit``'s atomic idiom: ``commit=False`` STAGES the
        insert on the shared connection so a following ``EntityStore.put`` commits
        BOTH in a single transaction (``Spine.archive_task`` / ``unarchive_task``
        write the flag change and its audit row atomically; a failed put leaves no
        orphan ledger row).

        LEDGER INVARIANT (hard): every row MUST carry a non-empty, non-whitespace
        ``reason`` — this is the NOT NULL constraint translated to the blob-row layer
        that actually exists. Rejected (``ValueError``) BEFORE anything is staged, so
        a bad row never touches the transaction. The ergonomic defaulting lives at
        the tool layer (an omitted reason becomes "manual_archive"/"manual_unarchive"
        there), never here: 100% of ledger rows are reasoned."""
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("archive_audit rows require a non-empty reason")
        self._conn.execute(
            f"INSERT INTO {ARCHIVE_AUDIT_TABLE} (id, data) VALUES (?, ?)",
            (row["id"], json.dumps(row, default=str)),
        )
        if commit:
            self._conn.commit()

    def list_archive_audit(self) -> List[Dict[str, Any]]:
        """Every ``archive_audit`` row as a parsed blob, in insert order (``rowid``).
        As ``list_tier_audit``: no MCP tool exposes this yet (record now, render
        later) — the audit read path for tests and a future history tool."""
        rows = self._conn.execute(
            f"SELECT data FROM {ARCHIVE_AUDIT_TABLE} ORDER BY rowid"
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ── append-only card-edit audit ledger (edit_audit) ──────────────────────────
    def append_edit_audit(self, row: Dict[str, Any], *, commit: bool = True) -> None:
        """Append one row to the append-only ``edit_audit`` ledger — INSERT-only,
        mirroring ``append_tier_audit``'s atomic idiom: ``commit=False`` STAGES the
        insert on the shared connection so a following ``EntityStore.put`` commits
        BOTH in a single transaction (``Spine.update_task`` stages all edit-audit rows
        before the put; a failed guard leaves no orphan ledger row).

        Row shape: ``{id, card_id, field, old, new, actor, ts}``. Written for every
        set/change/clear of ``due``, ``effort``, ``impact``, or ``depends_on`` — one
        row per field that actually changed value. No read API this version
        (record-now-render-later)."""
        self._conn.execute(
            f"INSERT INTO {EDIT_AUDIT_TABLE} (id, data) VALUES (?, ?)",
            (row["id"], json.dumps(row, default=str)),
        )
        if commit:
            self._conn.commit()

    def list_edit_audit(self) -> List[Dict[str, Any]]:
        """Every ``edit_audit`` row as a parsed blob, in insert order (``rowid``).
        No MCP tool exposes this in v1 (record-now-render-later); it is the audit
        read path for tests and a future history tool."""
        rows = self._conn.execute(
            f"SELECT data FROM {EDIT_AUDIT_TABLE} ORDER BY rowid"
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

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
