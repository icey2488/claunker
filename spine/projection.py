"""One-way projection ``TaskEntity → Card`` conforming to the Kanbantt MCP Card
schema (kanbantt-mcp-spec.md v0.2.4, schema_version 1).

This is a LENS, not a mirror: it is strictly one-directional (the spine never
reads a Card back), and it deliberately flattens/drops the rich orchestration
fields. The converged mapping:

    lifecycle_state  → column_id   (one-to-one: each state is its own column)
    tier             → a tag        ("tier:N" in the tags array)
    escalated        → a badge      (see note below)
    version, order   → pass through
    everything else  → Card defaults (priority "med", empty collections, nulls)

Two Claunker extension fields ride along — ``gate_status`` (hardcoded COMMITTED,
the spine only ever projects committed state) and ``badge``. Neither is a native
Card field, but the spec mandates unknown fields are "preserved and round-tripped,
never stripped, never an error", so they survive a Kanbantt round trip cleanly.

Soft-deleted entities (``deleted_at`` set) are OMITTED entirely — the projection
is a *full-state* view, and the spec's full-fetch semantics treat a card's absence
as authoritative (deletion), which is exactly the intent for a tombstone here.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .entity import TaskEntity, actor_ref, lifecycle_to_column

# Every projected card is committed state by construction (the spine projects only
# what is in the log). This is a Claunker extension field, not a native Card field.
GATE_STATUS_COMMITTED = "COMMITTED"

# Card spec default when a card carries no explicit priority.
DEFAULT_PRIORITY = "med"


def _tags_for(entity: TaskEntity) -> List[str]:
    """tier → a tag id in the Card ``tags`` array (omitted until a tier is assigned)."""
    if entity.tier is not None:
        return [f"tier:{entity.tier}"]
    return []


def to_card(entity: Optional[TaskEntity]) -> Optional[Dict[str, object]]:
    """Project one entity to a Card dict, or ``None`` if it is soft-deleted (and
    thus omitted from board output)."""
    if entity is None or entity.deleted_at is not None:
        return None

    return {
        # ── native Card fields (kanbantt-mcp-spec §Card) ──────────────────────
        "id": entity.id,
        "title": entity.title,
        "description": "",
        "column_id": lifecycle_to_column(entity.lifecycle_state),
        "order": entity.order,
        "tags": _tags_for(entity),
        "checklist": [],
        "due": None,
        "priority": DEFAULT_PRIORITY,
        "effort": None,
        "impact": None,
        "version": entity.version,
        "deleted_at": None,  # soft-deleted entities are omitted, never projected
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
        "created_by": actor_ref(entity.created_by),
        "updated_by": actor_ref(entity.updated_by),
        "attachments": [],
        # ── Claunker extensions (preserved by the unknown-field round-trip rule) ─
        "gate_status": GATE_STATUS_COMMITTED,
        "badge": "escalated" if entity.escalated else None,
    }


def project(entities: Iterable[TaskEntity]) -> List[Dict[str, object]]:
    """Project a set of entities to the Card list, omitting soft-deleted ones and
    sorting by (order, id) — the spec's stable tiebreak on an order collision."""
    cards = [card for card in (to_card(e) for e in entities) if card is not None]
    cards.sort(key=lambda c: (c["order"], c["id"]))
    return cards
