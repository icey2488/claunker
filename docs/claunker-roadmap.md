# Claunker Build Roadmap — Hermes-Claunker

**Status:** Active build plan
**Date:** 2026-06-12
**Companions:** Foundations 00–05 + the v2 index. This is the *execution* plan; the foundations are the *what and why*.
**Governing constraint:** The local inference rig (9800X3D + RTX 3080) is not yet ordered or assembled. This roadmap is sequenced so that the rig gates as little as possible.

---

## 1. The key realization: the rig is not on the critical path

Claunker's executor role is a **configuration value**, not a hardcoded dependency. Hermes routes `delegate_task` children to whatever `delegation.provider` / `delegation.model` names. That means the entire system can be built and tested against a **stand-in executor** now, and switched to the rig's local Ollama later with a one-line config change.

Of the eight phases below, **six require no rig at all** (Phases 0–5). The rig gates only Phase 6 (executor swap + relay decommission) and Phase 7 (economics validation + throughput tuning). The architecture, the loop, the MCP spine, Kanbantt, the security loop, and the downtime boundaries are all buildable and testable today.

What you *cannot* validate until the rig arrives: the local-inference-first **cost economics** (free local tokens for execution volume). Everything else is provable without it.

---

## 2. Executor substitution strategy

During Phases 0–5, the executor role is filled by a stand-in. Recommended approach:

- **Primary stand-in: a cheap hosted model** (e.g. Haiku, or an OpenRouter low-cost/free tier) as `delegation.model`. Fast iteration, zero local setup, exercises the full delegation path. Cost is real but small during development.
- **One Ollama smoke test before the swap:** at least once before Phase 6, point `delegation.provider` at a small Ollama model on whatever current hardware exists (laptop/desktop GPU or even CPU). This proves the *Ollama provider wiring* end-to-end so the rig swap is a model change, not a first-time integration. De-risks Phase 6 to near-zero.

The swap in Phase 6 is then: change two config lines, repull on real hardware, done.

**Consequence to accept consciously:** developing executor-blind to cost means the cost thesis is unvalidated until Phase 7. That is fine — it is the *only* thing deferred, and it is a measurement, not a design risk.

---

## 3. Phase map

```
        ┌─ Phase 0  CHASSIS + STAND-IN EXECUTOR ─┐   (no rig)
        │  Hermes install · Discord · DM pairing │
        │  Claude parent · stand-in executor     │
        └────────────────┬───────────────────────┘
                         │
     ┌───────────┬───────┴───────┬───────────────┐
     ▼           ▼               ▼               ▼
  Phase 1     Phase 2         Phase 4         Phase 5      (all no rig,
  JUDGE LOOP  MCP SPINE       SECURITY LOOP   DOWNTIME      parallelizable
  judge_      Project/Task/   redteam profile mode logic    after Phase 0,
  verdict +   Artifact/Escal  regression set  buffer/block/  bounded only by
  orch skill  + HermesMCP     draft_policy_   urgent_only    you being one dev)
     │        client wiring   diff + 2 logs   + override log
     │           │
     │           ▼
     │        Phase 3  KANBANTT  (no rig)
     │        MCPProvider → Claunker MCP server
     │           │
     └─────┬─────┘
           ▼
     ┌─────────────────────────────────────────┐
     │  Phase 6  RIG BRING-UP + EXECUTOR SWAP   │  ◀── RIG REQUIRED
     │  assemble · Ollama · swap config ·       │
     │  gateway to rig · decommission GCP relay │
     └────────────────┬─────────────────────────┘
                      ▼
     ┌─────────────────────────────────────────┐
     │  Phase 7  ECONOMICS + THROUGHPUT         │  ◀── RIG REQUIRED
     │  measure paid-token/task · concurrency · │
     │  child_timeout · tool-RPC scripting      │
     └─────────────────────────────────────────┘
```

---

## 4. Phases in detail

### Phase 0 — Chassis + stand-in executor · *no rig*
**Goal:** Phone-dispatched task completes the architect → executor loop with a stand-in executor.
- Install Hermes on the current dev machine (or the $5 VPS — either works; gateway location is decided in Phase 6).
- Configure Claude as the parent/architect provider.
- Set `delegation.model` to the hosted stand-in executor.
- Wire the Discord adapter; enable DM pairing.
- Pin the Hermes version; treat updates as deliberate events from here on.

**Done when:** a task sent from Discord on mobile decomposes (Claude) and executes (stand-in) end-to-end, no GCP relay involved.

---

### Phase 1 — The judge loop · *no rig*
**Goal:** Full architect → executor → judge cycle on a real task.
- Build the `judge_verdict` plugin (Gemini API; `~/.hermes/plugins/`). Input: task spec + executor output + acceptance criteria. Output: accept / revise / escalate + rationale.
- Author the orchestration `SKILL.md`: when to fan out, what a complete work order contains, judge criteria, revision limits. Include Pitfalls + Verification sections.
- Define the triage rule: architect self-accepts low-risk output; judge gates only substantive work (protects the latency/cost the judge would otherwise eat).

**Done when:** a known task runs the full three-model loop; paid-token-per-task is measured as a baseline (will drop in Phase 7 once the executor goes local).

---

### Phase 2 — The Claunker MCP spine · *no rig*
**Goal:** Orchestration-domain state persisted and queryable. This is the Foundation 01 spine, and it is **not** replaced by Hermes session storage — different state.
- Implement the MCP orchestration-state **server**: entities Project / Task / Artifact / Escalation; operations `list_projects`, `get_project`, `create_task`, `update_task`, `cancel_task`, `list_tasks`, `get_artifact`, `list_escalations`, `resolve_escalation`.
- Wire Hermes (MCP **client**) to it.

