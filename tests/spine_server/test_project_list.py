"""``project_list`` — the project-targeting read ``card_create`` rides on.

Drives the tool through the SDK's in-memory client (the test_card_write harness)
against a file-backed spine and asserts the read stance:

  * the envelope is ``{ projects: [{id, name, created_at}] }`` — a top-level object,
    minimal fields (no version token: projects are inert at v1, nothing to echo back);
  * LIVE projects only — a soft-deleted project is not a create target and is omitted
    (the same liveness rule jobcard's resolver applies);
  * deterministic order: sorted (created_at, id), oldest first;
  * an empty spine serves ``{ projects: [] }`` (a valid, empty enumeration — the
    client's picker gates create, it does not error);
  * strictly read-only — a call mutates nothing (no version movement anywhere).

Async calls go through ``anyio.run`` inside sync tests, so no async plugin is needed.
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


async def _call(server, name, arguments):
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool(name, arguments)
        return result.isError, result.structuredContent


def _seed_projects(path, names, tombstone=()):
    """Create projects in order; soft-delete the ones named in ``tombstone``.
    Returns name → id."""
    spine = Spine(Store(path))
    try:
        ids = {}
        for name in names:
            project = spine.create_project(name)
            ids[name] = project.id
            if name in tombstone:
                project.deleted_at = "2026-01-01T00:00:00+00:00"
                spine.store.projects.put(project)
        return ids
    finally:
        spine.store.close()


def test_project_list_serves_live_projects_with_minimal_fields():
    directory, path = make_temp_db()
    try:
        ids = _seed_projects(path, ["Claunker First Light", "Dispatch Log"])
        is_error, sc = anyio.run(_call, build_server(_config(path)), "project_list", {})
        assert is_error is False
        assert isinstance(sc, dict)  # top-level OBJECT, never a bare array
        projects = sc["projects"]
        assert [p["name"] for p in projects] == ["Claunker First Light", "Dispatch Log"]
        assert [p["id"] for p in projects] == [ids["Claunker First Light"], ids["Dispatch Log"]]
        for p in projects:
            # exactly the enumeration a create target needs — nothing to echo back.
            assert sorted(p.keys()) == ["created_at", "id", "name"]
            assert isinstance(p["created_at"], str) and p["created_at"]
    finally:
        cleanup(directory)


def test_project_list_omits_tombstoned_projects():
    directory, path = make_temp_db()
    try:
        _seed_projects(path, ["alive", "retired"], tombstone=("retired",))
        is_error, sc = anyio.run(_call, build_server(_config(path)), "project_list", {})
        assert is_error is False
        assert [p["name"] for p in sc["projects"]] == ["alive"]
    finally:
        cleanup(directory)


def test_project_list_on_an_empty_spine_is_an_empty_enumeration():
    directory, path = make_temp_db()
    try:
        is_error, sc = anyio.run(_call, build_server(_config(path)), "project_list", {})
        assert is_error is False
        assert sc["projects"] == []
    finally:
        cleanup(directory)


def test_project_list_is_read_only():
    directory, path = make_temp_db()
    try:
        ids = _seed_projects(path, ["p"])
        with Store(path) as store:
            before = store.projects.get(ids["p"]).version
        anyio.run(_call, build_server(_config(path)), "project_list", {})
        with Store(path) as store:
            assert store.projects.get(ids["p"]).version == before  # no version movement
    finally:
        cleanup(directory)
