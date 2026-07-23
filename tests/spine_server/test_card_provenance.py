"""Dispatch PROVENANCE on the card-write wire — model + effort + job_id carried
INSIDE ``created_by``, the receiving half of the provenance feature (spec v0.7.0).

The bridge-side change (emitting provenance when it mints a card) is a deliberate
FOLLOW-UP on the claude-async side; this suite proves the SPINE half in isolation:

  * card_create SPLITS created_by by trust: IDENTITY (type/id) stays authority-owned
    (always the operator credential, anti-spoof), while DISPATCH PROVENANCE
    (model/effort/job_id + any unknown foreign keys) is descriptive metadata the
    minting client owns — it is READ from the payload and MERGED onto the credential
    identity. No provenance in → no provenance stored (human intake).
  * created_by is WRITE-ONCE: card_update rejects it on ANY presence with an EXPLICIT
    validation_failed (never a silent drop), and the stored stamp is untouched.
  * The provenance survives to the projected Card, so the board can render its chip.

Same in-memory-client harness as test_card_write.
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


def _config(path, **overrides):
    return ServerConfig(token="test-token", db_path=path, enable_dns_rebinding_protection=False, **overrides)


def _seed_project(path):
    spine = Spine(Store(path))
    try:
        return spine.create_project("p").id
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


# ── card_create: provenance merged onto the credential identity ───────────────
def test_card_create_merges_provenance_onto_credential_identity():
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {
                "title": "dispatched",
                # A client CANNOT spoof identity: type/id here are ignored and
                # re-stamped from the credential. The provenance sub-keys ARE kept.
                "created_by": {"type": "agent", "id": "impostor",
                               "model": "claude-sonnet-5", "effort": "medium",
                               "job_id": "job-7"},
            }, "project_id": project_id},
        )
        assert is_error is False
        stored = _task_on_disk(path, sc["card"]["id"])
        # Identity is the operator credential (anti-spoof); provenance rides alongside.
        assert stored.created_by == {
            "type": "human", "id": "operator",
            "model": "claude-sonnet-5", "effort": "medium", "job_id": "job-7",
        }
        # And it reaches the projected Card so the board can render the chip.
        assert sc["card"]["created_by"]["model"] == "claude-sonnet-5"
        assert sc["card"]["created_by"]["effort"] == "medium"
    finally:
        cleanup(directory)


def test_card_create_absent_created_by_carries_no_provenance():
    """Human intake: no created_by in → exactly the operator identity, no provenance
    keys (the board shows no chip for a human-minted card)."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "hand-made"}, "project_id": project_id},
        )
        assert is_error is False
        stored = _task_on_disk(path, sc["card"]["id"])
        assert stored.created_by == {"type": "human", "id": "operator"}
        assert "model" not in stored.created_by and "effort" not in stored.created_by
    finally:
        cleanup(directory)


def test_card_create_identity_only_created_by_carries_no_provenance():
    """A payload created_by with ONLY type/id (both stripped as authority-owned) leaves
    no provenance — identical to sending none. Guards the existing anti-spoof test."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": {"type": "agent", "id": "impostor"}},
             "project_id": project_id},
        )
        assert is_error is False
        assert _task_on_disk(path, sc["card"]["id"]).created_by == {"type": "human", "id": "operator"}
    finally:
        cleanup(directory)


def test_card_create_unknown_provenance_keys_pass_through():
    """Interop: unknown non-identity keys from a foreign caller are tolerated and
    persisted (additive-only), never rejected."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": {"model": "m", "vendor_trace": "t"}},
             "project_id": project_id},
        )
        assert is_error is False
        stored = _task_on_disk(path, sc["card"]["id"])
        assert stored.created_by == {"type": "human", "id": "operator", "model": "m", "vendor_trace": "t"}
    finally:
        cleanup(directory)


def test_card_create_non_string_provenance_is_validation_failed():
    """Wire hygiene: a non-string model/effort/job_id is rejected by the Task
    constructor → validation_failed (not a 500)."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": {"model": 123}}, "project_id": project_id},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


# ── card_update: created_by is write-once (red-on-violation) ──────────────────
def test_card_update_rejects_created_by_patch_explicitly():
    """WRITE-ONCE: any created_by in a patch → validation_failed (an EXPLICIT error,
    never a silent drop — that is the description bug we do not duplicate)."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        _, created = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": {"model": "m1", "job_id": "j1"}},
             "project_id": project_id},
        )
        cid = created["card"]["id"]
        before = _task_on_disk(path, cid).created_by
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"created_by": {"type": "agent", "id": "x", "model": "tampered"}},
             "expected_version": _version_of(path, cid)},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "created_by" in sc["message"] and "write-once" in sc["message"]
        # Immutability: the stored stamp is untouched by the rejected patch.
        assert _task_on_disk(path, cid).created_by == before
    finally:
        cleanup(directory)