**Done when:** tasks, artifacts, and escalations created during a loop persist in the spine and are queryable via Hermes's MCP client. Parallelizable with Phase 1.

---

### Phase 3 — Kanbantt integration · *no rig*
**Goal:** The board is the live visual surface and a tool the architect reads/writes.
- Point Kanbantt's `MCPProvider` at the Claunker MCP server from Phase 2.
- Capability discovery lights up the Escalations column and real-time sync.
- Architect can read/write board state as a tool.

**Done when:** board reflects live orchestration state; a card created on the board appears as a task in the spine and vice versa. Depends on Phase 2.

---

### Phase 4 — Security loop scaffolding · *no rig*
**Goal:** A deliberately introduced gate weakness is caught and surfaces a white-hat draft.
- Create the `redteam` Hermes profile: isolated HERMES_HOME, **fake/revoked** credentials, restricted toolset, Docker backend. Its executor can also be the stand-in.
- Mirror the live gate config (approvals + blocklist) into the redteam profile so it tests the real gate logic against fake creds.
- Build the fixed **regression set** of known-bad payloads, asserted against the live gate.
- Build the `draft_policy_diff` plugin: write-blocked, layer-routed (gate-tighten → credential-scope → prose, in that order).
- Stand up the two append-only logs (finding log, policy diff log), both quarantined from any live-tool context.
- Schedule cadence via cron: regression set on every gate-code change; generative red-teamer weekly + on tool/credential changes. `cron_mode: deny` on the live profile.

**Done when:** introduce a gate hole → regression set fails loudly → `draft_policy_diff` emits a proposed fix you must approve before it applies; the redteam profile provably cannot resolve a live credential. Depends on Phase 0.

---

### Phase 5 — Downtime boundaries · *no rig*
**Goal:** Foundation 03 behavior enforced on the chassis.
- Implement the three modes (buffer / block / urgent_only) as Claunker logic layered on Hermes cron + gateway. (Hermes supplies the timer and delivery; the mode semantics are yours.)
- Schedule entity, escalation buffering with "Held — outside active hours" state, `!urgent` / `!worksession` overrides.
- Override audit log.

**Done when:** an off-hours task buffers and surfaces at next active time; `!urgent` overrides and the override is logged. Depends on Phase 0; independent of 1–4.

---

### Phase 6 — Rig bring-up + executor swap · *RIG REQUIRED*
**Goal:** Full loop runs on the local executor; relay-era prototype retired.
- Order/assemble the rig (9800X3D + RTX 3080). Install Ollama; pull executor model(s).
- Swap `delegation.model` / `delegation.provider` from stand-in to local Ollama (the one-line payoff).
- Decide gateway home: on the rig directly, or on the VPS with an SSH terminal backend into the rig.
- Migrate Claunker persona/prompt material into SOUL.md + USER.md/MEMORY.md.
- Decommission the GCP e2-micro relay (cost saving, one less attack surface).

**Done when:** a phone-dispatched task completes the full loop with the local executor and zero GCP involvement. The Phase 0 Ollama smoke test means this is a model swap, not an integration.

---

### Phase 7 — Economics + throughput · *RIG REQUIRED*
**Goal:** The cost thesis is validated with numbers; throughput tuned.
- Measure paid-token-per-task with the free local executor vs the Phase 1 baseline. This is the proof of the local-inference-first thesis.
- Size `delegate_task` concurrency to the 3080's batching headroom.
- Tune `child_timeout_seconds` down for fast local models.
- Apply the tool-RPC scripting pattern to serial-heavy pipelines (the Amdahl's-law fix).

**Done when:** paid-token-per-task is measurably below the Phase 1 baseline and throughput is tuned to the hardware.

---

## 5. Sequencing guidance

- **Strict prerequisites:** Phase 0 before everything. Phase 2 before Phase 3. Phases 6 → 7. The rig before 6.
- **Parallelizable after Phase 0:** Phases 1, 2, 4, 5 have no hard ordering between them — sequence them by your interest and energy, bounded only by being one developer. Phase 1 + Phase 2 together give the most satisfying early system (a working loop with persistent state), so they're the natural first pair.
- **Recommended order if going linearly:** 0 → 1 → 2 → 3 → 5 → 4 → (rig) → 6 → 7. Security scaffolding (4) lands late-but-before-rig deliberately: it has the most value once executors are about to get real tool access, and building it against the stand-in first is fine.

---

## 6. What the rig delay actually costs you

Nothing structural. The only deferred deliverable is the **cost-economics measurement** (Phase 7), and the only deferred *quality* is final throughput tuning. You can demo a fully working Hermes-Claunker — phone dispatch, three-model adversarial loop, persistent orchestration state, live Kanbantt board, red-hat/white-hat security loop, enforced downtime — entirely on cloud + current hardware before the rig ships. When it arrives, the system gets *cheaper and faster*, not *more capable*.

This is the cleanest possible position for a hardware-dependent project: hardware on the cost/perf axis, never the capability axis.

---

## 7. Immediate next action

Phase 0, step one: install Hermes on whatever machine you have now and get a single Discord-dispatched task running with Claude as parent and a hosted stand-in as executor. That one loop validates the entire chassis decision from Foundation 04 and unblocks Phases 1–5. Everything else is downstream of that working.
