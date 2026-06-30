"""The four ``card_*`` operator-write tools — the FREE / ungoverned write path.

Drives each tool through the SDK's in-memory client (same harness as
test_escalation_resolve) against a file-backed spine, and asserts the ratified
stance:

  * card_create persists a new Task and returns its projected Card; an empty title
    and a bad tier are validation_failed; an unknown project is not_found.
  * card_update edits ONLY the provided mutable fields (single put), leaving the
    others untouched; no fields → validation_failed; an unknown id → not_found.
  * card_move is FREE — it moves across NON-adjacent states with no transition check
    and applies an optional LexoRank order; a bad target state → validation_failed.
  * card_delete is a SOFT delete: the card disappears from card_list/the board but
    the ROW + its data persist on disk (auditable, recoverable); unknown id →
    not_found.

Async calls go through ``anyio.run`` inside sync tests, so no async plugin is needed.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import Spine, Store  # noqa: E402
from spine.entity import State  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import build_server  # noqa: E402
from tests.spine_server._util import cleanup, make_temp_db  # noqa: E402


def _config(path, **overrides):
    return ServerConfig(token="test-token", db_path=path, enable_dns_rebinding_protection=False, **overrides)


def _seed_task(path, *, title="t", state=State.CREATED, tier=None):
    """Seed project + one task; return (project_id, task_id)."""
    spine = Spine(Store(path))
    try:
        proj = spine.create_project("p")
        task = spine.create_task(proj.id, title, state=state, tier=tier)
        return proj.id, task.id
    finally:
        spine.store.close()


async def _call(server, name, arguments):
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool(name, arguments)
        return result.isError, result.structuredContent


async def _cards(server):
    """The current card_list snapshot (live cards only)."""
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool("card_list", {})
        return result.structuredContent["cards"]


def _task_on_disk(path, task_id):
    """Read a task straight from a freshly-opened store (proves a committed write)."""
    with Store(path) as store:
        return store.tasks.get(task_id)


# ── card_create ─────────────────────────────────────────────────────────────────
def test_card_create_persists_and_returns_projected_card():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)  # gives us a real project to create into
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"project_id": project_id, "title": "new card", "state": "tiered", "tier": 3},
        )
        assert is_error is False
        card = sc["card"]
        # The projected Card: state→column, tier→tag, gate_status COMMITTED, no badge.
        assert card["title"] == "new card"
        assert card["column_id"] == "tiered"
        assert card["tags"] == ["tier:3"]
        assert card["gate_status"] == "COMMITTED"
        assert card["badge"] is None
        # Committed to disk (acceptance_criteria is stored though not in the Card lens).
        stored = _task_on_disk(path, card["id"])
        assert stored is not None and stored.state == "tiered" and stored.tier == 3
    finally:
        cleanup(directory)


def test_card_create_defaults_state_created_tier_one_empty_criteria():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"project_id": project_id, "title": "defaulted"},
        )
        assert is_error is False
        assert sc["card"]["column_id"] == "created"   # default state
        assert sc["card"]["tags"] == ["tier:1"]        # default tier
        assert _task_on_disk(path, sc["card"]["id"]).acceptance_criteria == ""  # default ""
    finally:
        cleanup(directory)


def test_card_create_empty_title_is_validation_failed():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"project_id": project_id, "title": "   "},  # whitespace-only is empty
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


def test_card_create_bad_tier_is_validation_failed():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"project_id": project_id, "title": "t", "tier": 7},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


def test_card_create_unknown_project_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path)  # a real project exists, but we target a ghost id
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"project_id": "ghost", "title": "orphan"},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
    finally:
        cleanup(directory)


# ── card_update ─────────────────────────────────────────────────────────────────
def test_card_update_changes_only_provided_fields():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="before", tier=1)
        # Update ONLY title + acceptance_criteria; tier omitted → unchanged.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"task_id": task_id, "title": "after", "acceptance_criteria": "must compile"},
        )
        assert is_error is False
        assert sc["card"]["title"] == "after"
        assert sc["card"]["tags"] == ["tier:1"]            # tier left untouched
        stored = _task_on_disk(path, task_id)
        assert stored.title == "after" and stored.tier == 1
        assert stored.acceptance_criteria == "must compile"
    finally:
        cleanup(directory)


def test_card_update_no_fields_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update", {"task_id": task_id},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


def test_card_update_bad_tier_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update", {"task_id": task_id, "tier": 0},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, task_id).tier == 2  # no partial write
    finally:
        cleanup(directory)


def test_card_update_unknown_id_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update", {"task_id": "ghost", "title": "x"},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
    finally:
        cleanup(directory)


def test_card_update_empty_title_is_validation_failed_no_partial_write():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="before", tier=2)
        # An explicit empty / whitespace-only title is rejected on update just as on
        # create — and the co-submitted valid tier must NOT be written (atomic reject).
        for blank in ("", "   "):
            is_error, sc = anyio.run(
                _call, build_server(_config(path)), "card_update",
                {"task_id": task_id, "title": blank, "tier": 4},
            )
            assert is_error is True
            assert sc["code"] == "validation_failed"
            stored = _task_on_disk(path, task_id)
            assert stored.title == "before"   # title untouched on disk
            assert stored.tier == 2           # no partial write of the valid field
    finally:
        cleanup(directory)


# ── card_move ─────────────────────────────────────────────────────────────────
def test_card_move_is_free_across_non_adjacent_states_and_applies_order():
    directory, path = make_temp_db()
    try:
        # Seed in 'created'; jump straight to 'delivered' (skipping tiered/dispatched/
        # judged) — a NON-adjacent move that the free path must allow.
        _, task_id = _seed_task(path, state=State.CREATED)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"task_id": task_id, "to_state": "delivered", "order": "zzz"},
        )
        assert is_error is False
        assert sc["card"]["column_id"] == "delivered"   # the column moved, no gating
        assert sc["card"]["order"] == "zzz"             # the LexoRank order was applied
        assert _task_on_disk(path, task_id).state == "delivered"
    finally:
        cleanup(directory)


def test_card_move_without_order_keeps_position():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        before = _task_on_disk(path, task_id).order
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"task_id": task_id, "to_state": "failed"},  # order omitted
        )
        assert is_error is False
        assert sc["card"]["order"] == before            # position unchanged
        assert sc["card"]["column_id"] == "failed"
    finally:
        cleanup(directory)


def test_card_move_bad_state_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, state=State.CREATED)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"task_id": task_id, "to_state": "escalated"},  # not one of the six states
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, task_id).state == "created"  # unchanged
    finally:
        cleanup(directory)


def test_card_move_unknown_id_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"task_id": "ghost", "to_state": "tiered"},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
    finally:
        cleanup(directory)


# ── card_delete (SOFT delete) ─────────────────────────────────────────────────
def test_card_delete_hides_from_board_but_retains_the_row_on_disk():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="doomed")
        # Present on the board before deletion.
        assert task_id in {c["id"] for c in anyio.run(_cards, build_server(_config(path)))}

        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_delete", {"task_id": task_id},
        )
        assert is_error is False
        assert sc["id"] == task_id

        # Gone from the board / card_list snapshot…
        assert task_id not in {c["id"] for c in anyio.run(_cards, build_server(_config(path)))}
        # …but the ROW + its data PERSIST on disk as a tombstone (auditable/recoverable).
        stored = _task_on_disk(path, task_id)
        assert stored is not None
        assert stored.title == "doomed"            # data retained, not scrubbed
        assert stored.deleted_at is not None       # tombstoned, not hard-deleted
    finally:
        cleanup(directory)


def test_card_delete_unknown_id_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_delete", {"task_id": "ghost"},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
    finally:
        cleanup(directory)
