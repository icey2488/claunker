"""Fold an entity's event stream into its current ``TaskEntity``.

The reducer is the SINGLE source of truth for derived state, including the version
token: after folding, ``version = make_version(last_seq, content)``. The write
path stamps the same token onto the event row (``set_event_version``) so the log
is self-describing and the two never diverge.

The reducer is pure and permissive: it folds whatever the log holds (e.g. it will
happily fold events after a DELETED). The *write* path (``Spine``) is where the
"tombstones are immutable" policy lives — keeping the fold a total function.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .entity import Lifecycle, TaskEntity
from .events import Event, EventType
from .version import make_version


def _touch(state: TaskEntity, e: Event) -> None:
    """Advance the per-event metadata common to every non-CREATED event."""
    state.seq = e.seq
    state.updated_at = e.created_at
    state.updated_by = e.actor


def apply_event(state: Optional[TaskEntity], e: Event) -> TaskEntity:
    """Apply one event to the running state and return it. The first event for an
    entity MUST be CREATED; anything else before it is a malformed stream."""
    et = e.event_type
    p = e.payload or {}

    if et == EventType.CREATED:
        return TaskEntity(
            id=e.entity_id,
            title=p.get("title", ""),
            order=p.get("order", ""),
            lifecycle_state=p.get("lifecycle_state", Lifecycle.CREATED),
            tier=p.get("tier"),
            escalated=False,
            escalation_ref=None,
            seq=e.seq,
            deleted_at=None,
            created_at=e.created_at,
            updated_at=e.created_at,
            created_by=e.actor,
            updated_by=e.actor,
        )

    if state is None:
        raise ValueError(
            f"event {et} (seq={e.seq}) for {e.entity_id!r} arrived before CREATED"
        )

    if et == EventType.TITLE_CHANGED:
        state.title = p.get("title", state.title)
    elif et == EventType.COLUMN_CHANGED:
        # A move may carry a new lifecycle_state, a new order, or both (mirrors
        # the Card spec's card_move {column_id, order}).
        if "lifecycle_state" in p or "column" in p:
            state.lifecycle_state = p.get("lifecycle_state", p.get("column"))
        if "order" in p:
            state.order = p["order"]
    elif et == EventType.TIER_ASSIGNED:
        state.tier = p.get("tier", state.tier)
    elif et == EventType.ESCALATION_RAISED:
        state.escalated = True
        state.escalation_ref = p.get("ref", p.get("escalation_ref"))
    elif et == EventType.ESCALATION_RESOLVED:
        state.escalated = False
        state.escalation_ref = None  # no active escalation once resolved
    elif et == EventType.DELETED:
        state.deleted_at = e.created_at  # soft-delete tombstone marker
    else:
        raise ValueError(f"unknown event_type {et!r} (seq={e.seq})")

    _touch(state, e)
    return state


def reduce(events: Iterable[Event]) -> Optional[TaskEntity]:
    """Fold one entity's events (seq order) into its ``TaskEntity``, minting the
    version token from the last seq + reduced content. Returns ``None`` for an
    empty stream (an entity that was never created)."""
    state: Optional[TaskEntity] = None
    last_seq = 0
    for e in events:
        state = apply_event(state, e)
        last_seq = e.seq
    if state is not None:
        state.seq = last_seq
        state.version = make_version(last_seq, state.content())
    return state


def reduce_all(events: Iterable[Event]) -> Dict[str, TaskEntity]:
    """Group a (seq-ordered) global event stream by entity and reduce each."""
    by_entity: Dict[str, List[Event]] = {}
    for e in events:
        by_entity.setdefault(e.entity_id, []).append(e)
    return {eid: ent for eid, evs in by_entity.items() if (ent := reduce(evs)) is not None}
