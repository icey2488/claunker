"""card_list: full snapshot, fresh sync_token, tombstones, projection totality,
trivial filters, and complete-or-payload_too_large (never truncates).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from spine.entity import STATES, State  # noqa: E402
from spine_server.board import build_columns  # noqa: E402
from spine_server.cards import PayloadTooLarge, list_cards  # noqa: E402
from tests.spine_server._util import (  # noqa: E402
    BIG,
    assert_raises,
    cleanup,
    make_temp_db,
    seed,
)


def test_shape_is_cards_and_fresh_sync_token():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}, {"title": "b"}])
        r1 = list_cards(path, max_bytes=BIG)
        assert set(r1) == {"cards", "sync_token"}
        assert isinstance(r1["sync_token"], str) and r1["sync_token"]
        assert len(r1["cards"]) == 2
        # a fresh token is minted on every successful list (even a full fetch).
        r2 = list_cards(path, max_bytes=BIG)
        assert r2["sync_token"] != r1["sync_token"]
    finally:
        cleanup(directory)


def test_full_snapshot_cards_are_committed_top_level_objects():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a", "state": State.DISPATCHED, "tier": 2}])
        card = list_cards(path, max_bytes=BIG)["cards"][0]
        assert isinstance(card, dict)
        assert card["title"] == "a"
        assert card["column_id"] == State.DISPATCHED
        assert card["tags"] == ["tier:2"]
        assert card["gate_status"] == "COMMITTED"  # Claunker extension preserved
        assert card["deleted_at"] is None
    finally:
        cleanup(directory)


def test_projection_totality_every_state_lands_in_its_own_column():
    directory, path = make_temp_db()
    try:
        # one task per state → one card per state, each in the matching column id.
        seed(path, [{"title": s, "state": s} for s in STATES])
        cards = list_cards(path, max_bytes=BIG)["cards"]
        assert len(cards) == len(STATES)
        column_by_title = {c["title"]: c["column_id"] for c in cards}
        for s in STATES:
            assert column_by_title[s] == s
        # every projected column_id is one of the six board columns — none vanish.
        board_column_ids = {c["id"] for c in build_columns()}
        assert all(c["column_id"] in board_column_ids for c in cards)
    finally:
        cleanup(directory)


def test_tombstones_omitted_by_default_included_with_flag():
    directory, path = make_temp_db()
    try:
        live_id, dead_id = seed(
            path,
            [
                {"title": "live", "state": State.DISPATCHED},
                {"title": "dead", "state": State.FAILED, "deleted": True},
            ],
        )
        default = list_cards(path, max_bytes=BIG)["cards"]
        assert {c["id"] for c in default} == {live_id}
        assert all(c["deleted_at"] is None for c in default)

        included = {c["id"]: c for c in list_cards(path, include_deleted=True, max_bytes=BIG)["cards"]}
        assert set(included) == {live_id, dead_id}
        tombstone = included[dead_id]
        assert tombstone["deleted_at"] is not None        # marked as a tombstone
        assert tombstone["column_id"] == State.FAILED       # still maps to a column
    finally:
        cleanup(directory)


def test_trivial_column_and_tag_filters():
    directory, path = make_temp_db()
    try:
        seed(
            path,
            [
                {"title": "x", "state": State.DISPATCHED, "tier": 2},
                {"title": "y", "state": State.JUDGED, "tier": 4},
            ],
        )
        by_column = list_cards(path, column_id=State.DISPATCHED, max_bytes=BIG)["cards"]
        assert [c["title"] for c in by_column] == ["x"]
        by_tag = list_cards(path, tag="tier:4", max_bytes=BIG)["cards"]
        assert [c["title"] for c in by_tag] == ["y"]
    finally:
        cleanup(directory)


def test_never_truncates_raises_payload_too_large():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        # a 1-byte ceiling can't fit even the empty wrapper → complete-or-error.
        assert_raises(lambda: list_cards(path, max_bytes=1), PayloadTooLarge)
    finally:
        cleanup(directory)


def test_empty_spine_returns_empty_snapshot_with_token():
    directory, path = make_temp_db()
    try:
        result = list_cards(path, max_bytes=BIG)  # db auto-created, no tasks
        assert result["cards"] == []
        assert result["sync_token"]
    finally:
        cleanup(directory)
