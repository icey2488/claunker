"""Claunker Spine MCP server — the governed board surface.

The spine data core (``spine/``) is in-process truth with no network surface. This
package puts a real MCP server in front of it so Kanbantt can drive the spine's
Tasks as a board of Cards in six columns — conforming to the Kanbantt MCP spec
v0.6.0 (synced to ``docs/kanbantt-mcp-spec.md``).

Shape:

    config.py   ``ServerConfig`` + ``from_env`` — token, CORS origin, db path, the
                complete-or-payload_too_large ceiling, and transport-security hosts.
    board.py    ``build_board`` — the six columns DERIVED from the Task ``State``
                enum (id == state value, so the board stays in lockstep with the
                projection's ``column_id``) + the tier tags derived from the tier
                values (format pinned to what ``spine/projection.py`` emits).
    cards.py    ``list_cards`` — the spine's live Tasks projected to Cards + a fresh
                ``sync_token``; full snapshot, never truncated (payload_too_large
                instead); tombstones omitted unless ``include_deleted``.
    result.py   ``CallToolResult`` helpers: success structuredContent (top-level
                object, extension fields preserved) and the {code,message,meta}
                domain-error shape (isError).
    http.py     pure-ASGI CORS + Bearer middleware (pure-ASGI so the SSE GET stream
                is never buffered). Verbatim CORS headers; 401 at the transport.
    server.py   FastMCP wiring: advertises the eleven governed tools ``board_get``,
                ``card_list``, ``project_list``, ``card_create``, ``card_update``,
                ``card_move``, ``card_delete``, ``card_retier``, ``card_archive``,
                ``card_unarchive``, and ``escalation_resolve``, plus ``create_app``
                (the Starlette ASGI app) and ``main``.

GOVERNED: the write tools mutate the spine store under optimistic-concurrency
version checks; ``board_get``, ``card_list``, and ``project_list`` stay read-only.
"""

from .board import (  # noqa: F401
    BOARD_SCHEMA_VERSION,
    KANBANTT_SCHEMA_VERSION,
    TIERS,
    build_board,
    build_columns,
    build_tags,
    tier_tag_id,
)
from .cards import PayloadTooLarge, list_cards, mint_sync_token  # noqa: F401
from .config import ServerConfig, from_env  # noqa: F401
from .result import domain_error_result, ok_result  # noqa: F401
from .server import (  # noqa: F401
    SERVER_NAME,
    SERVER_VERSION,
    build_server,
    create_app,
    main,
)
