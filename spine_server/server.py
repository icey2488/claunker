"""FastMCP wiring + the Starlette ASGI app.

Advertises EXACTLY two tools — ``board_get`` and ``card_list``. Advertising only
these is deliberate: Kanbantt gates features on advertised tool names, so with no
write/escalation/artifact/column/tag tools it treats the board as read-only and
never tries to render escalations (which would need ``escalation_list`` /
``escalation_resolve``). This slice is a mirror, not a controller.
"""

from __future__ import annotations

from typing import Optional

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from .board import build_board
from .cards import PayloadTooLarge, list_cards
from .config import STREAMABLE_HTTP_PATH, ServerConfig, from_env
from .http import BearerAuthMiddleware, CORSMiddleware
from .result import PAYLOAD_TOO_LARGE, domain_error_result, ok_result

# serverInfo (arrives in the ``initialize`` handshake; Kanbantt shows it as
# "MCP: Claunker"). The version is this server's own version — distinct from the
# spec version (0.2.4) and the data schema version (1).
SERVER_NAME = "Claunker"
SERVER_VERSION = "0.1.0"


def build_server(config: ServerConfig) -> FastMCP:
    """Construct the FastMCP server bound to ``config`` (its tools read the spine at
    ``config.db_path``). Tools return a fully-formed ``CallToolResult`` so their
    structuredContent passes through verbatim (see ``result.py``)."""
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=config.enable_dns_rebinding_protection,
        allowed_hosts=config.resolved_allowed_hosts(),
        allowed_origins=[config.origin],
    )
    mcp = FastMCP(
        SERVER_NAME,
        host=config.host,
        port=config.port,
        streamable_http_path=STREAMABLE_HTTP_PATH,
        transport_security=transport_security,
    )
    # FastMCP exposes no version kwarg; the underlying low-level Server carries it,
    # so this is the supported knob for serverInfo.version.
    mcp._mcp_server.version = SERVER_VERSION

    @mcp.tool(
        name="board_get",
        description="Return the read-only board: six columns (one per Task state) and the tier tags.",
        structured_output=False,
    )
    def board_get() -> types.CallToolResult:
        return ok_result(build_board())

    @mcp.tool(
        name="card_list",
        description="Return a full snapshot of the spine's live Tasks projected to Cards, plus a fresh sync_token.",
        structured_output=False,
    )
    def card_list(
        updated_since: Optional[str] = None,
        column_id: Optional[str] = None,
        tag: Optional[str] = None,
        include_deleted: bool = False,
    ) -> types.CallToolResult:
        try:
            result = list_cards(
                config.db_path,
                updated_since=updated_since,
                column_id=column_id,
                tag=tag,
                include_deleted=include_deleted,
                max_bytes=config.max_bytes,
            )
        except PayloadTooLarge as exc:
            return domain_error_result(
                PAYLOAD_TOO_LARGE,
                "card snapshot exceeds the configured size ceiling; narrow the query",
                {"size": exc.size, "limit": exc.limit},
            )
        return ok_result(result)

    return mcp


def create_app(config: Optional[ServerConfig] = None) -> Starlette:
    """The Starlette ASGI app: FastMCP's Streamable HTTP transport (POST requests,
    GET SSE stream, DELETE teardown — all three wired) wrapped with the Bearer gate
    (inner) and the CORS layer (outer)."""
    config = config or from_env()
    app = build_server(config).streamable_http_app()
    # add_middleware inserts outermost-last → CORS wraps Auth wraps the transport.
    app.add_middleware(BearerAuthMiddleware, token=config.token)
    app.add_middleware(CORSMiddleware, origin=config.origin)
    return app


def main() -> None:
    """Console entry point. Refuses to start without a configured token (no
    unauthenticated fallback)."""
    import uvicorn

    config = from_env()
    if not config.token:
        raise SystemExit("CLAUNKER_SPINE_TOKEN is required (no unauthenticated fallback).")
    uvicorn.run(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
