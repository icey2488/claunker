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


def _seed_projects(path, names, tombstone=(), created_ats=None):
    """Create projects in order; soft-delete the ones named in ``tombstone``.
    ``created_ats`` optionally pins a name → created_at map (else the real clock — which
    can collide within a tick, exactly the same-tick case the ordering contract addresses).
    Returns name → id."""
    spine = Spine(Store(path))
    try:
        ids = {}
        for name in names:
            kwargs = {"created_at": created_ats[name]} if created_ats and name in created_ats else {}
            project = spine.create_project(name, **kwargs)
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
        stamps = {"Claunker First Light": "2026-01-01T00:00:00+00:00",
                  "Dispatch Log": "2026-01-02T00:00:00+00:00"}
        ids = _seed_projects(path, list(stamps), created_ats=stamps)
        is_error, sc = anyio.run(_call, build_server(_config(path)), "project_list", {})
        assert is_error is False
        assert isinstance(sc, dict)  # top-level OBJECT, never a bare array
        projects = sc["projects"]
        # Oldest-first by the DATA-BOUND (created_at, id) total order — expected built from the
        # SAME key, never from insertion order (the API does not promise creation order).
        expected = sorted(
            [{"id": ids[n], "name": n, "created_at": ca} for n, ca in stamps.items()],
            key=lambda p: (p["created_at"], p["id"]),
        )
        assert [p["name"] for p in projects] == [p["name"] for p in expected]
        assert [p["id"] for p in projects] == [p["id"] for p in expected]
        for p in projects:
            # exactly the enumeration a create target needs — nothing to echo back.
            assert sorted(p.keys()) == ["created_at", "id", "name"]
            assert isinstance(p["created_at"], str) and p["created_at"]
    finally:
        cleanup(directory)


def test_project_list_same_tick_orders_by_id_a_data_bound_total_order():
    """FIX 2 (round 2): same-tick projects (IDENTICAL created_at) order by ``id`` — the
    data-bound tiebreak. This is NOT creation order: ``id`` is a random UUIDv4, and
    ``created_at`` is documented display-only (never an ordering primitive), so the API
    promises a TOTAL, REPRODUCIBLE order, NOT within-tick creation order. The expected order is
    built from the SAME ``(created_at, id)`` key the server sorts on — asserting the computable
    order, not insertion order. The PRIOR fix (created_at ALONE over an ``ORDER BY rowid`` scan)
    would instead echo insertion order here, disguising the nondeterminism this test pins."""
    directory, path = make_temp_db()
    try:
        tick = "2026-03-03T12:00:00+00:00"
        names = ["p1", "p2", "p3", "p4", "p5"]
        ids = _seed_projects(path, names, created_ats={n: tick for n in names})
        is_error, sc = anyio.run(_call, build_server(_config(path)), "project_list", {})
        assert is_error is False
        got = [p["id"] for p in sc["projects"]]
        # created_at is equal across all five → the (created_at, id) key reduces to pure id sort.
        assert got == sorted(ids.values())
        assert got == sorted(got)  # strictly ascending by id — the reproducible tiebreak
    finally:
        cleanup(directory)


def test_project_list_order_survives_dump_reload_round_trip():
    """FIX 2 (round 2): the property ``rowid`` could NOT give. Because the order is intrinsic
    to the row CONTENTS (``created_at, id``), it survives a ``dump`` → ``load`` into a FRESH
    store — even though ``rowid`` is reassigned on reload. Same-tick projects are included so the
    id tiebreak actually engages; if the order depended on the physical rowid scan (the prior
    fix's hidden assumption), reloading would be free to flip it and this assertion would fail."""
    directory, path = make_temp_db()
    try:
        same = "2026-05-01T00:00:00+00:00"
        _seed_projects(path, ["a", "b", "c", "d"], created_ats={
            "a": same, "b": same, "c": same, "d": "2026-05-02T00:00:00+00:00",
        })
        _, before = anyio.run(_call, build_server(_config(path)), "project_list", {})
        # the served order IS the data-bound order, recomputable from the rows themselves
        assert before["projects"] == sorted(
            before["projects"], key=lambda p: (p["created_at"], p["id"])
        )
        # Dump the live store and reload into a FRESH file — rowids are reassigned on load.
        with Store(path) as store:
            blob = store.dump()
        directory2, path2 = make_temp_db()
        try:
            with Store(path2) as store2:
                store2.load(blob)
            _, after = anyio.run(_call, build_server(_config(path2)), "project_list", {})
            assert [p["id"] for p in after["projects"]] == [p["id"] for p in before["projects"]]
            assert [p["name"] for p in after["projects"]] == [p["name"] for p in before["projects"]]
        finally:
            cleanup(directory2)
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
