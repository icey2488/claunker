"""Transport layer (via Starlette's TestClient): Bearer auth (401 at the transport,
not a domain error), the verbatim CORS headers incl. the load-bearing
Expose-Headers: mcp-session-id, OPTIONS preflight, and all three methods wired.
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# The TestClient still works on the bundled httpx; silence its deprecation notice
# before the import that triggers it.
warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")

from starlette.testclient import TestClient  # noqa: E402

from spine_server.config import ServerConfig  # noqa: E402
from spine_server.http import ALLOW_METHODS  # noqa: E402
from spine_server.server import create_app  # noqa: E402
from tests.spine_server._util import cleanup, make_temp_db, seed  # noqa: E402

TOKEN = "test-token"
ORIGIN = "http://localhost:5173"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _app(path):
    # DNS-rebinding protection is disabled so TestClient's synthetic Host is accepted
    # and a valid initialize reaches a 200 (the production default keeps it ON).
    return create_app(
        ServerConfig(token=TOKEN, origin=ORIGIN, db_path=path, enable_dns_rebinding_protection=False)
    )


def test_missing_and_bad_token_are_rejected_401():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        with TestClient(_app(path)) as client:
            assert client.post("/mcp", json=INITIALIZE, headers=ACCEPT).status_code == 401
            bad = client.post("/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": "Bearer wrong"})
            assert bad.status_code == 401
    finally:
        cleanup(directory)


def test_configured_token_passes_auth_and_exposes_session_id():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        with TestClient(_app(path)) as client:
            ok = client.post("/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": f"Bearer {TOKEN}"})
            assert ok.status_code == 200            # passed auth AND the transport processed initialize
            assert "mcp-session-id" in ok.headers   # the session id rides a response header
    finally:
        cleanup(directory)


def test_unconfigured_server_token_fails_closed():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        app = create_app(ServerConfig(token=None, origin=ORIGIN, db_path=path, enable_dns_rebinding_protection=False))
        with TestClient(app) as client:
            # no server token configured → even a bearer-bearing request is 401.
            r = client.post("/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": "Bearer anything"})
            assert r.status_code == 401
    finally:
        cleanup(directory)


def test_cors_preflight_emits_verbatim_headers():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        with TestClient(_app(path)) as client:
            pre = client.options("/mcp", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
            assert pre.status_code == 204
            assert pre.headers["Access-Control-Allow-Origin"] == ORIGIN
            assert pre.headers["Access-Control-Allow-Methods"] == ALLOW_METHODS == "GET,POST,DELETE,OPTIONS"
            assert pre.headers["Access-Control-Expose-Headers"] == "mcp-session-id"
            allow_headers = pre.headers["Access-Control-Allow-Headers"]
            for required in ("Authorization", "Content-Type", "mcp-session-id", "mcp-protocol-version"):
                assert required in allow_headers
    finally:
        cleanup(directory)


def test_cors_headers_ride_every_response_including_the_401():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        with TestClient(_app(path)) as client:
            r = client.post("/mcp", json=INITIALIZE, headers=ACCEPT)  # 401 (no token)
            assert r.status_code == 401
            assert r.headers["Access-Control-Allow-Origin"] == ORIGIN
            assert r.headers["Access-Control-Expose-Headers"] == "mcp-session-id"
    finally:
        cleanup(directory)


def test_get_and_delete_are_wired_and_behind_auth():
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}])
        with TestClient(_app(path)) as client:
            # unauthenticated → 401 (behind the Bearer gate).
            assert client.get("/mcp", headers={"Accept": "text/event-stream"}).status_code == 401
            assert client.delete("/mcp").status_code == 401
            # authenticated but session-less → the transport answers (400), proving
            # GET (SSE stream) and DELETE (teardown) are wired, not 404.
            get = client.get("/mcp", headers={"Accept": "text/event-stream", "Authorization": f"Bearer {TOKEN}"})
            assert get.status_code == 400
            delete = client.delete("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
            assert delete.status_code == 400
    finally:
        cleanup(directory)
