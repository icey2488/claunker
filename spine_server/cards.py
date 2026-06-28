"""``card_list`` payload construction — the spine's live Tasks projected to Cards.

v1 serves a FULL snapshot on every call (``updated_since`` is an accepted seam, not
yet a delta) and mints a fresh ``sync_token`` each time. It NEVER truncates: an
oversized snapshot fails with ``payload_too_large`` (complete-or-error, per the
spec). Soft-deleted Tasks are omitted from the snapshot unless ``include_deleted``,
in which case they ride along as tombstones (``deleted_at`` non-null).

Reads a fresh store connection per call: the spine opens WAL, so a reader never
blocks the live writer and always sees the latest committed state. The tools are
strictly read-only — nothing here mutates the store.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any, Dict, List, Optional

from spine.entity import Task
from spine.projection import project, to_card
from spine.storage import Store


def mint_sync_token() -> str:
    """A fresh, opaque, server-minted token for every successful list (even a full
    fetch). uuid4-based, so it is guaranteed distinct per call; the client echoes it
    verbatim and never constructs one."""
    return f"st_{uuid.uuid4().hex}"


def _tombstone_card(task: Task) -> Dict[str, Any]:
    """Project a soft-deleted Task to its tombstone card. Reuses ``to_card`` (on a
    copy with ``deleted_at`` cleared so the lens emits the full card) then restores
    ``deleted_at`` — so the field mapping never drifts from the live projection. No
    approval badge: a deleted card carries no actionable affordance."""
    card = to_card(replace(task, deleted_at=None))
    card["deleted_at"] = task.deleted_at
    return card


class PayloadTooLarge(Exception):
    """The complete snapshot exceeds the configured ceiling. Surfaced as the
    ``payload_too_large`` domain error — the list tool truncates NEVER."""

    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(f"snapshot {size}B exceeds limit {limit}B")


def list_cards(
    db_path: str,
    *,
    updated_since: Optional[str] = None,  # accepted seam; v1 always returns a full snapshot
    column_id: Optional[str] = None,
    tag: Optional[str] = None,
    include_deleted: bool = False,
    max_bytes: int,
) -> Dict[str, Any]:
    """Return ``{"cards": [...], "sync_token": ...}`` — a full snapshot of the live
    Tasks projected to Cards. ``column_id`` / ``tag`` apply as trivial filters;
    ``updated_since`` is accepted but does not narrow the result in v1."""
    _ = updated_since  # documented seam: full snapshot is conforming (authoritative full fetch)

    with Store(db_path) as store:
        tasks = store.tasks.list_all()
        escalations = store.escalations.list_all()

    # project() omits soft-deleted Tasks and sorts by (order, id); badges any task
    # with a live unresolved escalation (orthogonal to its column).
    cards = project(tasks, escalations)
    if include_deleted:
        tombstones = [_tombstone_card(t) for t in tasks if t.deleted_at is not None]
        cards = sorted(cards + tombstones, key=lambda c: (c["order"], c["id"]))

    if column_id is not None:
        cards = [c for c in cards if c["column_id"] == column_id]
    if tag is not None:
        cards = [c for c in cards if tag in c["tags"]]

    result: Dict[str, Any] = {"cards": cards, "sync_token": mint_sync_token()}

    size = len(json.dumps(result, default=str).encode("utf-8"))
    if size > max_bytes:
        raise PayloadTooLarge(size, max_bytes)
    return result
