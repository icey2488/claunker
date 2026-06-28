"""``Spine`` — the facade that ties the event log, reducer, ordering, and
projection together behind semantic write paths.

Each write helper reduces the entity's current state, enforces the immutability
of tombstones (the one policy the pure reducer leaves to the write layer), appends
the event, then stamps the resulting version token onto the row (two-phase: append
→ reduce → ``set_event_version``). The reducer is the single source of truth for
that token; the stamp just makes the log self-describing.

Reads return reduced ``TaskEntity`` objects; ``cards()`` returns the projected,
soft-delete-omitting Card view.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from .entity import Actor, Lifecycle, TaskEntity
from .events import EventStore, EventType
from .ordering import append_rank
from .projection import project
from .reducer import reduce, reduce_all


class Spine:
    def __init__(self, store: Optional[EventStore] = None) -> None:
        self.store = store or EventStore()

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, entity_id: str) -> Optional[TaskEntity]:
        events = self.store.read_events(entity_id)
        return reduce(events) if events else None

    def _all_entities(self) -> List[TaskEntity]:
        entities = list(reduce_all(self.store.read_all_events()).values())
        entities.sort(key=lambda e: (e.order, e.id))
        return entities

    def list_entities(self, include_deleted: bool = False) -> List[TaskEntity]:
        entities = self._all_entities()
        if include_deleted:
            return entities
        return [e for e in entities if e.deleted_at is None]

    def cards(self) -> List[dict]:
        """The projected board view (soft-deleted entities omitted by the lens)."""
        return project(self._all_entities())

    # ── write internals ──────────────────────────────────────────────────────
    def _commit(self, entity_id: str, event_type: str, payload: dict, actor: str) -> TaskEntity:
        seq = self.store.append_event(entity_id, event_type, payload, actor)
        entity = reduce(self.store.read_events(entity_id))
        assert entity is not None  # we just appended at least one event
        self.store.set_event_version(seq, entity.version)
        return entity

    def _require_live(self, entity_id: str) -> TaskEntity:
        current = self.get(entity_id)
        if current is None:
            raise KeyError(f"unknown entity {entity_id!r}")
        if current.deleted_at is not None:
            raise ValueError(f"entity {entity_id!r} is soft-deleted (tombstone is immutable)")
        return current

    # ── semantic write paths ─────────────────────────────────────────────────
    def create_task(
        self,
        title: str,
        actor: str = Actor.OPERATOR,
        *,
        lifecycle: str = Lifecycle.CREATED,
        tier: Optional[int] = None,
        entity_id: Optional[str] = None,
    ) -> TaskEntity:
        """Create a task, append-at-end in creation order. The order is seeded
        after the current max live rank; ``rebalance`` is NEVER invoked here."""
        entity_id = entity_id or str(uuid.uuid4())  # client-minted UUIDv4 per Card spec
        last_order = max((e.order for e in self.list_entities()), default="")
        payload: dict = {
            "title": title,
            "order": append_rank(last_order),
            "lifecycle_state": lifecycle,
        }
        if tier is not None:
            payload["tier"] = tier
        return self._commit(entity_id, EventType.CREATED, payload, actor)

    def change_title(self, entity_id: str, title: str, actor: str = Actor.CLAUDE) -> TaskEntity:
        self._require_live(entity_id)
        return self._commit(entity_id, EventType.TITLE_CHANGED, {"title": title}, actor)

    def move_column(
        self,
        entity_id: str,
        lifecycle_state: Optional[str] = None,
        order: Optional[str] = None,
        actor: str = Actor.CLAUDE,
    ) -> TaskEntity:
        """Move (and/or reposition) a task. Mirrors the Card spec's card_move,
        which carries column and order together."""
        self._require_live(entity_id)
        payload: dict = {}
        if lifecycle_state is not None:
            payload["lifecycle_state"] = lifecycle_state
        if order is not None:
            payload["order"] = order
        if not payload:
            raise ValueError("move_column requires lifecycle_state and/or order")
        return self._commit(entity_id, EventType.COLUMN_CHANGED, payload, actor)

    def assign_tier(self, entity_id: str, tier: int, actor: str = Actor.CLAUDE) -> TaskEntity:
        self._require_live(entity_id)
        return self._commit(entity_id, EventType.TIER_ASSIGNED, {"tier": tier}, actor)

    def raise_escalation(self, entity_id: str, ref: str, actor: str = Actor.CLAUDE) -> TaskEntity:
        self._require_live(entity_id)
        return self._commit(entity_id, EventType.ESCALATION_RAISED, {"ref": ref}, actor)

    def resolve_escalation(
        self, entity_id: str, actor: str = Actor.OPERATOR, resolution: Optional[str] = None
    ) -> TaskEntity:
        self._require_live(entity_id)
        payload = {} if resolution is None else {"resolution": resolution}
        return self._commit(entity_id, EventType.ESCALATION_RESOLVED, payload, actor)

    def soft_delete(self, entity_id: str, actor: str = Actor.OPERATOR) -> TaskEntity:
        """Soft-delete (tombstone). The entity remains in the log and reduces with
        ``deleted_at`` set, but the projection omits it."""
        self._require_live(entity_id)
        return self._commit(entity_id, EventType.DELETED, {}, actor)
