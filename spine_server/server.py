"""FastMCP wiring + the Starlette ASGI app.

Advertises THREE tools — the read-only mirror pair (``board_get``, ``card_list``)
PLUS exactly one human-gated mutating control, ``escalation_resolve``. This slice
is therefore no longer a pure mirror: it is a read-only mirror plus a single,
narrow write path (an operator approve/deny on an escalation). Kanbantt still gates
features on advertised tool NAMES — with no card_*/column/tag/artifact write tools
the board stays a read-only mirror, and it renders escalations from the per-card
badge that already rides in ``card_list`` (no ``escalation_list`` is advertised;
see the asymmetric-advertising note on the tool below).

The actor that performs the mutation is derived from the AUTHENTICATED CREDENTIAL,
never from the request payload — see the actor-invariant comment on
``escalation_resolve``.
"""

from __future__ import annotations

from typing import Optional

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from spine import Spine, Store

from .board import build_board
from .cards import PayloadTooLarge, list_cards
from .config import STREAMABLE_HTTP_PATH, ServerConfig, from_env
from .http import BearerAuthMiddleware, CORSMiddleware
from .result import (
    NOT_FOUND,
    PAYLOAD_TOO_LARGE,
    UNAUTHORIZED,
    VALIDATION_FAILED,
    domain_error_result,
    ok_result,
)

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

    @mcp.tool(
        name="escalation_resolve",
        description=(
            "Resolve an escalation: record an operator approve/deny decision with a "
            "rationale. The one human-gated mutating control (it writes the spine)."
        ),
        structured_output=False,
    )
    def escalation_resolve(
        id: str,
        resolution: str,
        resolution_rationale: str,
    ) -> types.CallToolResult:
        # ── ACTOR INVARIANT (security-critical) ──────────────────────────────────
        # The actor is derived from the AUTHENTICATED CREDENTIAL, never from the
        # payload. This tool deliberately takes NO `actor` parameter, so a client
        # CANNOT set it via a JSON-RPC payload field — an extra `actor` field never
        # reaches resolve_escalation. The server authenticates exactly one Bearer
        # token (the operator token; see http.BearerAuthMiddleware), so the
        # authenticated actor is, by construction, 'operator'. THE TOKEN IS THE ACTOR
        # ASSERTION: an agent that steals the operator token still cannot forge a
        # DIFFERENT actor, because resolve_escalation re-asserts actor == 'operator'
        # and hard-aborts otherwise.
        #
        # TOKEN-SCOPING SEAM (v2): when distinct agent credentials exist (the v2 write
        # path), the actor must be resolved from the authenticated identity and agent
        # tokens MUST be refused for this tool — an agent is not 'operator'. Wire the
        # per-credential actor here the moment a second credential is introduced.
        actor = "operator"

        # WRITABLE STORE: the read tools open a Store only to read; this tool WRITES,
        # so it opens its own Store the way the Spine facade does (Spine(Store(path)))
        # and the put inside resolve_escalation commits. WAL serializes writers, so
        # this is safe alongside the read tools' connections and any other writer.
        try:
            with Store(config.db_path) as store:
                resolved = Spine(store).resolve_escalation(
                    id,
                    resolution=resolution,
                    resolution_rationale=resolution_rationale,
                    actor=actor,
                )
        except KeyError:
            return domain_error_result(NOT_FOUND, f"escalation {id!r} does not exist", {"id": id})
        except PermissionError as exc:
            # The actor invariant fired. Unreachable for the operator token today; the
            # defense-in-depth backstop for the v2 agent-credential write path.
            return domain_error_result(UNAUTHORIZED, str(exc), {"id": id})
        except ValueError as exc:
            # Bad resolution enum, or a rationale under the >=10-char semantic floor.
            return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})
        return ok_result({"escalation": resolved.to_dict()})

    # ASYMMETRIC-ADVERTISING / DEFERRED CAPABILITY GAP (deliberate, NOT permanent):
    # escalation_resolve is advertised WITHOUT a matching escalation_list query. The
    # resolve payload already rides in card_list's per-card badge, so a separate list
    # query is unnecessary for THIS slice — Kanbantt gates the resolve control on the
    # advertised escalation_resolve name and reads the badge from card_list. At v2
    # (multi-client / a real approval queue) realign discovery by adding escalation_list
    # so the read and write halves of the escalation surface are symmetric again.

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
