"""``card_retier`` — the GOVERNED, audited tier-change tool (spec v0.3.0 §Re-tier) —
plus the matching ``card_update`` WRITE-ONCE tier guard.

Drives both through the SDK's in-memory client (same harness as test_card_write)
against a file-backed spine, and asserts the locked governance contract:

  * card_retier changes an ALREADY-SET tier to a different valid tier and writes ONE
    append-only ``tier_audit`` row, in a single transaction. ``reduces_control`` is
    1 on a downgrade (a LOWER new tier weakens oversight) and 0 on an upgrade.
  * every rejection branch — untiered card, tier out of range, no-op same-tier,
    empty/whitespace reason — is validation_failed and writes NO audit row.
  * the concurrency contract — a stale ``expected_version`` is a `conflict` carrying
    the fresh card; there is NO ``force`` (a re-tier cannot bypass the check); a
    tombstoned card is an immutable `conflict` — and NONE of these write an audit row.
  * card_update's WRITE-ONCE guard refuses to change a SET tier (→ validation_failed,
    no mutation, no audit row), while the FREE untiered → N initial classification
    still succeeds. The suite FAILS if a set-tier change ever slips through card_update.

Async calls go through ``anyio.run`` inside sync tests, so no async plugin is needed.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import RETIER_ACTOR, Spine, Store  # noqa: E402
from spine.entity import State  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import build_server  # noqa: E402
from tests.spine_server._util import cleanup, make_temp_db  # noqa: E402

# A token that never matches a real one (real tokens are "{seq}:{hash}" with seq>=1).
STALE = "0:stale"


def _config(path, **overrides):
    return ServerConfig(token="test-token", db_path=path, enable_dns_rebinding_protection=False, **overrides)


def _seed_task(path, *, title="t", state=State.CREATED, tier=None):
    """Seed project + one task at a given tier; return (project_id, task_id)."""
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


def _task_on_disk(path, task_id):
    with Store(path) as store:
        return store.tasks.get(task_id)


def _version_of(path, task_id):
    return _task_on_disk(path, task_id).version


def _audit_rows(path):
    """The append-only tier_audit ledger, read fresh from disk (insert order)."""
    with Store(path) as store:
        return store.list_tier_audit()


def _tombstone(path, task_id):
    spine = Spine(Store(path))
    try:
        spine.soft_delete_task(task_id)
    finally:
        spine.store.close()


# ── happy path: tier change + one audit row, reduces_control by direction ─────────
def test_card_retier_downgrade_records_reduces_control_true_audit_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=4)
        ev = _version_of(path, task_id)
        # 4 → 2 is a DOWNGRADE: a lower tier = weaker oversight, so reduces_control.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_retier",
            {"id": task_id, "new_tier": "tier:2", "expected_version": ev,
             "reason": "tighten review after a near-miss"},
        )
        assert is_error is False
        # The tier tag was rewritten; the projection re-emits it.
        assert sc["card"]["tags"] == ["tier:2"]
        assert _task_on_disk(path, task_id).tier == 2

        rows = _audit_rows(path)
        assert len(rows) == 1                       # exactly one ledger row
        row = rows[0]
        assert row["card_id"] == task_id
        assert row["old_tier"] == 4 and row["new_tier"] == 2
        assert row["reduces_control"] == 1          # downgrade weakens control (int 0/1)
        assert row["actor"] == RETIER_ACTOR         # the authenticated-client placeholder
        assert row["reason"] == "tighten review after a near-miss"
        assert isinstance(row["ts"], str) and "T" in row["ts"]   # ISO-8601 UTC stamp
    finally:
        cleanup(directory)


def test_card_retier_upgrade_records_reduces_control_false_audit_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        ev = _version_of(path, task_id)
        # 2 → 4 is an UPGRADE: a higher tier = stronger oversight, so NOT reduces_control.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_retier",
            {"id": task_id, "new_tier": "tier:4", "expected_version": ev,
             "reason": "promote to human sign-off"},
        )
        assert is_error is False
        assert sc["card"]["tags"] == ["tier:4"]
        assert _task_on_disk(path, task_id).tier == 4
        row = _audit_rows(path)[0]
        assert (row["old_tier"], row["new_tier"]) == (2, 4)
        assert row["reduces_control"] == 0          # upgrade strengthens control
    finally:
        cleanup(directory)


# ── rejection branches (each: validation_failed, NO mutation, NO audit row) ───────
def test_card_retier_untiered_card_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=None)   # never classified
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_retier",
            {"id": task_id, "new_tier": "tier:3", "expected_version": ev, "reason": "x"},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "untiered" in sc["message"]          # RE-TIER ONLY: set the initial tier via card_update
        assert _task_on_disk(path, task_id).tier is None
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)


def test_card_retier_out_of_range_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_retier",
            {"id": task_id, "new_tier": "tier:9", "expected_version": ev, "reason": "bump it"},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, task_id).tier == 2
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)


def test_card_retier_same_tier_is_no_op_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=3)
        ev = _version_of(path, task_id)
        # new_tier == current tier: a no-op is rejected and writes NO audit row.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_retier",
            {"id": task_id, "new_tier": "tier:3", "expected_version": ev, "reason": "no change really"},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "equals current tier" in sc["message"]
        assert _audit_rows(path) == []              # no no-op rows in the ledger
    finally:
        cleanup(directory)


def test_card_retier_empty_or_whitespace_reason_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        ev = _version_of(path, task_id)
        for blank in ("", "   "):                   # empty AND whitespace-only (after trim)
            is_error, sc = anyio.run(
                _call, build_server(_config(path)), "card_retier",
                {"id": task_id, "new_tier": "tier:4", "expected_version": ev, "reason": blank},
            )
            assert is_error is True
            assert sc["code"] == "validation_failed"
            assert "reason" in sc["message"]
            assert _task_on_disk(path, task_id).tier == 2   # no mutation
            assert _audit_rows(path) == []                  # no audit row
    finally:
        cleanup(directory)


def test_card_retier_unknown_id_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path, tier=2)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_retier",
            {"id": "ghost", "new_tier": "tier:3", "expected_version": STALE, "reason": "n/a"},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)


# ── concurrency: stale version is a conflict; NO force; tombstone is immutable ─────
def test_card_retier_version_mismatch_is_conflict_and_force_is_not_accepted():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        # A stale expected_version → conflict carrying the fresh card. card_retier has NO
        # force parameter, so a re-tier can NEVER bypass the optimistic-concurrency check
        # — it must re-fetch and re-decide.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_retier",
            {"id": task_id, "new_tier": "tier:4", "expected_version": STALE, "reason": "racing write"},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        current = sc["meta"]["current"]
        assert current["id"] == task_id
        assert current["tags"] == ["tier:2"]        # unchanged ground truth
        assert current["deleted_at"] is None        # still live
        assert _task_on_disk(path, task_id).tier == 2   # no write happened
        assert _audit_rows(path) == []                  # and no audit row
    finally:
        cleanup(directory)


def test_card_retier_on_tombstone_is_conflict_no_audit_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        _tombstone(path, task_id)
        # Even with the CORRECT current version, a tombstone is immutable (the gate fires
        # before the re-tier invariants) — and writes NO audit row.
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_retier",
            {"id": task_id, "new_tier": "tier:4", "expected_version": ev, "reason": "resurrect?"},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        assert sc["meta"]["current"]["deleted_at"] is not None   # the tombstone rides in meta.current
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)


# ── card_update WRITE-ONCE tier guard (the lock card_retier is the only way past) ─
def test_card_update_set_tier_change_is_write_once_no_mutation_no_audit_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)       # already classified
        ev = _version_of(path, task_id)
        # Changing a SET tier via the FREE update path is refused server-side — it must
        # go through the governed, audited card_retier. (If this ever slips through, the
        # write-once lock is broken: the test FAILS.)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"tier": "tier:4"}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "write-once" in sc["message"]
        assert _task_on_disk(path, task_id).tier == 2   # tier NOT mutated
        assert _audit_rows(path) == []                  # update never writes the ledger
    finally:
        cleanup(directory)


def test_card_update_untiered_to_tier_initial_classification_still_succeeds():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=None)    # untiered
        ev = _version_of(path, task_id)
        # Untiered → N is the FREE initial classification — the write-once guard does NOT
        # apply, and no audit row is written (only card_retier audits).
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"tier": "tier:3"}, "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["tags"] == ["tier:3"]
        assert _task_on_disk(path, task_id).tier == 3
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)


def test_card_update_same_tier_patch_is_allowed_not_write_once():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="keep", tier=2)
        ev = _version_of(path, task_id)
        # A patch carrying the SAME tier (alongside another field) is unaffected by the
        # guard — write-once blocks a CHANGE, not a restatement.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "kept", "tier": "tier:2"}, "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["title"] == "kept"
        assert sc["card"]["tags"] == ["tier:2"]
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)
