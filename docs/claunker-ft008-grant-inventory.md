# Claunker — Standing Tool-Access Grant Inventory (FT-008) — v2.1

**Status:** Second inventory, 2026-07-03. Supersedes the 2026-06-16 first inventory (Drive corpus, moves to Superseded). Trigger: the re-inventory rule fired several times over — the Cloudflare Tunnel (a public edge + new credential), the OAuth Drive-auth rewrite (a new secret + redirect grant), and two new dispatch lanes (claude-async, Desktop Claude Code). This discharges punch-list #2 and is a Phase 4 entry criterion. v2.1, 2026-07-07: touch-up — Grant #3 watch updated (Kanbantt remember-token opt-in shipped 2026-07-04, commit 6b7803b); Grant #9 added (ClaunkerElevatedRunner, standing elevation trampoline, verified 2026-07-06).

**Audit stance (unchanged):** the point is a verdict per grant — deliberately scoped, over-broad, or fails open — not a list.

**Verification note (v2):** every machine-checkable claim was read-only verified against live files on 2026-07-03 before this document was written. Claims bearing **[verified 2026-07-03]** were confirmed from source. Claims bearing **[asserted from record]** could not be machine-checked in this pass (historical state, Drive corpus, or external-system state) and rest on prior audit records. Corrections from the draft are marked **[CORRECTED]**.

**Verification note (v2.1 addendum):** new claims in this revision were read-only verified on 2026-07-06. Grant #3 watch update rests on the Kanbantt commit record (6b7803b, 2026-07-04) [asserted from record]. Grant #9 (ClaunkerElevatedRunner): task action, run-level, run-as principal, trigger, and runner script contents were confirmed from `schtasks /query /tn ClaunkerElevatedRunner /fo LIST /v` and direct file read of `logs\_elevated_runner.ps1` on 2026-07-06 [verified 2026-07-06].

---

## The grants, as of 2026-07-03

### 1. Hermes executor — scoped host sandbox directory

