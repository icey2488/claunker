"""The narrative ``description`` body + PRESERVE-AND-ROUND-TRIP of unmodeled foreign
keys on the card-write wire (spec v0.8.0).

Two contract fixes proven here at the wire boundary (same in-memory-client harness as
test_card_provenance):

  * ``description`` — the spec-conformant, agent-agnostic Markdown BODY. It is now a real
    modeled field: it persists and round-trips on create, is MUTABLE on update (set,
    change, and clear-via-null), absent means null (never a coerced ""), and an over-limit
    body is rejected LOUDLY naming the char limit (never silently truncated). This ends the
    old "projection emits constant ''" silent drop.
  * UNMODELED FOREIGN KEYS — a key the spine has no first-class Card field for is PRESERVED
    into ``Task.metadata`` and echoed on read, never flattened away (spec §Schema
    Versioning: unknown fields round-trip). card_update merges foreign keys (RFC 7386 —
    null removes). Over-limit foreign payloads reject loudly. Governance/extension keys are
    NOT client-preservable (a client cannot smuggle gate_status through metadata).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import Spine, Store  # noqa: E402
from spine.entity import (  # noqa: E402
    MAX_DESCRIPTION_LEN,
    MAX_METADATA_BYTES,
    MAX_METADATA_KEYS,
    MAX_METADATA_VALUE_LEN,
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


def _create(path, card):
    return anyio.run(
        _call, build_server(_config(path)), "card_create",
        {"card": card, "project_id": _seed_project(path)},
    )


# ── description: create persistence + projection ──────────────────────────────
def test_card_create_description_persists_and_round_trips():
    directory, path = make_temp_db()
    try:
        body = "# Goal\n\nMake the widget **fast**.\n\n- step one\n- step two"
        is_error, sc = _create(path, {"title": "t", "description": body})
        assert is_error is False
        # Reaches the projected Card...
        assert sc["card"]["description"] == body
        # ...and is persisted on the Task entity.
        assert _task_on_disk(path, sc["card"]["id"]).description == body
    finally:
        cleanup(directory)


def test_card_create_absent_description_is_null_not_empty_string():
    """Omitted description → null (absent), NOT a coerced "". The absent/present
    distinction survives so a foreign client can tell "no body" from "empty body"."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t"})
        assert is_error is False
        assert sc["card"]["description"] is None
        assert _task_on_disk(path, sc["card"]["id"]).description is None
    finally:
        cleanup(directory)


def test_card_create_over_limit_description_rejects_loudly_naming_limit():
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "description": "x" * (MAX_DESCRIPTION_LEN + 1)})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert str(MAX_DESCRIPTION_LEN) in sc["message"] and "description" in sc["message"]
    finally:
        cleanup(directory)


def test_card_create_non_string_description_is_validation_failed():
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "description": {"not": "a string"}})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "description" in sc["message"]
    finally:
        cleanup(directory)


def test_card_create_max_length_description_is_accepted():
    """The cap bounds abuse, not a legitimately long body: exactly MAX is fine."""
    directory, path = make_temp_db()
    try:
        body = "y" * MAX_DESCRIPTION_LEN
        is_error, sc = _create(path, {"title": "t", "description": body})
        assert is_error is False
        assert _task_on_disk(path, sc["card"]["id"]).description == body
    finally:
        cleanup(directory)


# ── description: mutability on update (set / change / clear) ───────────────────
def test_card_update_description_is_mutable_and_clearable():
    directory, path = make_temp_db()
    try:
        _, created = _create(path, {"title": "t", "description": "first"})
        cid = created["card"]["id"]

        # CHANGE the body.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"description": "second"}, "expected_version": _version_of(path, cid)},
        )
        assert is_error is False
        assert sc["card"]["description"] == "second"
        assert _task_on_disk(path, cid).description == "second"

        # CLEAR the body (present-null → key-presence clear, like effort/impact/due).
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"description": None}, "expected_version": _version_of(path, cid)},
        )
        assert is_error is False
        assert sc["card"]["description"] is None
        assert _task_on_disk(path, cid).description is None
    finally:
        cleanup(directory)


