"""board_get: six columns DERIVED from the Task State enum + declared tier tags.

The columns must be derived from ``spine.entity.STATES`` (not a hand-kept parallel
list) and their ids must equal the enum values, because Kanbantt renders columns
verbatim with no fallback tray — a card whose column matches none of the six would
vanish. These tests pin that lockstep, the schema versions, and the tier-tag format.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from spine.entity import STATES, Task  # noqa: E402
from spine.projection import _tags_for  # noqa: E402  (pin the tag format to the projection)
from spine_server.board import (  # noqa: E402
    BOARD_SCHEMA_VERSION,
    KANBANTT_SCHEMA_VERSION,
    TIERS,
    build_board,
    build_columns,
    build_tags,
    tier_tag_id,
)

ACCENT_PALETTE = {"textDim", "frost", "ice", "amber", "mint", "coral"}


def test_board_get_shape_and_schema_versions():
    board = build_board()
    assert set(board) == {"board", "kanbantt_schema_version"}
    assert board["kanbantt_schema_version"] == KANBANTT_SCHEMA_VERSION == 1
    inner = board["board"]
    assert set(inner) == {"schema_version", "columns", "tags"}
    assert inner["schema_version"] == BOARD_SCHEMA_VERSION == 1


def test_columns_derived_from_state_enum_ids_equal_enum_values():
    cols = build_columns()
    assert len(cols) == len(STATES) == 6
    # ids are EXACTLY the enum values — total over the enum, no extras (no vanish).
    assert {c["id"] for c in cols} == set(STATES)
    # each column is well-formed; name is derived from the id (not a parallel list).
    for c in cols:
        assert set(c) == {"id", "name", "color", "order"}
        assert c["name"] == c["id"].capitalize()
        assert isinstance(c["order"], str) and c["order"]


def test_columns_sorted_by_order_read_left_to_right_in_pipeline_order():
    cols = build_columns()
    assert cols == sorted(cols, key=lambda c: c["order"])  # already sorted by order
    # …and that order is the pipeline (enum) order, left-to-right.
    assert [c["id"] for c in cols] == list(STATES)


def test_pinned_column_colors_from_accent_palette():
    color = {c["id"]: c["color"] for c in build_columns()}
    # the three mappings the brief pins explicitly:
    assert color["judged"] == "amber"
    assert color["delivered"] == "mint"
    assert color["failed"] == "coral"
    # all six colors come from the accent palette and are distinct.
    assert set(color.values()) <= ACCENT_PALETTE
    assert len(set(color.values())) == 6


def test_tier_tags_declared_for_every_tier():
    tags = build_tags()
    assert [t["id"] for t in tags] == [tier_tag_id(t) for t in TIERS]
    assert [t["id"] for t in tags] == ["tier:1", "tier:2", "tier:3", "tier:4"]
    for t in tags:
        assert set(t) == {"id", "name", "color"}
        assert t["color"] in ACCENT_PALETTE
    assert build_board()["board"]["tags"] == tags


def test_tier_tag_format_matches_projection():
    # Consistency: the board's declared tag id equals what spine/projection emits for
    # a Task carrying that tier. Both derive from the same f"tier:{N}" format.
    for tier in TIERS:
        emitted = _tags_for(Task(id="x", project_id="p", title="t", tier=tier))
        assert emitted == [tier_tag_id(tier)]
