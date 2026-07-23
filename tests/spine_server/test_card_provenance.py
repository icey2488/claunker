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
from spine.entity import (  # noqa: E402
    MAX_CREATED_BY_BYTES,
    MAX_PROVENANCE_KEYS,
    MAX_PROVENANCE_VALUE_LEN,
)
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


# ── FINDING 2: created_by admission caps (red-on-violation) ───────────────────
# Unknown-key tolerance + write-once immutability = a payload the spine accepts and can
# never clean up, so the WRITE boundary must bound it. Over-limit → validation_failed
# naming the specific limit; fail closed (whole create rejected, never truncated).
def test_card_create_nested_provenance_value_under_depth_is_accepted():
    """INTEROP PROMISE (the test the string-only over-correction broke): a foreign server
    sending a STRUCTURED value under an unknown key — e.g. a nested vendor_trace object —
    must NOT have its whole card_create hard-rejected. The nested value round-trips
    verbatim and reaches the projected Card. Bounded by the depth + byte caps, not a
    flat-value rule."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": {
                "model": "m", "vendor_trace": {"span": "abc", "duration": 12}}},
             "project_id": project_id},
        )
        assert is_error is False
        stored = _task_on_disk(path, sc["card"]["id"])
        assert stored.created_by == {
            "type": "human", "id": "operator",
            "model": "m", "vendor_trace": {"span": "abc", "duration": 12},
        }
        assert sc["card"]["created_by"]["vendor_trace"] == {"span": "abc", "duration": 12}
    finally:
        cleanup(directory)


def test_card_create_over_depth_provenance_is_rejected():
    """The relaxation keeps a depth cap: a deeply-recursive foreign value is a parser-bomb
    surface and is rejected → validation_failed naming the depth limit. depth 4 is one past
    the depth-3 cap (created_by → vt → a → b)."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": {"vt": {"a": {"b": {"c": 1}}}}},
             "project_id": project_id},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "depth" in sc["message"]
    finally:
        cleanup(directory)


def test_card_create_too_many_provenance_keys_is_rejected():
    """Over the key-count cap → validation_failed naming the limit."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        too_many = {f"k{i}": "v" for i in range(MAX_PROVENANCE_KEYS + 1)}
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": too_many}, "project_id": project_id},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "too many provenance keys" in sc["message"]
    finally:
        cleanup(directory)


def test_card_create_oversized_provenance_value_is_rejected():
    """A single value over the per-value length cap → validation_failed naming the key."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": {"model": "m" * (MAX_PROVENANCE_VALUE_LEN + 1)}},
             "project_id": project_id},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "too long" in sc["message"] and "model" in sc["message"]
    finally:
        cleanup(directory)


def test_card_create_oversized_total_created_by_is_rejected():
    """Under the key-count AND per-value caps but over the total-bytes backstop →
    validation_failed. 10 keys × 500-char values isolate the serialized-size cap."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        bulky = {f"k{i}": "v" * 500 for i in range(10)}  # 10 ≤ 12 keys, 500 ≤ 512 each
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": bulky}, "project_id": project_id},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "serialized size exceeds cap" in sc["message"]
        # Sanity: this payload genuinely exceeds the byte cap while obeying the other two.
        assert 10 <= MAX_PROVENANCE_KEYS and 500 <= MAX_PROVENANCE_VALUE_LEN
        assert 10 * 500 > MAX_CREATED_BY_BYTES
    finally:
        cleanup(directory)


def test_card_create_normal_provenance_passes_under_caps():
    """A realistic agent provenance payload sits comfortably under every cap and is
    stored — the caps bound abuse, not legitimate mints."""
    directory, path = make_temp_db()
    try:
        project_id = _seed_project(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "x", "created_by": {
                "type": "agent", "id": "impostor",
                "model": "claude-sonnet-5", "effort": "high", "job_id": "job-abc-123",
            }}, "project_id": project_id},
        )
        assert is_error is False
        stored = _task_on_disk(path, sc["card"]["id"])
        assert stored.created_by == {
            "type": "human", "id": "operator",
            "model": "claude-sonnet-5", "effort": "high", "job_id": "job-abc-123",
        }
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