def test_card_update_over_limit_description_rejects_and_leaves_body_untouched():
    directory, path = make_temp_db()
    try:
        _, created = _create(path, {"title": "t", "description": "keep me"})
        cid = created["card"]["id"]
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"description": "z" * (MAX_DESCRIPTION_LEN + 1)},
             "expected_version": _version_of(path, cid)},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, cid).description == "keep me"  # rejected write changed nothing
    finally:
        cleanup(directory)


# ── preserve-and-round-trip: unmodeled foreign keys ───────────────────────────
def test_card_create_unknown_key_survives_and_is_echoed():
    """The headline contract: a foreign key the spine does not model survives create and is
    echoed on read — never silently discarded (forward-compat with a newer client)."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "x_from_v0_9": {"nested": [1, 2]}})
        assert is_error is False
        assert sc["card"]["x_from_v0_9"] == {"nested": [1, 2]}
        assert _task_on_disk(path, sc["card"]["id"]).metadata == {"x_from_v0_9": {"nested": [1, 2]}}
    finally:
        cleanup(directory)


def test_card_update_merges_and_removes_foreign_keys():
    directory, path = make_temp_db()
    try:
        _, created = _create(path, {"title": "t", "foo": "1", "bar": "2"})
        cid = created["card"]["id"]
        # Merge a new foreign key + change one; a null REMOVES a key (RFC 7386).
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"foo": "1-updated", "baz": "3", "bar": None},
             "expected_version": _version_of(path, cid)},
        )
        assert is_error is False
        assert sc["card"]["foo"] == "1-updated" and sc["card"]["baz"] == "3"
        assert "bar" not in sc["card"]
        assert _task_on_disk(path, cid).metadata == {"foo": "1-updated", "baz": "3"}
    finally:
        cleanup(directory)


def test_card_create_over_limit_foreign_metadata_rejects_loudly():
    directory, path = make_temp_db()
    try:
        # Over the total-bytes backstop while under key-count/per-value caps.
        bulky = {f"k{i}": "v" * 500 for i in range(10)}
        is_error, sc = _create(path, {"title": "t", **bulky})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "metadata serialized size exceeds cap" in sc["message"]
        assert 10 <= MAX_METADATA_KEYS and 500 <= MAX_METADATA_VALUE_LEN
        assert 10 * 500 > MAX_METADATA_BYTES
    finally:
        cleanup(directory)


def test_card_create_too_many_foreign_keys_rejects_loudly():
    directory, path = make_temp_db()
    try:
        too_many = {f"k{i}": "v" for i in range(MAX_METADATA_KEYS + 1)}
        is_error, sc = _create(path, {"title": "t", **too_many})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "metadata carries too many keys" in sc["message"]
    finally:
        cleanup(directory)


def test_client_cannot_smuggle_extension_field_via_metadata():
    """A governance/extension key (gate_status, badge) is part of the known Card surface,
    so it is NOT preserved as foreign metadata: the projection re-emits the SERVER's value.
    A client cannot overwrite it — the metadata boundary excludes CARD_FIELD_KEYS."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "gate_status": "HACKED", "badge": {"x": 1}})
        assert is_error is False
        assert sc["card"]["gate_status"] == "COMMITTED"   # server value, not the client's
        assert sc["card"]["badge"] is None                # no escalation → server's null
        assert _task_on_disk(path, sc["card"]["id"]).metadata == {}  # neither was preserved
    finally:
        cleanup(directory)


def test_unmodeled_known_card_fields_keep_documented_defaults():
    """priority/checklist/attachments are KNOWN Card fields the spine does not model; a
    client value for them is projected as the documented default (a recorded divergence —
    SPEC-DIVERGENCES.md), NOT preserved as foreign metadata. This pins that boundary."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "priority": "high", "checklist": [{"text": "a"}]})
        assert is_error is False
        assert sc["card"]["priority"] == "med"   # documented default, not the client's "high"
        assert sc["card"]["checklist"] == []
        assert _task_on_disk(path, sc["card"]["id"]).metadata == {}
    finally:
        cleanup(directory)
