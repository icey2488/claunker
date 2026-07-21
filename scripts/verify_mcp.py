"""Authed MCP probe: initialize + tools/list against the live spine.

Run (from the repo root): .venv\Scripts\python.exe scripts\verify_mcp.py

Prints serverInfo and the sorted advertised tool names. Exit 0 iff every
REQUIRED_CORE tool is advertised — a superset passes, so newly added tools
never fail the probe; retiring a core tool means updating REQUIRED_CORE here.

The bearer token lives in ONE place: .env.spine-token (gitignored, ACL-locked)
— same source as logs/_relaunch_spine.py. No secrets in this file.
"""
import os
import sys

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, ".env.spine-token")

HOST = os.environ.get("CLAUNKER_SPINE_HOST", "127.0.0.1")
PORT = os.environ.get("CLAUNKER_SPINE_PORT", "8848")
URL = f"http://{HOST}:{PORT}/mcp"

# Keep in sync with the @mcp.tool registrations in spine_server/server.py.
# REQUIRED-SUBSET semantics, not an exact-match freeze: the old probe froze
# the surface at 3 tools and went stale the moment 0.6.0 shipped 11.
REQUIRED_CORE = frozenset({
    "board_get",
    "card_list",
    "escalation_resolve",
    "project_list",
    "card_create",
    "card_update",
    "card_move",
    "card_delete",
    "card_retier",
    "card_archive",
    "card_unarchive",
})


def _read_token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="ascii") as f:
            token = f.read().strip()
    except OSError as exc:
        sys.exit(f"FATAL: cannot read {TOKEN_FILE}: {exc}")
    if not token:
        sys.exit("FATAL: empty token in " + TOKEN_FILE)
    return token


async def main() -> int:
    token = _read_token()
    async with streamablehttp_client(URL, headers={"Authorization": f"Bearer {token}"}) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"serverInfo: {init.serverInfo.name} {init.serverInfo.version}")
            print(f"TOOLS({len(names)}): {names}")
            missing = sorted(REQUIRED_CORE - set(names))
            extra = sorted(set(names) - REQUIRED_CORE)
            if extra:
                print(f"note: {len(extra)} tool(s) beyond the required core: {extra}")
            if missing:
                print(f"RESULT: FAIL — missing required core tools: {missing}")
                return 1
            print(f"RESULT: PASS ({len(REQUIRED_CORE)} required core tools all advertised)")
            return 0


sys.exit(anyio.run(main))
