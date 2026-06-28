"""MCP surface (via the SDK's in-memory client): exactly two tools advertised,
serverInfo identity, structuredContent as top-level objects, and the domain-error
shape. Async calls are driven through ``anyio.run`` inside sync test functions so no
async test plugin is needed.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine.entity import STATES  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import SERVER_NAME, SERVER_VERSION, build_server  # noqa: E402
from tests.spine_server._util import cleanup, make_temp_db, seed  # noqa: E402


def _config(path, **overrides):
    return ServerConfig(token="test-token", db_path=path, enable_dns_rebinding_protection=False, **overrides)


async def _tool_names(server):
    return [t.name for t in await server.list_tools()]


async def _server_info(server):
    async with connect(server) as client:
        return (await client.initialize()).serverInfo


async def _call(server, name, arguments):
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool(name, arguments)
        return result.isError, result.structuredContent


def test_tools_list_advertises_exactly_board_get_and_card_list():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        names = anyio.run(_tool_names, build_server(_config(path)))
        # exactly the read-only pair — no write/escalation/artifact/column/tag tools.
        assert sorted(names) == ["board_get", "card_list"]
    finally:
        cleanup(directory)


def test_serverinfo_name_and_version():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        info = anyio.run(_server_info, build_server(_config(path)))
        assert info.name == SERVER_NAME == "Claunker"
        assert info.version == SERVER_VERSION
    finally:
        cleanup(directory)


def test_board_get_structuredcontent_is_top_level_object():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        is_error, sc = anyio.run(_call, build_server(_config(path)), "board_get", {})
        assert is_error is False
        assert isinstance(sc, dict)  # top-level OBJECT, never a bare array
        assert sc["kanbantt_schema_version"] == 1
        assert {c["id"] for c in sc["board"]["columns"]} == set(STATES)
    finally:
        cleanup(directory)


def test_card_list_structuredcontent_is_top_level_object():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        is_error, sc = anyio.run(_call, build_server(_config(path)), "card_list", {})
        assert is_error is False
        assert isinstance(sc, dict)
        assert isinstance(sc["cards"], list) and isinstance(sc["sync_token"], str)
        assert sc["cards"][0]["gate_status"] == "COMMITTED"
    finally:
        cleanup(directory)


def test_payload_too_large_rides_as_domain_error_not_a_transport_failure():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        # a 1-byte ceiling forces the only realistic domain error for read tools.
        is_error, sc = anyio.run(_call, build_server(_config(path, max_bytes=1)), "card_list", {})
        assert is_error is True
        assert sc["code"] == "payload_too_large"
        assert isinstance(sc["message"], str) and sc["message"]
        assert isinstance(sc["meta"], dict)
    finally:
        cleanup(directory)
