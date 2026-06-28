"""Transport-layer middleware: verbatim CORS and Bearer auth, as pure ASGI.

Both are pure-ASGI (not ``BaseHTTPMiddleware``) on purpose: the Streamable HTTP
transport serves an SSE stream over GET, and ``BaseHTTPMiddleware`` would buffer it.
Pure-ASGI lets us inject headers into ``http.response.start`` and short-circuit
preflight/401 without ever touching the response body.

Wiring (see ``server.create_app``): CORS is the OUTER layer, Bearer the inner one,
so OPTIONS preflight is answered before auth and a 401 still carries CORS headers
(the browser must be able to read the 401).
"""

from __future__ import annotations

import hmac
from typing import Optional

from starlette.responses import JSONResponse, Response

# The CORS contract, verbatim (kanbantt-mcp-spec §Transport & CORS). Allow-Origin is
# the configured client origin (filled per-request). Expose-Headers is load-bearing:
# the session id rides a response header and is invisible to browser JS without it.
ALLOW_METHODS = "GET,POST,DELETE,OPTIONS"
ALLOW_HEADERS = "Content-Type,Authorization,mcp-session-id,mcp-protocol-version,Accept,Last-Event-ID"
EXPOSE_HEADERS = "mcp-session-id"


class CORSMiddleware:
    """Echo the configured client origin, answer OPTIONS preflight directly, and put
    the full CORS header set on every response (including the auth 401)."""

    def __init__(self, app, origin: str) -> None:
        self.app = app
        self.origin = origin

    def _headers(self) -> dict:
        return {
            "Access-Control-Allow-Origin": self.origin,
            "Access-Control-Allow-Methods": ALLOW_METHODS,
            "Access-Control-Allow-Headers": ALLOW_HEADERS,
            "Access-Control-Expose-Headers": EXPOSE_HEADERS,
        }

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        if scope["method"] == "OPTIONS":
            response = Response(status_code=204, headers=self._headers())
            return await response(scope, receive, send)

        injected = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in self._headers().items()]

        async def send_with_cors(message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].extend(injected)
            await send(message)

        await self.app(scope, receive, send_with_cors)


class BearerAuthMiddleware:
    """``Authorization: Bearer <token>`` or 401 — at the transport layer, NOT a
    domain error, with no unauthenticated fallback. An unconfigured server token
    fails closed (every request 401). OPTIONS is exempt (preflight carries no auth,
    and CORS has already answered it upstream)."""

    def __init__(self, app, token: Optional[str]) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        if not self._authorized(scope):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            return await response(scope, receive, send)
        await self.app(scope, receive, send)

    def _authorized(self, scope) -> bool:
        if not self.token:
            return False  # fail-closed: no server token configured
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                header = value.decode("latin-1")
                if header.startswith("Bearer "):
                    return hmac.compare_digest(header[len("Bearer ") :], self.token)
                return False
        return False
