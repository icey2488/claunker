"""One-way projection ``Task → Card`` (kanbantt-mcp-spec v0.2.4 Card shape).

A LENS, not a mirror: strictly one-directional (the spine never reads a Card back)
and deliberately flattening. The locked v1 mapping:

    Task.state         → column_id    (one-to-one: six states, six columns)
    Task.tier          → a tag        ("tier:N" in the tags array)
    unresolved escalation → a badge   (extension field; see below)
    gate_status        → extension field, hardcoded "COMMITTED"
    id, title, order, version, created_at → pass through
    everything else    → Card schema defaults (priority "med", empty collections,
                         nulls). The v1 entities carry no actor attribution or
                         update timestamp, so created_by/updated_by/updated_at
                         project as null.

Escalation is ORTHOGONAL to the board column: an unresolved escalation attaches a
``badge`` but never moves the card out of its ``state`` column (escalation is
neither a column nor a tag). The badge carries exactly what an approval-queue
filter needs — the escalation id, its reason, and its control_diff (which contains
``reduces_control``).

Two Claunker extension fields ride along (``gate_status``, ``badge``). The spec
mandates unknown fields are "preserved and round-tripped, never stripped", so they
survive a Kanbantt round trip cleanly. Soft-deleted Tasks are OMITTED entirely.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .entity import Escalation, Task

# Every projected card is committed state by construction (the write/gate path is a
# later slice). Claunker extension field, not a native Card field.
GATE_STATUS_COMMITTED = "COMMITTED"

# Card spec default when a card carries no explicit priority.
DEFAULT_PRIORITY = "med"


def _tags_for(task: Task) -> List[str]:
    """tier → a tag id in the Card ``tags`` array (omitted until a tier is set)."""
    return [f"tier:{task.tier}"] if task.tier is not None else []


def _badge_for(escalation: Optional[Escalation]) -> Optional[Dict[str, Any]]:
    """An unresolved escalation → the approval badge extension; else ``None``.
    Carries the fields an approval-queue filter needs (id, reason, control_diff)."""
    if escalation is None:
        return None
    return {
        "kind": "escalation",
        "id": escalation.id,
        "reason": escalation.reason,
        "control_diff": escalation.control_diff,
    }


def to_card(task: Optional[Task], escalation: Optional[Escalation] = None) -> Optional[Dict[str, Any]]:
    """Project one Task to a Card dict, or ``None`` if it is soft-deleted (and thus
    omitted from board output). ``escalation`` is the task's chosen unresolved
    escalation (or ``None``); it sets the badge without affecting ``column_id``."""
    if task is None or task.deleted_at is not None:
        return None

    return {
        # ── native Card fields (kanbantt-mcp-spec §Card) ──────────────────────
        "id": task.id,
        "title": task.title,
        "description": "",
        "column_id": task.state,            # state → column, one-to-one
        "order": task.order,
        "tags": _tags_for(task),
        "checklist": [],
        "due": None,
        "priority": DEFAULT_PRIORITY,
        "effort": None,
        "impact": None,
        "version": task.version,
        "deleted_at": None,                 # soft-deleted tasks are omitted, never projected
        "created_at": task.created_at,
        "updated_at": None,                 # v1 entities track no update timestamp
        "created_by": None,                 # v1 entities carry no actor attribution
        "updated_by": None,
        "attachments": [],
        # ── Claunker extensions (preserved by the unknown-field round-trip rule) ─
        "gate_status": GATE_STATUS_COMMITTED,
        "badge": _badge_for(escalation),
    }


def _unresolved_by_task(escalations: Iterable[Escalation]) -> Dict[str, Escalation]:
    """task_id → its chosen unresolved+live escalation. An escalation counts only
    when ``resolved_at is None`` AND ``deleted_at is None``. On multiple, the
    oldest (min created_at, id tiebreak) wins — the one waiting longest."""
    chosen: Dict[str, Escalation] = {}
    for e in escalations:
        if e.deleted_at is not None or e.resolved_at is not None:
            continue
        cur = chosen.get(e.task_id)
        if cur is None or (e.created_at or "", e.id) < (cur.created_at or "", cur.id):
            chosen[e.task_id] = e
    return chosen


def project(tasks: Iterable[Task], escalations: Iterable[Escalation] = ()) -> List[Dict[str, Any]]:
    """Project tasks to the Card list: omit soft-deleted tasks, attach an approval
    badge for any task with an unresolved escalation, and sort by ``(order, id)``
    (the spec's stable tiebreak on an order collision)."""
    badge_for_task = _unresolved_by_task(escalations)
    cards = []
    for task in tasks:
        card = to_card(task, badge_for_task.get(task.id))
        if card is not None:
            cards.append(card)
    cards.sort(key=lambda c: (c["order"], c["id"]))
    return cards
