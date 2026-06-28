"""``board_get`` payload construction — columns DERIVED from the Task ``State`` enum.

CRITICAL no-vanish guarantee (verified against Kanbantt): Kanbantt renders these
columns verbatim and has NO fallback tray for an unmapped ``column_id`` — a card
whose column matches none of the six VANISHES silently. So the column ids MUST
exactly equal the ``State`` enum values, and the Task→Card projection (which sets
``column_id = task.state``) must be total over that enum. Deriving the columns
here straight from ``spine.entity.STATES`` — never a hand-maintained parallel list
— is what keeps the two in lockstep.

Tags declare the tier vocabulary so a card's ``tier:N`` tag resolves. Both the tag
ids here and the tier values they encode are derived from ``TIERS``; the id FORMAT
mirrors what ``spine/projection.py`` emits (``f"tier:{tier}"``) and a test pins
that equality so the two never drift.
"""

from __future__ import annotations

from typing import Any, Dict, List

from spine.entity import STATES, State
from spine.ordering import rebalance

# Data schema versions (kanbantt-mcp-spec §Board / §Discovery). Both are the data
# schema version 1 — deliberately separate from the MCP protocol revision.
BOARD_SCHEMA_VERSION = 1
KANBANTT_SCHEMA_VERSION = 1

# ── columns ───────────────────────────────────────────────────────────────────

# State → accent color (Kanbantt theme tokens). Keyed by the State enum value (a
# semantic map, NOT a positional parallel list) so it survives an enum reorder and
# pins the brief's three explicit examples: judged→amber, delivered→mint,
# failed→coral. Accent palette: textDim, frost, ice, amber, mint, coral.
COLOR_BY_STATE: Dict[str, str] = {
    State.CREATED: "textDim",
    State.TIERED: "frost",
    State.DISPATCHED: "ice",
    State.JUDGED: "amber",
    State.DELIVERED: "mint",
    State.FAILED: "coral",
}
# Fallback so an unforeseen future state still gets a (visible) column rather than
# vanishing — totality over the enum beats a pretty palette.
DEFAULT_COLUMN_COLOR = "textDim"

# LexoRank-style sortable order strings, one per state, in pipeline order. Derived
# via the spine's own ordering authority over STATES (which is already in pipeline
# order) so the columns read created → … → failed left-to-right.
_ORDER_BY_STATE: Dict[str, str] = dict(rebalance(list(STATES)))


def _column(state: str) -> Dict[str, Any]:
    return {
        "id": state,                       # == State enum value → lockstep with Card.column_id
        "name": state.capitalize(),        # "Created" … "Failed" (derived, never a parallel list)
        "color": COLOR_BY_STATE.get(state, DEFAULT_COLUMN_COLOR),
        "order": _ORDER_BY_STATE[state],
    }


def build_columns() -> List[Dict[str, Any]]:
    """One column per Task ``State``, derived from the enum and sorted by ``order``
    (pipeline order, left-to-right)."""
    return sorted((_column(s) for s in STATES), key=lambda c: c["order"])


# ── tier tags ─────────────────────────────────────────────────────────────────

# The classifier tier space: 1=self-accept, 2=single judge, 3=dual sign-off,
# 4=human. The spine stays decoupled from the classifier, so the values live here.
# Both the board tags and the tag-id format below derive from these values.
TIERS = (1, 2, 3, 4)
TIER_TAG_PREFIX = "tier:"
TIER_LABELS: Dict[int, str] = {
    1: "Tier 1 · self-accept",
    2: "Tier 2 · single judge",
    3: "Tier 3 · dual sign-off",
    4: "Tier 4 · human",
}
# Escalating severity across the accent palette: routine → human gate.
TIER_COLOR_BY_TIER: Dict[int, str] = {1: "mint", 2: "frost", 3: "amber", 4: "coral"}


def tier_tag_id(tier: int) -> str:
    """Tag id for a tier. MUST equal what ``spine.projection`` emits for a Task with
    that tier (``f"tier:{tier}"``) — pinned by ``test_tier_tag_format_matches_projection``."""
    return f"{TIER_TAG_PREFIX}{tier}"


def build_tags() -> List[Dict[str, Any]]:
    """Declare one tag per tier so cards' ``tier:N`` tags resolve on the board."""
    return [
        {"id": tier_tag_id(t), "name": TIER_LABELS[t], "color": TIER_COLOR_BY_TIER[t]}
        for t in TIERS
    ]


# ── board ─────────────────────────────────────────────────────────────────────


def build_board() -> Dict[str, Any]:
    """The ``board_get`` structuredContent: a Board (six enum-derived columns + tier
    tags) plus the data schema version. Input: none."""
    return {
        "board": {
            "schema_version": BOARD_SCHEMA_VERSION,
            "columns": build_columns(),
            "tags": build_tags(),
        },
        "kanbantt_schema_version": KANBANTT_SCHEMA_VERSION,
    }