Unchanged from v1: Docker backend (`terminal.backend: docker` [verified 2026-07-03]), `container_persistent: true` [verified 2026-07-03], container host binds (`/root` and `/workspace` under `sandboxes\docker\`), read-only infra binds. NOT the operator's filesystem, NOT container-only. Delegation allowlist present: `allowed_providers: [nous]`, `allowed_models: [stepfun/step-3.7-flash:free]` [verified 2026-07-03].

**Verdict: INTENTIONAL, materially narrowed.** Same watch: partial writes on kill stay in the sandbox binds; spine state is disjoint by design.

### 2. Spine server — local SQLite durable store (CORRECTED from v1)

**Holds:** durable orchestration state in `spine/spine.db` (SQLite, WAL mode per `PRAGMA journal_mode=WAL` in storage.py [verified 2026-07-03]) local to the Windows host, per the 2026-06-28 storage amendment. WAL sibling files (`spine.db-wal`, `spine.db-shm`) were absent at verification time — expected behavior after a checkpoint with no active writers, not an indication that WAL mode is off [verified 2026-07-03]. The v1 entry described a Drive credential for `claunker_spine_v1`; that reflects the pre-amendment design. NO Drive-sync controller is wired in the live persistence path: `storage.py` explicitly designates `dump()`/`load()` as "the seam for a *future* Google-Drive sync (sync-merge is out of scope this slice)" [verified 2026-07-03]. No `claunker_spine_v1` reference found anywhere in the live codebase [verified 2026-07-03].

**Reach:** one local database file.

**Verdict: INTENTIONAL per the amendment — but the durability property moved and is now a flagged gap.** §5.9's "spine state is Drive-durable" does not currently hold: the ledger's blast radius is one machine. The dump/merge/load sync seam is designed and unwired. Until wired, a disk loss is a total orchestration-ledger loss.

**Action owed:** wire the Drive-durable sync (or an equivalent off-machine backup) before the spine is load-bearing for anything beyond reconstructable work.

### 3. Kanbantt — spine URL + Bearer token; now also the OAuth Drive grant

**Holds:** (a) spine base URL + Bearer `auth_token` via Connection settings (BYO-spine, shipped 2026-07-02): **[CORRECTED; updated v2.1]** as of the v2 cut (2026-07-03) the token was always-persisted to `localStorage['kanbantt_config']` on every connect. The remember-token opt-in shipped 2026-07-04 (Kanbantt commit 6b7803b). Current behavior: token is held in memory only by default; the user may explicitly opt in to localStorage persistence. Legacy configs (pre-6b7803b) were migrated as "remembered" (treated as opted-in). The operator has opted in on the daily device [asserted from record, 6b7803b]. (b) The Google Drive OAuth grant for board sync: auth-code + PKCE, `client_id` public-by-design in the bundle, `client_secret` held server-side in the Cloudflare Pages Function env (`/api/auth/exchange`), redirect URI registered in the OAuth console. Google OAuth session tokens are kept in memory only (no localStorage) per `auth.js` [verified 2026-07-03].

**Reach:** full spine MCP surface (R+W) when connected; the user's Drive scope for board-blob sync.

**Verdict: INTENTIONAL, secret correctly placed.** The `client_secret` is in the Function env, not the bundle — the decision the auth plan required. CSP blast door (`script-src 'self'`) is the compensating control for any opted-in persistent spine Bearer token.

**Watch [CORRECTED; updated v2.1]:** the remember-token opt-in shipped 2026-07-04 (Kanbantt 6b7803b); spine Bearer token is now in-memory-only by default, with explicit operator opt-in to localStorage persistence. Legacy configs were migrated as opted-in; the operator has opted in on the daily device (accepted). CSP (`script-src 'self'`) remains the compensating control — the only barrier between XSS and token exfiltration for opted-in sessions. Keep the CSP strict; Google Fonts runtime injection is a known accepted carve-out. Read-only vs read-write board roles remain a pre-multi-user decision.

### 4. Claude Code (dev-time) — Desktop-wide read (STILL OVER-BROAD, action still owed)

Unchanged from v1: `Read(//c/Users/Raide/OneDrive/Desktop/**)` + `additionalDirectories: ["c:\\Users\\Raide\\OneDrive\\Desktop"]` persists in `AutoSummon/.claude/settings.json` [verified 2026-07-03]. `.claude/` gitignore containment done; scope narrowing NOT done.

**Verdict: OVER-BROAD.** The owed pre-Phase-4 action stands: narrow to actual project dirs; decide spine MCP-connect scope before wiring.

### 5. Judge + executor pins — allowlists (FT-013 config condition resolved; new code mismatch open)

[CORRECTED] The FT-013 condition ("allow_*_override present without an allowlist") is NOT observed in the live config [verified 2026-07-03]. The live `config.yaml` carries both override flags AND allowlists:

```
allow_provider_override: true
allow_model_override: true
allowed_providers: [gemini]
allowed_models: [gemini-2.5-flash]
```

Executor allowlist fail-closed-by-default remains (FT-007 closed [asserted from record]).

**NEW OPERATIONAL FINDING [verified 2026-07-03]:** the code pin `JUDGE_MODEL = "gemini-3.5-flash"` (`plugins/judge-verdict/tools.py:25`) does not match the live config allowlist `allowed_models: [gemini-2.5-flash]`. The trust gate compares the requested model against the allowlist at call time; a mismatch raises `PluginLlmTrustError`. If the judge plugin is invoked in the current state, every call will fail. The reference doc (`judge-config.md`) shows `gemini-3.5-flash` as the intended allowlist value, suggesting the live config was updated to `gemini-2.5-flash` without updating the code constant (or vice versa). One of the two must be corrected.

**Broader FT-013 concern (code-level backstop for absent allowlist [asserted from record]):** the code audit item — what happens when override flags are set but no allowlist is present in config — is a separate concern from the config-level condition. That code audit remains open until a hardcoded default/backstop is confirmed in Hermes' plugin LLM trust gate.

**Verdict: FT-013 config-level condition closed; two new open items.** Code/allowlist model name mismatch is a blocker for judge operation. Broad code backstop audit still owed.

**Actions owed:** (a) align `JUDGE_MODEL` constant in `tools.py` with the live config allowlist, or align the config allowlist with the intended code pin; (b) confirm or add a hardcoded default in the Hermes plugin LLM trust gate for the absent-allowlist case.

### 6. Cloudflare Tunnel + spine edge token (NEW)

**Holds:** cloudflared running as a Windows service, STATE 4 RUNNING [verified 2026-07-03], carrying `spine.icehunter.net` to the local spine server; a strong random bearer credential in `.env.spine-token` (file exists [verified 2026-07-03]; `icacls` shows single principal `DOWNSTAIRS_PC\Raide:(F)` — owner-only, no other ACL entries [verified 2026-07-03]); `CLAUNKER_SPINE_ALLOWED_HOSTS` + `CLAUNKER_SPINE_ORIGIN` restricting host/origin at the app layer [verified 2026-07-03 from `spine_server/config.py`]. Edge matrix verified 2026-07-02 [asserted from record]: 401 bare, tools via token, preflight echoes the deployed origin, the retired firstlight token dead at edge.

**[CORRECTED]** Draft listed the app-layer env vars as `ALLOWED_HOSTS` + `CLAUNKER_SPINE_ORIGIN`; the actual env var name for the hosts list is `CLAUNKER_SPINE_ALLOWED_HOSTS` per `spine_server/config.py`.

**Reach:** the spine's entire MCP surface, from the public internet, gated by one bearer token.

**Verdict: INTENTIONAL, deliberately hardened at creation** — the rare grant that arrived with its audit done.

**Watch:** this is the system's first standing PUBLIC edge; the token is the whole gate. No rotation story exists yet — define one (even "rotate on suspicion + quarterly") before Phase 4's red team treats this edge as a target.

### 7. claude-async MCP bridge (NEW — the widest new grant)

**Holds:** an MCP server registered in Claude Desktop (`claude_desktop_config.json`, name `claude-async`, command `claude-async-server.mjs` via Node [verified 2026-07-03]) that any connected Claude session (claude.ai chat, Desktop) can use to start detached Claude Code jobs with arbitrary prompts and `workFolder`s on this machine. Located at `C:\Users\Raide\tools\claude-async`; job directory `C:\Users\Raide\.claude-async-jobs` (active job records confirmed [verified 2026-07-03]). An HTTP mode (`claude-async-http.mjs`) is also present managed by PM2, separate from the Desktop MCP registration [verified 2026-07-03 from `ecosystem.config.cjs`].

**Reach:** effectively everything the operator's user account can do — Claude Code runs with user permissions, unsandboxed, on any `workFolder`.

**Verdict: OVER-BROAD BY NATURE, accepted deliberately as the subscription-priced dispatch lane.** The compensating control is the dispatch-lane ledger (design note 2026-07-03 [asserted from record]): jobcard entries per job, moving to auto-carding once `created_by` + `artifacts` land. This lane has NO classifier, NO allowlist, NO judge gate — the operator is the gate, plus Gemini review at the prompt-drafting seat.

**Watch:** any session with this connector is dispatch authority. If the connector set ever widens beyond the operator's own sessions, this grant is the first to revisit.

### 8. Desktop Claude Code lane (NEW — recorded for completeness)

**Holds:** interactive Claude Code under the subscription, dispatched from Claude Desktop [asserted from record]; same user-account reach as #7 without the remote-trigger surface.

**Verdict: INTENTIONAL.** Correlated Claude proposer/executor pair behind a decorrelated Gemini review seat; tracked on the board via jobcard. The economics note: this lane substitutes for the rig at ~zero marginal cost, which reframes Phase 7's baseline.

### 9. ClaunkerElevatedRunner — standing elevation trampoline (NEW)

**Holds:** a Windows scheduled task (`\ClaunkerElevatedRunner`, `Run As User: Raide`, `RunLevel: HighestAvailable`) with a one-time trigger set to 2099-01-01 — it never fires on its own schedule and exists solely as an on-demand trampoline via `schtasks /run /tn ClaunkerElevatedRunner`. Last run confirmed at 2026-07-03 21:26 (task prompt states "in use 2026-07-04"; schtasks record shows 2026-07-03 — plausible documentation lag). [verified 2026-07-06]

The action is **not a fixed operation**: it executes `logs\_elevated_runner.ps1`, which reads whatever PowerShell has been staged at `logs\elevated_command.ps1` and spawns it as a child `powershell.exe -ExecutionPolicy Bypass -File` process, capturing stdout/stderr to `elevated_result.json`. The command file is an ordinary user-writable path under the project tree — any process running as `Raide`, including non-elevated processes, can write it. `schtasks /run` does not require elevation; the task infrastructure supplies the token upgrade from the stored task credential. Runner script contents confirmed by direct file read [verified 2026-07-06].

**Reach:** arbitrary PowerShell at HighestAvailable privilege, on demand, from any user-session process. Both prerequisites — write `logs\elevated_command.ps1`, call `schtasks /run` — are reachable without elevation. Via the claude-async lane (Grant #7): a dispatched job running at user-account permission level can stage the command file and fire the task. Section 5.6 reachability logic therefore applies: any claude-async dispatch is transitively elevation-capable, with no additional gate between the dispatch surface and HighestAvailable privilege.

**Verdict: STANDING ELEVATION TRAMPOLINE — reachability accepted, capability surface unbounded.** The action is arbitrary PowerShell (not a fixed or allowlisted script), making this trampoline functionally equivalent to a persistent `RunAs Administrator` without a UAC gate. The design is intentional — it was built to solve a specific operational need (re-registering scheduled tasks without prompts). The risk is the combination: (a) an arbitrary-command execution target at HighestAvailable privilege, (b) reachable from any user-session process including non-elevated ones, (c) transitively reachable from the claude-async dispatch lane (Grant #7) with no classifier, allowlist, or judge gate on that lane, and (d) the only gate is filesystem write access to a path inside the operator's own project tree. This does not change the verdict on Grant #7 (accepted, operator is the gate) — it adds a transitive elevation dimension to it.

**Watch:** the command file path (`logs\elevated_command.ps1`) is a user-writable path reachable from any user-session process, including claude-async jobs. A hardened posture would either (a) restrict the command file path to a location only an elevated actor can write, or (b) enumerate and allowlist specific permitted operations rather than executing arbitrary staged content. Until then, treat the claude-async lane as transitively holding HighestAvailable elevation authority, and weight that into any future expansion of who can trigger that lane.

---

## Audit summary

| # | Grant | Reach | Verdict |
|---|---|---|---|
| 1 | Hermes executor | sandbox host dir + container | INTENTIONAL — narrowed |
| 2 | Spine server | spine.db, one local file | INTENTIONAL — **Drive-durability gap flagged** |
| 3 | Kanbantt | spine surface R+W; Drive OAuth | INTENTIONAL — secret correctly server-side; **Bearer token opt-in persistent as of 2026-07-04 [CORRECTED; updated v2.1]**; CSP remains the wall |
| 4 | Claude Code | Desktop-wide read | **OVER-BROAD** — narrowing still owed |
| 5 | Judge/executor pins | model-family authorization | **FT-013 config condition closed; code/allowlist mismatch BLOCKS judge; code backstop audit still owed [CORRECTED]** |
| 6 | Tunnel + edge token | full spine surface, public internet | INTENTIONAL — hardened; rotation story owed |
| 7 | claude-async | operator user account, remote-triggerable | **OVER-BROAD BY NATURE** — accepted; ledger is the control |
| 8 | Desktop Claude Code | operator user account, interactive | INTENTIONAL — correlated lane, on-ledger |
| 9 | ClaunkerElevatedRunner | arbitrary PowerShell at HighestAvailable, user-session-triggerable | **STANDING ELEVATION TRAMPOLINE** — reachability accepted; claude-async lane transitively elevation-capable |

**Owed actions, consolidated (pre-Phase-4):**
- #4 scope narrowing (carried from v1)
- #5a align `JUDGE_MODEL` constant (`tools.py:25`) with live config allowlist, or vice versa — **judge is currently broken** (NEW, blocker)
- #5b confirm/add hardcoded absent-allowlist backstop in Hermes plugin LLM trust gate (carried FT-013 code concern)
- #2 Drive-durable spine sync (new)
- #6 token rotation story (new)

- #9 harden elevation trampoline: restrict `logs\elevated_command.ps1` to a location only an elevated actor can write, or enumerate/allowlist permitted operations — eliminates the arbitrary-command surface at HighestAvailable without removing the operational utility (deferred; accepted as-is until the claude-async lane's operator-only gate is no longer sufficient)

**Re-inventory trigger (unchanged):** any new spine client, any new credential, any scope change. Each gets a row as it lands.
