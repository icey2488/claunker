# Claunker Build Roadmap — Hermes-Claunker

**Status:** Active build plan. Originally 2026-06-12; status ledger updated 2026-07-03. Phases 0-3 are COMPLETE and the anywhere-in-world loop is closed (spine.icehunter.net live via Cloudflare Tunnel, Kanbantt connecting from the deployed web app). The remaining work is Phases 4-7 plus a short punch list.
**Companions:** Foundations 00-05 + the v2 index, the Phase 2 build plan v3, the storage/schema ratifications, the Phase 1 findings log. This is the *execution* plan; the foundations are the *what and why*.
**Governing constraint (original):** the local inference rig (9800X3D + RTX 3080) gates only Phases 6-7. That sequencing held: everything through Phase 3 shipped with no rig.

---

## 0. Status ledger (2026-07-03)

| Phase | Status | Evidence |
|---|---|---|
| 0 — Chassis + stand-in executor | **DONE** | Gateway confirmation closed (findings log, 2026-06-13) |
| 1 — Judge loop | **DONE** | Live loop + honest defect ledger (FT-001..013, RC-001); FT-003/007/009 all RESOLVED with red-on-violation tests |
| 2 — MCP spine | **DONE** | Abort + classifier core (observe-only) + executor allowlist + spine server 127/127; storage/schema ratified; spine repo at spec v0.3.0, cleanup commit 753fba9 |
| 3 — Kanbantt integration | **DONE** | Live client 143/143 → production persistence port → write-through (c00c1b0) → card_retier + Unlock-to-re-tier (e6991ae, 59c8d10) → escalation resolve UI + structured control_diff renderer → read-only spine support → Connection settings BYO-spine (1d709b6) → CSP blast door (cb3cb82). Board-creates-spine-task and spine-task-renders-as-card both hold; the Phase 3 done-when is satisfied in both directions |
| First light / anywhere-in-world | **DONE 2026-07-02 03:28 PT** | cloudflared tunnel, spine.icehunter.net, firstlight token retired for a strong random credential (.env.spine-token, icacls-locked), edge matrix verified (401 bare, 8 tools via token, preflight echoes deployed origin, firstlight dead at edge); operator connected from the live site |
| Drive auth (spine transport dependency) | **DONE** | Rewritten to auth-code + PKCE with same-origin Cloudflare Pages Function (/api/auth/exchange); GIS/popup gone; top-level redirect; verified at pushed HEAD |
| 4 — Security loop | NOT STARTED | Pre-work partially banked: FT-008 Docker grant-narrowing done, executor allowlist done, judge outage fail-safe done |
| 5 — Downtime boundaries | NOT STARTED | |
| 6 — Rig bring-up + executor swap | BLOCKED ON RIG | |
| 7 — Economics + throughput | BLOCKED ON 6 | |

**Classifier arming (`enforce: true`):** still observe-only, parallel + evidence-gated per the Phase 2 plan. Not a phase gate; arm when observation logs confirm tiers on real dispatches.

