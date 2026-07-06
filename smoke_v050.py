"""v0.5.0 live smoke test.

Creates a test card via the spine facade (same DB the live server reads), then
drives card_update / card_delete through the live MCP server at localhost:8848.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TOKEN_FILE = ROOT / ".env.spine-token"
TOKEN = TOKEN_FILE.read_text(encoding="ascii").strip()

DB_PATH = str(ROOT / "spine" / "spine.db")
MCP_URL = "http://127.0.0.1:8848/mcp"

# ── step 1: find a live project and create the test card via the facade ────────
sys.path.insert(0, str(ROOT))
from spine import Spine, Store

ANOTHER_CARD_ID = None
TEST_CARD_ID = None

with Store(DB_PATH) as store:
    s = Spine(store)
    # Find first live project.
    projects = store.projects.list_live()
    if not projects:
        sys.exit("FATAL: no live projects in the spine DB")
    proj = projects[0]
    # Find another live task to use for depends_on.
    tasks = store.tasks.list_live()
    existing = [t for t in tasks if not t.deleted_at]
    if existing:
        ANOTHER_CARD_ID = existing[0].id
    # Create the test card.
    test_task = s.create_task(proj.id, "TEST: v0.5.0 smoke - tombstone me")
    TEST_CARD_ID = test_task.id
    TEST_VERSION = test_task.version

print(f"[1] Created test card: {TEST_CARD_ID}  version={TEST_VERSION}")
if ANOTHER_CARD_ID:
    print(f"    another card for depends_on: {ANOTHER_CARD_ID}")
else:
    print("    (no other live card; will use a placeholder id for depends_on)")
    ANOTHER_CARD_ID = "placeholder-card-id-for-smoke"

# ── step 2: drive card_update calls through the live MCP server ───────────────

async def call_tool(name: str, arguments: dict) -> dict:
    """Call one MCP tool via Streamable HTTP and return the structuredContent."""
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    async with streamablehttp_client(
        MCP_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            # structuredContent is the structured output dict.
            if result.structuredContent:
                return {"isError": result.isError, "content": result.structuredContent}
            # Fall back to text content.
            return {"isError": result.isError, "content": result.content}


def smoke(name, arguments, *, label):
    result = asyncio.run(call_tool(name, arguments))
    prefix = "ERROR" if result["isError"] else "OK"
    print(f"\n[{prefix}] {label}")
    print(json.dumps(result["content"], indent=2, default=str))
    return result


# Reload current version after facade create.
with Store(DB_PATH) as store:
    t = store.tasks.get(TEST_CARD_ID)
    TEST_VERSION = t.version

# 2a. Set due to a real date.
r = smoke("card_update", {
    "id": TEST_CARD_ID,
    "patch": {"due": "2026-12-31"},
    "expected_version": TEST_VERSION,
}, label="card_update due=2026-12-31 -> ledger row")
with Store(DB_PATH) as store:
    rows = store.list_edit_audit()
print(f"    edit_audit rows after set: {len(rows)}  last={rows[-1] if rows else None}")

# 2b. Reload version and clear due with null.
with Store(DB_PATH) as store:
    TEST_VERSION = store.tasks.get(TEST_CARD_ID).version
r = smoke("card_update", {
    "id": TEST_CARD_ID,
    "patch": {"due": None},
    "expected_version": TEST_VERSION,
}, label='card_update {"due": null} -> cleared + ledger row old=date/new=null')
with Store(DB_PATH) as store:
    rows = store.list_edit_audit()
    t = store.tasks.get(TEST_CARD_ID)
print(f"    due on disk after clear: {t.due!r}  edit_audit rows: {len(rows)}")
if rows:
    print(f"    last ledger row: {rows[-1]}")

# 2c. Reload version and try guarded tier=null -> validation_failed.
with Store(DB_PATH) as store:
    TEST_VERSION = store.tasks.get(TEST_CARD_ID).version
r = smoke("card_update", {
    "id": TEST_CARD_ID,
    "patch": {"tier": None},
    "expected_version": TEST_VERSION,
}, label='card_update {"tier": null} -> validation_failed, no row')
with Store(DB_PATH) as store:
    rows_after = store.list_edit_audit()
print(f"    edit_audit row count unchanged: {len(rows_after) == len(rows)}")

# 2d. Set depends_on to another real card.
with Store(DB_PATH) as store:
    TEST_VERSION = store.tasks.get(TEST_CARD_ID).version
r = smoke("card_update", {
    "id": TEST_CARD_ID,
    "patch": {"depends_on": [ANOTHER_CARD_ID]},
    "expected_version": TEST_VERSION,
}, label=f"card_update depends_on=[{ANOTHER_CARD_ID[:8]}...] -> row")
with Store(DB_PATH) as store:
    rows = store.list_edit_audit()
    t = store.tasks.get(TEST_CARD_ID)
print(f"    depends_on on disk: {t.depends_on}  edit_audit rows: {len(rows)}")

# 2e. Clear depends_on with [].
with Store(DB_PATH) as store:
    TEST_VERSION = store.tasks.get(TEST_CARD_ID).version
r = smoke("card_update", {
    "id": TEST_CARD_ID,
    "patch": {"depends_on": []},
    "expected_version": TEST_VERSION,
}, label='card_update {"depends_on": []} -> cleared + row')
with Store(DB_PATH) as store:
    rows = store.list_edit_audit()
    t = store.tasks.get(TEST_CARD_ID)
print(f"    depends_on on disk after clear: {t.depends_on}  edit_audit rows: {len(rows)}")

# 2f. Tombstone the test card.
with Store(DB_PATH) as store:
    TEST_VERSION = store.tasks.get(TEST_CARD_ID).version
r = smoke("card_delete", {
    "id": TEST_CARD_ID,
    "expected_version": TEST_VERSION,
}, label="card_delete (tombstone) the test card")

print("\nsmoke complete")
