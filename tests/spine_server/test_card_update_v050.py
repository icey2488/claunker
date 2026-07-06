"""Tests for spine v0.5.0 card_update enhancements (amendment 2026-07-06):

  * RFC 7386 key-presence patch semantics — clearable {due, effort, impact}
  * Task.due — set/clear/projection passthrough
  * Task.depends_on — set/replace/clear/self-ref/type validation
  * Guarded fields {tier, archived_at, deleted_at} present-null → validation_failed
  * Edit-audit ledger — one row per changed field, atomicity, correct old/new
  * Legacy blob load — tasks without due/depends_on project null/[]

Async calls go through anyio.run inside sync tests (no async plugin needed).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import Spine, Store  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import build_server  # noqa: E402
from tests.spine_server._util import cleanup, make_temp_db  # noqa: E402

STALE = "0:stale"
ISO_DATE = "2026-12-31"
ISO_DT = "2026-12-31T00:00:00"


def _config(path, **overrides):
    return ServerConfig(token="test-token", db_path=path, enable_dns_rebinding_protection=False, **overrides)


def _seed_task(path, *, title="t", tier=None):
    spine = Spine(Store(path))
    try:
        proj = spine.create_project("p")
        task = spine.create_task(proj.id, title, tier=tier)
        return proj.id, task.id
    finally:
        spine.store.close()


def _seed_two_tasks(path):
    """Seed project + two tasks; return (project_id, task_id_1, task_id_2)."""
    spine = Spine(Store(path))
    try:
        proj = spine.create_project("p")
        t1 = spine.create_task(proj.id, "t1")
        t2 = spine.create_task(proj.id, "t2")
        return proj.id, t1.id, t2.id
    finally:
        spine.store.close()


async def _call(server, name, arguments):
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool(name, arguments)
        return result.isError, result.structuredContent


async def _cards(server):
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool("card_list", {})
        return result.structuredContent["cards"]


def _task_on_disk(path, task_id):
    with Store(path) as store:
        return store.tasks.get(task_id)


def _version_of(path, task_id):
    return _task_on_disk(path, task_id).version


def _edit_audit_rows(path):
    with Store(path) as store:
        return store.list_edit_audit()


# ── clearable fields: due ──────────────────────────────────────────────────────

def test_due_set_and_projected():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"due": ISO_DATE}, "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["due"] == ISO_DATE
        assert _task_on_disk(path, task_id).due == ISO_DATE
    finally:
        cleanup(directory)


def test_due_null_clears_and_writes_ledger_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        # Set due first.
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"due": ISO_DT}, "expected_version": ev},
        )
        ev2 = _version_of(path, task_id)
        # Clear due with explicit null.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"due": None}, "expected_version": ev2},
        )
        assert is_error is False
        assert sc["card"]["due"] is None
        assert _task_on_disk(path, task_id).due is None
        # Ledger: two rows — set then clear.
        rows = _edit_audit_rows(path)
        assert len(rows) == 2
        set_row = rows[0]
        assert set_row["field"] == "due"
        assert set_row["old"] is None
        assert set_row["new"] == ISO_DT
        clear_row = rows[1]
        assert clear_row["field"] == "due"
        assert clear_row["old"] == ISO_DT
        assert clear_row["new"] is None
    finally:
        cleanup(directory)


def test_due_omission_leaves_unchanged():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"due": ISO_DATE}, "expected_version": ev},
        )
        ev2 = _version_of(path, task_id)
        # Patch only title — due omitted entirely.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "new"}, "expected_version": ev2},
        )
        assert is_error is False
        assert sc["card"]["due"] == ISO_DATE   # unchanged
        assert _task_on_disk(path, task_id).due == ISO_DATE
    finally:
        cleanup(directory)


def test_due_invalid_iso_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"due": "not-a-date"}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, task_id).due is None
    finally:
        cleanup(directory)


# ── clearable fields: effort ───────────────────────────────────────────────────

def test_effort_null_clears_and_writes_ledger_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"effort": "high"}, "expected_version": ev},
        )
        ev2 = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"effort": None}, "expected_version": ev2},
        )
        assert is_error is False
        assert sc["card"]["effort"] is None
        assert _task_on_disk(path, task_id).effort is None
        rows = _edit_audit_rows(path)
        clear_row = rows[-1]
        assert clear_row["field"] == "effort"
        assert clear_row["old"] == "high"
        assert clear_row["new"] is None
    finally:
        cleanup(directory)


def test_effort_omission_leaves_existing_value():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"effort": "low"}, "expected_version": ev},
        )
        ev2 = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "x"}, "expected_version": ev2},
        )
        assert is_error is False
        assert sc["card"]["effort"] == "low"   # key absent → unchanged
    finally:
        cleanup(directory)


# ── clearable fields: impact ───────────────────────────────────────────────────

def test_impact_null_clears_and_writes_ledger_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"impact": "med"}, "expected_version": ev},
        )
        ev2 = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"impact": None}, "expected_version": ev2},
        )
        assert is_error is False
        assert sc["card"]["impact"] is None
        assert _task_on_disk(path, task_id).impact is None
        rows = _edit_audit_rows(path)
        clear_row = rows[-1]
        assert clear_row["field"] == "impact"
        assert clear_row["old"] == "med"
        assert clear_row["new"] is None
    finally:
        cleanup(directory)


# ── guarded fields: present-null → validation_failed, no ledger row ────────────

def test_guarded_tier_null_is_validation_failed_no_ledger_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"tier": None}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, task_id).tier == 2  # tier unchanged
        assert _edit_audit_rows(path) == []            # no ledger row
    finally:
        cleanup(directory)


def test_guarded_archived_at_null_is_validation_failed_no_ledger_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"archived_at": None}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "card_archive" in sc["message"] or "card_archive" in str(sc)
        assert _edit_audit_rows(path) == []
    finally:
        cleanup(directory)


def test_guarded_deleted_at_null_is_validation_failed_no_ledger_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"deleted_at": None}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _edit_audit_rows(path) == []
    finally:
        cleanup(directory)


# ── depends_on: set / replace / clear / validations ───────────────────────────

def test_depends_on_set_and_projected():
    directory, path = make_temp_db()
    try:
        _, task_id_1, task_id_2 = _seed_two_tasks(path)
        ev = _version_of(path, task_id_1)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id_1, "patch": {"depends_on": [task_id_2]}, "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["depends_on"] == [task_id_2]
        assert _task_on_disk(path, task_id_1).depends_on == [task_id_2]
        rows = _edit_audit_rows(path)
        assert len(rows) == 1
        assert rows[0]["field"] == "depends_on"
        assert rows[0]["old"] == []
        assert rows[0]["new"] == [task_id_2]
    finally:
        cleanup(directory)


def test_depends_on_replace():
    directory, path = make_temp_db()
    try:
        _, task_id_1, task_id_2 = _seed_two_tasks(path)
        # Create a third task for replacement.
        spine = Spine(Store(path))
        try:
            proj = spine.get_project(None)  # unused, but we need project_id
        except Exception:
            pass
        spine.store.close()
        with Store(path) as store:
            s = Spine(store)
            # Get an existing project_id from stored tasks.
            t = store.tasks.get(task_id_1)
            t3 = s.create_task(t.project_id, "t3")
            task_id_3 = t3.id

        ev = _version_of(path, task_id_1)
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id_1, "patch": {"depends_on": [task_id_2]}, "expected_version": ev},
        )
        ev2 = _version_of(path, task_id_1)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id_1, "patch": {"depends_on": [task_id_3]}, "expected_version": ev2},
        )
        assert is_error is False
        assert sc["card"]["depends_on"] == [task_id_3]
        rows = _edit_audit_rows(path)
        # Two rows: set [t2] then replace with [t3].
        assert len(rows) == 2
        assert rows[1]["old"] == [task_id_2]
        assert rows[1]["new"] == [task_id_3]
    finally:
        cleanup(directory)


def test_depends_on_empty_list_clears():
    directory, path = make_temp_db()
    try:
        _, task_id_1, task_id_2 = _seed_two_tasks(path)
        ev = _version_of(path, task_id_1)
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id_1, "patch": {"depends_on": [task_id_2]}, "expected_version": ev},
        )
        ev2 = _version_of(path, task_id_1)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id_1, "patch": {"depends_on": []}, "expected_version": ev2},
        )
        assert is_error is False
        assert sc["card"]["depends_on"] == []
        assert _task_on_disk(path, task_id_1).depends_on == []
        rows = _edit_audit_rows(path)
        clear_row = rows[-1]
        assert clear_row["field"] == "depends_on"
        assert clear_row["old"] == [task_id_2]
        assert clear_row["new"] == []
    finally:
        cleanup(directory)


def test_depends_on_null_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"depends_on": None}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _edit_audit_rows(path) == []
    finally:
        cleanup(directory)


def test_depends_on_non_string_entry_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"depends_on": [123]}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


def test_depends_on_self_reference_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"depends_on": [task_id]}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, task_id).depends_on == []  # no write
        assert _edit_audit_rows(path) == []                   # no ledger row
    finally:
        cleanup(directory)


# ── edit-audit ledger: atomicity ──────────────────────────────────────────────

def test_atomicity_rejected_guarded_null_writes_no_rows():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=1)
        ev = _version_of(path, task_id)
        # Patch includes a guarded-null (tier) — rejected at handler, before update_task.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"tier": None, "due": ISO_DATE}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _edit_audit_rows(path) == []          # no row despite due being present
        assert _task_on_disk(path, task_id).due is None  # no partial write
    finally:
        cleanup(directory)


def test_atomicity_self_ref_writes_no_rows():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        # Self-ref is rejected inside update_task after the concurrency gate —
        # no audit rows must be committed.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"depends_on": [task_id]}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _edit_audit_rows(path) == []
    finally:
        cleanup(directory)


# ── legacy blob load: tasks without due/depends_on project null/[] ─────────────

def test_legacy_task_without_due_projects_null():
    """A task created before v0.5.0 (blob has no 'due' key) loads as due=null."""
    directory, path = make_temp_db()
    try:
        # Simulate legacy blob: write a task dict without 'due' or 'depends_on'.
        import json, uuid as _uuid
        with Store(path) as store:
            task_id = str(_uuid.uuid4())
            proj_id = str(_uuid.uuid4())
            blob = {
                "id": task_id, "project_id": proj_id, "title": "legacy",
                "state": "created", "tier": None, "acceptance_criteria": None,
                "effort": None, "impact": None, "order": "a",
                "created_at": "2026-01-01T00:00:00+00:00",
                "version": None, "deleted_at": None, "archived_at": None,
                "created_by": None,
                # 'due' and 'depends_on' intentionally absent
            }
            store._conn.execute(
                "INSERT OR REPLACE INTO tasks (id, data) VALUES (?, ?)",
                (task_id, json.dumps(blob)),
            )
            store._conn.commit()
            task = store.tasks.get(task_id)
            assert task.due is None
            assert task.depends_on == []
        # Projection passthrough: due=null, depends_on=[]
        cards = anyio.run(_cards, build_server(_config(path)))
        # card_list only shows live tasks (need project to exist; skip via direct projection)
        from spine.projection import to_card
        from spine import Store as S
        with S(path) as store:
            t = store.tasks.get(task_id)
            card = to_card(t)
        assert card["due"] is None
        assert card["depends_on"] == []
    finally:
        cleanup(directory)


# ── projection passthrough ──────────────────────────────────────────────────────

def test_projection_passthrough_due_and_depends_on():
    directory, path = make_temp_db()
    try:
        _, task_id_1, task_id_2 = _seed_two_tasks(path)
        ev = _version_of(path, task_id_1)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id_1,
             "patch": {"due": ISO_DT, "depends_on": [task_id_2]},
             "expected_version": ev},
        )
        assert is_error is False
        card = sc["card"]
        assert card["due"] == ISO_DT
        assert card["depends_on"] == [task_id_2]
        # Confirm via a fresh card_list round-trip.
        cards = anyio.run(_cards, build_server(_config(path)))
        fresh = next(c for c in cards if c["id"] == task_id_1)
        assert fresh["due"] == ISO_DT
        assert fresh["depends_on"] == [task_id_2]
    finally:
        cleanup(directory)


def test_new_task_defaults_due_null_depends_on_empty():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        cards = anyio.run(_cards, build_server(_config(path)))
        card = next(c for c in cards if c["id"] == task_id)
        assert card["due"] is None
        assert card["depends_on"] == []
    finally:
        cleanup(directory)
