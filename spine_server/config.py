"""Server configuration — env-driven, with safe defaults.

Every knob has an env var so the server runs from the environment alone, but
``ServerConfig`` is a plain dataclass so tests construct it directly (temp db path,
explicit token, disabled DNS-rebinding protection for the in-process test client).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from spine.storage import DB_PATH

# Kanbantt's dev origin (Vite). Not pinned by the spec or the brief — override via
# CLAUNKER_SPINE_ORIGIN for any other client origin. Echoed in Access-Control-
# Allow-Origin and trusted by the transport's Origin check.
DEFAULT_ORIGIN = "http://localhost:5173"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8848

# Complete-or-payload_too_large ceiling for a card snapshot. The list tool NEVER
# truncates; if the full snapshot exceeds this it fails with payload_too_large.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB

# The Streamable HTTP endpoint path (MCP SDK default). Kanbantt's mcp.url points at
# ``<host>/mcp``.
STREAMABLE_HTTP_PATH = "/mcp"


@dataclass
class ServerConfig:
    """Resolved server configuration.

    ``token is None`` means no server token is configured → the Bearer gate fails
    closed (every request 401); there is no unauthenticated fallback.
    """

    token: Optional[str]
    origin: str = DEFAULT_ORIGIN
    db_path: str = DB_PATH
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_bytes: int = DEFAULT_MAX_BYTES
    # Extra Host-header values the transport will accept (the server's own host
    # variants are always allowed; see ``resolved_allowed_hosts``).
    allowed_hosts: List[str] = field(default_factory=list)
    # DNS-rebinding protection (MCP transport security). On by default; tests that
    # drive the app through an in-process client with a synthetic Host disable it.
    enable_dns_rebinding_protection: bool = True

    def resolved_allowed_hosts(self) -> List[str]:
        """Host-header values the transport accepts: the configured host/port plus
        the usual loopback aliases, then any explicit extras (order-preserving,
        deduped)."""
        base = [
            self.host,
            f"{self.host}:{self.port}",
            "localhost",
            f"localhost:{self.port}",
            "127.0.0.1",
            f"127.0.0.1:{self.port}",
        ]
        return list(dict.fromkeys(base + list(self.allowed_hosts)))


def _csv(name: str) -> List[str]:
    return [part.strip() for part in os.environ.get(name, "").split(",") if part.strip()]


def from_env() -> ServerConfig:
    """Build a ``ServerConfig`` from the CLAUNKER_SPINE_* environment."""
    return ServerConfig(
        token=os.environ.get("CLAUNKER_SPINE_TOKEN") or None,
        origin=os.environ.get("CLAUNKER_SPINE_ORIGIN", DEFAULT_ORIGIN),
        db_path=os.environ.get("CLAUNKER_SPINE_DB", DB_PATH),
        host=os.environ.get("CLAUNKER_SPINE_HOST", DEFAULT_HOST),
        port=int(os.environ.get("CLAUNKER_SPINE_PORT", str(DEFAULT_PORT))),
        max_bytes=int(os.environ.get("CLAUNKER_SPINE_MAX_BYTES", str(DEFAULT_MAX_BYTES))),
        allowed_hosts=_csv("CLAUNKER_SPINE_ALLOWED_HOSTS"),
    )
