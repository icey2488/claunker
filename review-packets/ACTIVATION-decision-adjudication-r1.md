# Activation decision — round-1 adjudication, ratified v2 set, and acceptance receipts

Adjudicated 2026-07-31 (Claude, architect seat); operator ratified same day: "Agree with V2 set as adjudicated."

F1 ACCEPT BLOCKER — RPO downgrade stated explicitly in Register #13; verify-first on the pipeline found the sqlite3 Online Backup API already in place (fix n/a); regression test added (claunker-ops 194719f); restore drill must prove integrity_check + spine read. F2 PARTIAL->doc — spine clients were never offline-first (polling + expected_version per Foundation 02); Decision 2 wording now says so. F3 ACCEPT, fix narrowed MAJOR — freeze labeling over dual-target suite (no consumers; deletion discards ratified work); labels shipped kanbantt-app 2d5a686; BYO revival hard-gates on contract re-verification. F4 ACCEPT BLOCKER->MAJOR — single-token model: revocation IS rotation; lifecycle documented, rotation made an acceptance criterion and proven. F5 ACCEPT, split MAJOR — backend-identity test (claunker-hermes 6d4a791) + capture-method test (claunker-ops 194719f); operational freshness routes to the scheduled-run/alert lane (card 9b7ec4ef). F6 PARTIAL MINOR — authorization-path claim rejected (operator ratification authorizes, not the register); re-scope accepted: BYO persistence tracked on card 1c0c316a. F7 ACCEPT BLOCKER — acceptance grew to four criteria: render, live arrival, write round-trip, rotation drill.

Register #13 landed claunker-ops 23afda1. Hygiene: freeze labels + stale patch (kanbantt-app 2d5a686); rubric first write-down docs/DISPATCH-ROUTING.md (claunker-ops dbcc48b, enum bridge-verified low/medium/high/xhigh/max/ultracode).

ACCEPTANCE RECEIPTS (operator-ratified activation, completed 2026-08-02):
1. RENDER — operator connected phone + desktop via https://spine.icehunter.net/mcp, live board rendered. Endpoint note: client requires the /mcp path; error copy guided correctly.
2. LIVE ARRIVAL — card f2eb317c ("FIRST LIGHT") minted 2026-08-01T10:13:15Z by job firstlight-wireproof-20260801, appeared on both surfaces without reload; confirmed by operator.
3. WRITE ROUND-TRIP — operator drag of 225b2832 to judged from the phone; state verified through the independent CLI/sqlite path. Finding surfaced: edit_audit is blind to state transitions (zero rows DB-wide) — carded 29881322.
4. ROTATION — first drill interrupted by session limit mid-sequence, leaving file=new/server=old undetected (watchdog treats 401 as alive; designed consequence of credential isolation) — findings carded 3f93e789; completion 2026-08-02T01:09:26Z, 63s downtime, old token rejected, rotated token authorized. Drill v2 requirement recorded: write+bounce atomic.

Standing grant recorded: card 173bce6d (FT-008 line). Archive server-side discovery appended to 184eadf0. Payoff checklist e9bc9355 delivered. The activation set of the ratified v2 decision document is fully banked.