**Post-Phase-3 board work also shipped (beyond the original roadmap's scope):** full mobile pass (Matrix compact map, Timeline frozen columns, long-press card move, tag filter collapse), sticky column headers, archival UI with purge guard + bulk sweep + Show Archive (4299489), Calendar Day view + Timeline Work Week (9535480), top-level view persistence (9cc427b), PolyForm Noncommercial 1.0.0 license (4b53d78). **Human-intake write path (spec v0.6.0→v0.6.1, 2026-07-20):** `project_list` + project-targeted `card_create` with human-intake defaults (Kanbantt 0610163), uniform conflict snap-back on move/delete (e61d6ff, PR #1 3314e71), conflict-envelope key pinned to `meta.current` (spec v0.6.1, fa22cd1; spine copy synced at claunker-hermes 8a0e990). The MCP spec is at **v0.6.1**, byte-identical in the canonical (kanbantt-app) and spine (`docs/kanbantt-mcp-spec.md`) copies [verified 2026-07-23].

## 0.1 Punch list (carry-forward, before/alongside Phase 4)

1. **Kanbantt cleanup arc — the one deferred item still open.** Deferred behind the Connection commit and never landed: README is still the stock Vite template at pushed HEAD, lint debt (audit counted 21), missing window guard behind two failing tests. One cleanup commit closes it.
2. **FT-008 grant inventory refresh.** New standing grants since the last inventory: the tunnel credential (.env.spine-token), the Cloudflare Tunnel itself, the OAuth redirect URI + Pages Function secret (grant #3), the Google Fonts runtime injection noted in the CSP. Plus the two carried actions: narrow Claude Code's Desktop-wide `.claude` read grant; FT-013 judge trust-block absent→hardcoded-default backstop.
3. **Classifier arming** per the evidence gate above.
4. **Architecture SVG update** requested at first light (firstlight token retirement marked); confirm it landed in the corpus.

---

## 1. The key realization: the rig is not on the critical path

Claunker's executor role is a **configuration value**, not a hardcoded dependency. Hermes routes `delegate_task` children to whatever `delegation.provider` / `delegation.model` names. The entire system was built and tested against a stand-in executor; the rig swap remains a config change.

**Validated by events:** Phases 0-3 shipped rig-free exactly as sequenced. The only thing still deferred is the cost-economics measurement (Phase 7).

---

## 2. Executor substitution strategy

Stand-in executor in place through Phases 0-5. Remaining item from this section: **one Ollama smoke test before Phase 6** on current hardware, proving the Ollama provider wiring end-to-end so the rig swap is a model change, not a first-time integration.

---

## 3. Phase map

```
 Phase 0 CHASSIS ──────────────────────────────── DONE
 Phase 1 JUDGE LOOP ───────────────────────────── DONE
 Phase 2 MCP SPINE ────────────────────────────── DONE (server 127/127; classifier observe-only, arming evidence-gated)
 Phase 3 KANBANTT ─────────────────────────────── DONE (live client, write-through, BYO connect, tunnel first light)
 Phase 4 SECURITY LOOP ────────────────────────── NEXT (no rig)
 Phase 5 DOWNTIME BOUNDARIES ──────────────────── open (no rig, independent)
 Phase 6 RIG BRING-UP + EXECUTOR SWAP ─────────── rig required
 Phase 7 ECONOMICS + THROUGHPUT ───────────────── rig required, after 6
```

---

## 4. Phases in detail

### Phase 0 — Chassis + stand-in executor — DONE
Gateway confirmation closed 2026-06-13: judge beat gate-guaranteed (RC-001 allowlist), skill-loads observed, executor beat gated against accidental fallback. See the Phase 1 findings log.

### Phase 1 — The judge loop — DONE
Full architect → executor → judge cycle live. The primary deliverable was the honest defect ledger: FT-003 (abort) fixed with a container-layer kill + RED/GREEN test; FT-007 (executor pin) closed by the delegation allowlist; FT-009 (judge outage self-verification) closed with the escalate envelope fix + 4/4 control test. FT-005 (durable judge-call provenance) still rides toward Phase 4.

### Phase 2 — The Claunker MCP spine — DONE
Per the Phase 2 build plan v3: abort → classifier core (observe-only) + executor allowlist → spine. Storage ratified (one Drive-durable blob, schema-dumb merge, R1-R6), four-entity schema ratified (write-once tier, structured control_diff, two-projection boundary, MI-1/2/3), convergence re-proven for the four-entity shape, MCP method surface built as a seam (127/127). Spine repo cleaned and public: README rewritten from code, spec v0.3.0, pyproject 0.3.0, commit 753fba9.

### Phase 3 — Kanbantt integration — DONE
The original done-when (board reflects live orchestration state; card-on-board ⟷ task-in-spine both directions) is satisfied and exceeded: MCPProvider live client with zero projection imports (proof-by-absence), production Drive persistence port, card write-through over the live spine, card_retier with write-once tier enforcement (spec v0.3.0) and the governed Unlock-to-re-tier control, escalation resolve + control_diff rendered legibly per §5.6, read-only spine servers first-class, BYO-spine Connection settings from the deployed web app (disposed-flag resurrection guards, parse-then-regex 401 classifier, unreachable strike counter), CSP blast door (script-src 'self', BYO connect-src), and the Cloudflare Tunnel closing the anywhere-in-world loop at spine.icehunter.net.

### Phase 4 — Security loop scaffolding — NEXT, no rig
Unchanged scope: `redteam` Hermes profile (isolated HERMES_HOME, fake/revoked credentials, restricted toolset, Docker backend); mirror the live gate config; fixed regression set of known-bad payloads against the live gate; `draft_policy_diff` plugin (write-blocked, layer-routed); two append-only quarantined logs; cron cadence with `cron_mode: deny` on live.
**Banked pre-work:** Docker backend switch + grant narrowing (FT-008 structural half), executor allowlist (FT-007), judge-unavailable hard-escalate (FT-009), §5.8 controls tests. **Entry criteria before starting:** the FT-008 inventory refresh (punch list #2), FT-013 backstop, FT-004 home-channel routing decision, FT-005 durable attribution log. §5.9's red-team isolation (scoped credential + separate host, two independent boundaries) governs the sandbox design.
**Done when:** introduce a gate hole → regression set fails loudly → `draft_policy_diff` emits a proposed fix requiring approval; the redteam profile provably cannot resolve a live credential.

### Phase 5 — Downtime boundaries — open, no rig
Unchanged: buffer / block / urgent_only modes on Hermes cron + gateway; "Held — outside active hours" buffering; `!urgent` / `!worksession` overrides; override audit log. Independent of Phase 4.

### Phase 6 — Rig bring-up + executor swap — RIG REQUIRED
Unchanged: assemble rig, Ollama, swap `delegation.provider/model` (the one-line payoff), decide gateway home, migrate persona into SOUL.md, decommission the GCP relay. Prerequisite from §2: the Ollama smoke test on current hardware.

### Phase 7 — Economics + throughput — RIG REQUIRED
Unchanged: measure paid-token-per-task vs the Phase 1 baseline, size concurrency to the 3080, tune `child_timeout_seconds`, apply tool-RPC scripting to serial-heavy pipelines.

---

## 5. Sequencing guidance

Phases 4 and 5 remain parallelizable and rig-free. Recommended: the punch list first (it is small and mostly inventory), then Phase 4, with Phase 5 slotted by energy. The rig gates only 6 → 7.

---

## 6. What the rig delay actually costs

Nothing structural, now demonstrated rather than argued: the full system (phone dispatch, three-model adversarial loop, persistent governed spine, live board reachable from anywhere) shipped on cloud + current hardware. The rig makes it cheaper and faster, not more capable.

---

## 7. Immediate next action

Close the Kanbantt cleanup commit (punch list #1), then run the FT-008 inventory refresh (punch list #2). Those two clear the deck for Phase 4, whose entry criteria are otherwise already banked.
