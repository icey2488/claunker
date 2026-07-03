"""``card_archive`` / ``card_unarchive`` — the GOVERNED, audited archive pair (spec
v0.4.0 §Archive) — plus the ``archive_audit`` ledger invariants and the
``include_archived`` list filter.

Drives the tools through the SDK's in-memory client (same harness as
test_card_retier) against a file-backed spine, and asserts the locked contract:

  * archive sets ``archived_at`` (orthogonal to state), unarchive clears it, and each
    writes ONE append-only ``archive_audit`` row atomically with the flag change
    (a forced failure between the staged row and the put leaves NEITHER).
  * LOUD idempotency — archiving an already-archived card and unarchiving a
    non-archived card are validation_failed rejections, never silent no-ops, and
    write NO audit row.
  * the concurrency contract — a stale ``expected_version`` is a `conflict` carrying
    the fresh card in meta.current; there is NO ``force``; a tombstoned card is an
    immutable `conflict` (the gate fires BEFORE the archive invariants) — and NONE
    of these write an audit row.
  * the ESCALATION GATE — an open (live, unresolved) escalation blocks card_archive;
    resolving it unblocks; card_unarchive is ungated.
  * the two-layer reason contract — an OMITTED reason defaults to
    "manual_archive"/"manual_unarchive" in the ledger; an EXPLICIT empty/whitespace
    reason is REJECTED by the ledger (validation_failed at the tool, ValueError at
    ``append_archive_audit`` directly), so 100% of ledger rows are reasoned.
  * the version token moves on archive and AGAIN on unarchive (``archived_at`` rides
    ``content()``), and ``archived_at`` round-trips through ``to_card``.
  * ``include_archived`` on card_list — archived cards omitted by default, included
    on request, COMPOSING with ``include_deleted`` (deleted+archived needs both).

Async calls go through ``anyio.run`` inside sync tests, so no async plugin is needed.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import ARCHIVE_ACTOR, Spine, Store  # noqa: E402
from spine.entity import State  # noqa: E402
from spine.projection import to_card  # noqa: E402
from spine_server.cards import list_cards  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import build_server  # noqa: E402
from tests.spine_server._util import BIG, assert_raises, cleanup, make_temp_db  # noqa: E402

# A token that never matches a real one (real tokens are "{seq}:{hash}" with seq>=1).
STALE = "0:stale"


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


def _task_on_disk(path, task_id):
    with Store(path) as store:
        return store.tasks.get(task_id)


def _version_of(path, task_id):
    return _task_on_disk(path, task_id).version


def _audit_rows(path):
    """The append-only archive_audit ledger, read fresh from disk (insert order)."""
    with Store(path) as store:
        return store.list_archive_audit()


def _tombstone(path, task_id):
    spine = Spine(Store(path))
    try:
        spine.soft_delete_task(task_id)
    finally:
        spine.store.close()


def _archive(path, task_id, reason="setup archive"):
    """Archive directly via the facade (test setup, not the path under test)."""
    spine = Spine(Store(path))
    try:
        spine.archive_task(task_id, reason=reason)
    finally:
        spine.store.close()


def _escalate(path, task_id, reason="needs a human look"):
    """Raise an escalation on the task; return the escalation id."""
    spine = Spine(Store(path))
    try:
        return spine.create_escalation(task_id, reason).id
    finally:
        spine.store.close()


def _resolve(path, escalation_id, resolution="approve"):
    spine = Spine(Store(path))
    try:
        spine.resolve_escalation(
            escalation_id,
            resolution=resolution,
            resolution_rationale="reviewed and cleared for archive",
            actor="operator",
        )
    finally:
        spine.store.close()


# ── happy paths: flag set/cleared + one audit row each, full row shape ────────────
def test_card_archive_sets_flag_and_records_one_audit_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_archive",
            {"id": task_id, "expected_version": ev, "reason": "sprint closed out"},
        )
        assert is_error is False
        assert sc["card"]["archived_at"] is not None    # echoed on the returned card
        assert sc["card"]["deleted_at"] is None         # archived ≠ deleted
        on_disk = _task_on_disk(path, task_id)
        assert on_disk.archived_at is not None
        assert on_disk.state == State.CREATED           # ORTHOGONAL: the column never moved

        rows = _audit_rows(path)
        assert len(rows) == 1                           # exactly one ledger row
        row = rows[0]
        assert row["card_id"] == task_id
        assert row["action"] == "archive"
        assert row["actor"] == ARCHIVE_ACTOR            # the authenticated-client placeholder
        assert row["reason"] == "sprint closed out"
        assert isinstance(row["ts"], str) and "T" in row["ts"]   # ISO-8601 UTC stamp
        assert set(row) == {"id", "card_id", "action", "actor", "reason", "ts"}
    finally:
        cleanup(directory)


def test_card_unarchive_clears_flag_and_records_second_audit_row():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        _archive(path, task_id, reason="setup")
        ev = _version_of(path, task_id)                 # fresh token after the archive
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_unarchive",
            {"id": task_id, "expected_version": ev, "reason": "picked back up"},
        )
        assert is_error is False
        assert sc["card"]["archived_at"] is None
        assert _task_on_disk(path, task_id).archived_at is None

        rows = _audit_rows(path)
        assert len(rows) == 2                           # archive row + unarchive row
        assert [r["action"] for r in rows] == ["archive", "unarchive"]
        assert rows[1]["card_id"] == task_id
        assert rows[1]["actor"] == ARCHIVE_ACTOR
        assert rows[1]["reason"] == "picked back up"
        assert isinstance(rows[1]["ts"], str) and "T" in rows[1]["ts"]
    finally:
        cleanup(directory)


# ── atomicity: a failure between the staged row and the put leaves NOTHING ────────
def test_forced_put_failure_leaves_no_audit_row_and_no_state_change():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        spine = Spine(Store(path))
        try:
            # Force a failure BETWEEN append_archive_audit(commit=False) and the put:
            # the staged ledger insert must die with the transaction, never commit alone.
            def boom(entity):
                raise RuntimeError("forced put failure")
            spine.store.tasks.put = boom
            assert_raises(
                lambda: spine.archive_task(task_id, reason="doomed"), RuntimeError
            )
        finally:
            spine.store.close()                         # close without commit → rollback
        assert _audit_rows(path) == []                  # the staged row never landed
        assert _task_on_disk(path, task_id).archived_at is None   # no state change
    finally:
        cleanup(directory)


# ── loud idempotency: healthy and broken must not emit the same signal ────────────
def test_card_archive_on_already_archived_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        _archive(path, task_id)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_archive",
            {"id": task_id, "expected_version": ev, "reason": "again"},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "already archived" in sc["message"]
        assert len(_audit_rows(path)) == 1              # only the setup archive's row
    finally:
        cleanup(directory)


def test_card_unarchive_on_non_archived_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)                   # never archived
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_unarchive",
            {"id": task_id, "expected_version": ev, "reason": "restore"},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "not archived" in sc["message"]
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)


def test_unknown_id_is_not_found_for_both_tools():
    directory, path = make_temp_db()
    try:
        _seed_task(path)
        for tool in ("card_archive", "card_unarchive"):
            is_error, sc = anyio.run(
                _call, build_server(_config(path)), tool,
                {"id": "ghost", "expected_version": STALE, "reason": "n/a"},
            )
            assert is_error is True
            assert sc["code"] == "not_found"
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)


# ── concurrency: gate BEFORE invariants; stale is conflict; tombstone immutable ───
def test_stale_expected_version_is_conflict_carrying_meta_current():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_archive",
            {"id": task_id, "expected_version": STALE, "reason": "racing write"},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        current = sc["meta"]["current"]
        assert current["id"] == task_id
        assert current["archived_at"] is None           # unchanged ground truth
        assert current["deleted_at"] is None            # still live
        assert _task_on_disk(path, task_id).archived_at is None   # no write happened
        assert _audit_rows(path) == []                            # and no audit row
    finally:
        cleanup(directory)


def test_tombstone_is_conflict_not_validation_gate_fires_before_invariants():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        _tombstone(path, task_id)
        ev = _version_of(path, task_id)
        # Even with the CORRECT current version — and even though a tombstone is also
        # "not archived" (which would be validation_failed on unarchive) — the
        # tombstone gate fires FIRST: both tools answer conflict, never validation.
        for tool in ("card_archive", "card_unarchive"):
            is_error, sc = anyio.run(
                _call, build_server(_config(path)), tool,
                {"id": task_id, "expected_version": ev, "reason": "poke tombstone"},
            )
            assert is_error is True
            assert sc["code"] == "conflict"
            assert sc["meta"]["current"]["deleted_at"] is not None   # the tombstone rides along
        assert _audit_rows(path) == []
    finally:
        cleanup(directory)


def test_conflict_meta_current_of_an_archived_card_carries_archived_at():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        _archive(path, task_id)
        # A stale write against an ARCHIVED card: the conflict envelope's meta.current
        # goes through the live projection (archived ≠ tombstoned), so the client's
        # reconcile sees the archive flag as part of the fresh ground truth.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_unarchive",
            {"id": task_id, "expected_version": STALE, "reason": "stale restore"},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        assert sc["meta"]["current"]["archived_at"] is not None
        assert sc["meta"]["current"]["deleted_at"] is None
        assert len(_audit_rows(path)) == 1              # only the setup archive's row
    finally:
        cleanup(directory)


# ── the escalation gate: open blocks archive; resolved unblocks; unarchive ungated ─
def test_open_escalation_blocks_archive_and_resolving_unblocks():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        escalation_id = _escalate(path, task_id)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_archive",
            {"id": task_id, "expected_version": ev, "reason": "tidy up"},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "unresolved escalation" in sc["message"]
        assert _task_on_disk(path, task_id).archived_at is None
        assert _audit_rows(path) == []

        # Resolving the escalation clears the gate. Resolution writes only the
        # ESCALATION entity, so the task's version token is untouched — the original
        # expected_version is still current and the retry succeeds with it.
        _resolve(path, escalation_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_archive",
            {"id": task_id, "expected_version": ev, "reason": "tidy up"},
        )
        assert is_error is False
        assert sc["card"]["archived_at"] is not None
        assert len(_audit_rows(path)) == 1
    finally:
        cleanup(directory)


def test_deny_resolution_also_unblocks_archive():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        escalation_id = _escalate(path, task_id)
        # The gate predicate is RESOLVED-ness (resolved_at set), not the decision: a
        # DENIED escalation is no longer awaiting attention, so archive is unblocked.
        _resolve(path, escalation_id, resolution="deny")
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_archive",
            {"id": task_id, "expected_version": ev, "reason": "denied and shelved"},
        )
        assert is_error is False
        assert sc["card"]["archived_at"] is not None
        assert sc["card"]["badge"]["status"] == "denied"   # the receipt still rides
    finally:
        cleanup(directory)


def test_open_escalation_does_not_block_unarchive():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        _archive(path, task_id)
        # An escalation raised AFTER the archive (an archived task is live — MI-1
        # only rejects tombstones). The gate applies to archive ONLY: restoring a
        # card to view never buries anything, so unarchive proceeds.
        _escalate(path, task_id)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_unarchive",
            {"id": task_id, "expected_version": ev, "reason": "surface it"},
        )
        assert is_error is False
        assert sc["card"]["archived_at"] is None
        assert sc["card"]["badge"]["status"] == "unresolved"   # the badge still rides
        assert [r["action"] for r in _audit_rows(path)] == ["archive", "unarchive"]
    finally:
        cleanup(directory)


# ── the two-layer reason contract ──────────────────────────────────────────────────
def test_omitted_reason_defaults_in_the_ledger():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        server = build_server(_config(path))
        ev = _version_of(path, task_id)
        is_error, _ = anyio.run(
            _call, server, "card_archive", {"id": task_id, "expected_version": ev}
        )
        assert is_error is False
        ev = _version_of(path, task_id)
        is_error, _ = anyio.run(
            _call, server, "card_unarchive", {"id": task_id, "expected_version": ev}
        )
        assert is_error is False
        # The tool layer injected the deterministic defaults; the ledger stays 100%
        # reasoned with zero operator friction.
        assert [r["reason"] for r in _audit_rows(path)] == ["manual_archive", "manual_unarchive"]
    finally:
        cleanup(directory)


def test_explicit_empty_or_whitespace_reason_is_validation_failed_not_defaulted():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        for blank in ("", "   "):
            # EXPLICIT garbage is loud (the ledger's hard invariant), never silently
            # rewritten to the default — only omission is ergonomic.
            is_error, sc = anyio.run(
                _call, build_server(_config(path)), "card_archive",
                {"id": task_id, "expected_version": ev, "reason": blank},
            )
            assert is_error is True
            assert sc["code"] == "validation_failed"
            assert "reason" in sc["message"]
            assert _task_on_disk(path, task_id).archived_at is None   # no state change
            assert _audit_rows(path) == []                            # no audit row
    finally:
        cleanup(directory)


def test_append_archive_audit_rejects_unreasoned_rows_at_the_ledger_layer():
    directory, path = make_temp_db()
    try:
        with Store(path) as store:
            for bad in ("", "   ", None):
                row = {"id": "r1", "card_id": "c1", "action": "archive",
                       "actor": ARCHIVE_ACTOR, "reason": bad, "ts": "2026-07-02T00:00:00+00:00"}
                assert_raises(lambda: store.append_archive_audit(row), ValueError)
            assert store.list_archive_audit() == []     # nothing ever staged
    finally:
        cleanup(directory)


# ── versioning + projection round-trip ─────────────────────────────────────────────
def test_version_token_moves_on_archive_and_again_on_unarchive():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        v0 = _version_of(path, task_id)
        server = build_server(_config(path))
        anyio.run(_call, server, "card_archive",
                  {"id": task_id, "expected_version": v0, "reason": "shelve"})
        v1 = _version_of(path, task_id)
        assert v1 != v0                                 # archive is a real mutation
        anyio.run(_call, server, "card_unarchive",
                  {"id": task_id, "expected_version": v1, "reason": "unshelve"})
        v2 = _version_of(path, task_id)
        assert v2 != v1 and v2 != v0                    # and so is unarchive
        # The mechanism: archived_at is a dataclass field, so it rides content() —
        # the slice hashed into the token (recon confirmation, pinned here).
        assert "archived_at" in _task_on_disk(path, task_id).content()
    finally:
        cleanup(directory)


def test_archived_at_round_trips_through_to_card():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        assert to_card(_task_on_disk(path, task_id))["archived_at"] is None
        _archive(path, task_id)
        task = _task_on_disk(path, task_id)
        assert to_card(task)["archived_at"] == task.archived_at   # echoed verbatim
    finally:
        cleanup(directory)


def test_projection_lens_itself_never_omits_archived():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        _archive(path, task_id)
        # The archived-card filter lives in list_cards (the card_list view), NOT in the
        # lens: project()/Spine.cards() still emits an archived task (unlike a
        # tombstone, which the lens omits). This is what lets the write tools and the
        # conflict envelope return archived cards at all.
        spine = Spine(Store(path))
        try:
            cards = spine.cards()
        finally:
            spine.store.close()
        assert [c["id"] for c in cards] == [task_id]
        assert cards[0]["archived_at"] is not None
    finally:
        cleanup(directory)


# ── include_archived filtering on card_list ────────────────────────────────────────
def _seed_visibility_matrix(path):
    """Four tasks: active / archived / deleted / deleted+archived. Returns their ids."""
    spine = Spine(Store(path))
    try:
        proj = spine.create_project("p")
        active = spine.create_task(proj.id, "active").id
        archived = spine.create_task(proj.id, "archived").id
        deleted = spine.create_task(proj.id, "deleted").id
        both = spine.create_task(proj.id, "both").id
        spine.archive_task(archived, reason="matrix setup")
        spine.soft_delete_task(deleted)
        spine.archive_task(both, reason="matrix setup")   # archive FIRST (tombstones are immutable)
        spine.soft_delete_task(both)
        return active, archived, deleted, both
    finally:
        spine.store.close()


def test_include_archived_default_excludes_true_includes_and_composes_with_include_deleted():
    directory, path = make_temp_db()
    try:
        active, archived, deleted, both = _seed_visibility_matrix(path)

        ids = lambda **kw: {c["id"] for c in list_cards(path, max_bytes=BIG, **kw)["cards"]}
        # default: the working view — no archived, no deleted.
        assert ids() == {active}
        # include_archived alone: archived surfaces; anything deleted stays hidden.
        assert ids(include_archived=True) == {active, archived}
        # include_deleted alone: tombstones surface; anything archived stays hidden
        # (the deleted+archived card needs BOTH flags — one is not enough).
        assert ids(include_deleted=True) == {active, deleted}
        # both flags: the full matrix, deleted+archived included.
        assert ids(include_deleted=True, include_archived=True) == {active, archived, deleted, both}

        # the deleted+archived card carries BOTH marks when it does appear.
        full = {c["id"]: c for c in list_cards(
            path, include_deleted=True, include_archived=True, max_bytes=BIG)["cards"]}
        assert full[both]["deleted_at"] is not None
        assert full[both]["archived_at"] is not None
    finally:
        cleanup(directory)


def test_card_list_tool_accepts_include_archived():
    directory, path = make_temp_db()
    try:
        active, archived, _, _ = _seed_visibility_matrix(path)
        server = build_server(_config(path))
        is_error, sc = anyio.run(_call, server, "card_list", {})
        assert is_error is False
        assert {c["id"] for c in sc["cards"]} == {active}
        is_error, sc = anyio.run(_call, server, "card_list", {"include_archived": True})
        assert is_error is False
        assert {c["id"] for c in sc["cards"]} == {active, archived}
        # the archived card rides with its flag intact for the client to render.
        by_id = {c["id"]: c for c in sc["cards"]}
        assert by_id[archived]["archived_at"] is not None
    finally:
        cleanup(directory)
