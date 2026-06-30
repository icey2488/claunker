"""``escalation_resolve`` — the spine's FIRST mutating MCP tool (security-sensitive).

Drives the tool through the SDK's in-memory client (same harness as
test_mcp_surface) against a file-backed spine seeded with a live escalation, and
asserts:
  * the happy path persists the operator decision (resolution + rationale + actor)
    and flips the card_list badge — cleared on approve, a 'denied' receipt on deny;
  * the recorded actor is 'operator', DERIVED FROM THE SERVER and never a client
    field (the tool exposes no actor parameter — the operator-only invariant for a
    forged/non-operator actor is proven at the Spine layer in the data-core suite);
  * the write is COMMITTED (visible from a freshly-opened store);
  * the domain-error mapping: unknown id → not_found; bad resolution / a rationale
    under the >=10-char floor → validation_failed (and no partial write).

Async calls go through ``anyio.run`` inside sync tests, so no async plugin is needed.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import Spine, Store  # noqa: E402
from spine.projection import project  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import build_server  # noqa: E402
from tests.spine_server._util import cleanup, make_temp_db  # noqa: E402

_CONTROL_DIFF = {"control_id": "net.egress", "old_value": "deny", "new_value": "allow", "reduces_control": True}


def _config(path, **overrides):
    return ServerConfig(token="test-token", db_path=path, enable_dns_rebinding_protection=False, **overrides)


def _seed_escalation(path, *, reason="needs human", control_diff=None):
    """Seed project + task + one live (unresolved) escalation; return (task_id, esc_id)."""
    spine = Spine(Store(path))
    try:
        proj = spine.create_project("p")
        task = spine.create_task(proj.id, "t")
        esc = spine.create_escalation(task.id, reason, control_diff=control_diff)
        return task.id, esc.id
    finally:
        spine.store.close()


async def _call(server, name, arguments):
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool(name, arguments)
        return result.isError, result.structuredContent


def _badge(path, task_id):
    """The current card_list badge for ``task_id``, read from a freshly-opened store."""
    with Store(path) as store:
        cards = project(store.tasks.list_all(), store.escalations.list_all())
    return next(c["badge"] for c in cards if c["id"] == task_id)


# ── happy path: the decision is persisted and the badge reflects it ─────────────
def test_resolve_approve_clears_badge_and_records_operator_actor():
    directory, path = make_temp_db()
    try:
        task_id, esc_id = _seed_escalation(path, control_diff=_CONTROL_DIFF)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "escalation_resolve",
            {"id": esc_id, "resolution": "approve", "resolution_rationale": "approved after review"},
        )
        assert is_error is False
        # The returned escalation records the decision + the SERVER-derived actor.
        assert sc["escalation"]["resolution"] == "approve"
        assert sc["escalation"]["actor"] == "operator"
        assert sc["escalation"]["resolved_at"] is not None
        # approve → the card's badge clears (the change is committed).
        assert _badge(path, task_id) is None
    finally:
        cleanup(directory)


def test_resolve_deny_flips_badge_to_a_denied_receipt():
    directory, path = make_temp_db()
    try:
        task_id, esc_id = _seed_escalation(path, reason="weakens egress", control_diff=_CONTROL_DIFF)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "escalation_resolve",
            {"id": esc_id, "resolution": "deny", "resolution_rationale": "rejecting: ghost-worker overreach"},
        )
        assert is_error is False
        assert sc["escalation"]["resolution"] == "deny"
        badge = _badge(path, task_id)
        assert badge["status"] == "denied"
        assert badge["resolution_rationale"] == "rejecting: ghost-worker overreach"
        assert badge["id"] == esc_id
    finally:
        cleanup(directory)


def test_resolution_is_committed_to_the_writable_store():
    directory, path = make_temp_db()
    try:
        _, esc_id = _seed_escalation(path)
        is_error, _ = anyio.run(
            _call, build_server(_config(path)), "escalation_resolve",
            {"id": esc_id, "resolution": "deny", "resolution_rationale": "rejecting on review"},
        )
        assert is_error is False
        # Re-open the store from disk: the mutation was COMMITTED (a writable store),
        # not stranded in a transient connection.
        with Store(path) as store:
            esc = store.escalations.get(esc_id)
        assert esc.resolution == "deny" and esc.actor == "operator" and esc.resolved_at is not None
    finally:
        cleanup(directory)


# ── domain-error mapping (no partial writes) ────────────────────────────────────
def test_unknown_id_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_escalation(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "escalation_resolve",
            {"id": "ghost", "resolution": "approve", "resolution_rationale": "approved after review"},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
    finally:
        cleanup(directory)


def test_bad_resolution_is_validation_failed():
    directory, path = make_temp_db()
    try:
        task_id, esc_id = _seed_escalation(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "escalation_resolve",
            {"id": esc_id, "resolution": "maybe", "resolution_rationale": "a long enough rationale"},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _badge(path, task_id)["status"] == "unresolved"  # no partial write
    finally:
        cleanup(directory)


def test_subfloor_rationale_is_validation_failed():
    directory, path = make_temp_db()
    try:
        task_id, esc_id = _seed_escalation(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "escalation_resolve",
            {"id": esc_id, "resolution": "approve", "resolution_rationale": "too short"},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _badge(path, task_id)["status"] == "unresolved"  # rejected → still unresolved
    finally:
        cleanup(directory)
