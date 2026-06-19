# Claunker Foundation Document 04 — Composing on Hermes Agent

**Status:** Decision made — Compose (Option 3)
**Date:** 2026-06-12
**Supersedes:** Portions of Foundation 01 (Vision) covering gateway, session, and scheduling infrastructure. The multi-model orchestration thesis in Foundation 01 is unchanged and elevated.
**Companions:** Foundation 02 (Kanbantt MCP/Provider Spec), Foundation 03 (Intentional Downtime Boundaries)

---

## 1. Decision

Claunker stops building its own gateway, session, memory, and scheduling infrastructure. **Hermes Agent (NousResearch, MIT) becomes the chassis. Claunker becomes the brain** — the heterogeneous adversarial multi-model orchestration layer that Hermes does not have and is not building.

The three options considered:

| Option | Description | Verdict |
|---|---|---|
| 1. Compete | Keep building Claunker's gateway/sessions/cron from scratch | Rejected — months of work to reach Hermes's 2024 feature set; zero differentiation gained |
| 2. Capitulate | Run stock Hermes pointed at the Ollama rig | Rejected — abandons the multi-model thesis, which is the entire point |
| 3. **Compose** | Hermes chassis + Claunker orchestration logic as plugin/skills/config | **Adopted** |

### Why compose wins

Hermes ships, today: a single `AIAgent` core serving CLI/gateway/cron/IDE; 20 platform adapters including Discord and Telegram; SQLite+FTS5 session storage with lineage tracking; bounded curated memory with prompt-cache-stable injection; an agentskills.io-compatible skills system with autonomous skill creation; first-class cron agent tasks with platform delivery; a security model (approval modes, hardline blocklist, DM pairing, container hardening); and 6 terminal backends. ~25,000 tests, 200+ contributors, MIT licensed.

None of that is Claunker's differentiation. All of it is Claunker's dependency list. Rebuilding it is undifferentiated labor.

What Hermes does **not** have — and what Claunker keeps as its identity:

1. **The adversarial multi-model hierarchy.** Hermes is single-active-model with switchable providers. There is no proposer → reviewer → judge loop, no heterogeneous quality gate. Claunker's Ollama-executor / Claude-architect / Gemini-judge design has no counterpart.
2. **Local-inference-first economics.** Claunker's cost architecture — burn free local tokens for execution volume, spend paid tokens only on architecture and judgment — is not encoded anywhere in Hermes.
3. **Kanbantt as orchestration surface.** Task-board-driven dispatch (Foundation 02) remains unique to this stack.

---

## 2. Concept Mapping — Claunker → Hermes

| Claunker concept (Foundation 01) | Hermes mechanism | Notes |
|---|---|---|
| Discord dispatch from mobile | Gateway + Discord adapter | Also gains Telegram, Signal, etc. for free |
| GCP e2-micro relay → local rig | **Retired.** Gateway runs directly on the rig (or stays on the VPS with SSH terminal backend into the rig) | One less hop, one less component to maintain |
| Claude as architect/reviewer | **Parent agent** — gateway/CLI profile configured with Claude as provider | |
| Ollama as executor | **Subagent model override** — `delegation.model` + `delegation.provider` in config.yaml routes all `delegate_task` children to local Ollama | Confirmed supported config, not a hack |
| Gemini as judge | Plugin tool (`judge_verdict`) or auxiliary-client pattern calling Gemini API | See §3.3 — the one genuinely custom build |
| Parallel instance scaling (Amdahl's law work) | `delegate_task` batch mode — 3 concurrent default, configurable, no hard ceiling; `max_spawn_depth` for orchestrator trees | Plus the tool-RPC scripting pattern collapses serial tool-call chains into single turns |
| Intentional downtime boundaries (Foundation 03) | Cron jobs + gateway config | Quiet hours and scheduled check-ins become configuration, not architecture |
| Claunker persona/behavior | SOUL.md context file + USER.md/MEMORY.md | Migration target for existing Claunker prompt material |
| Task protocols and runbooks | Hermes skills (agentskills.io format) | Each skill auto-becomes a slash command on every platform |

---

## 3. Composition Architecture

### 3.1 Topology

```
Phone (Discord / Telegram)
        │
        ▼
Hermes Gateway  ──────────────  runs on local rig (9800X3D + RTX 3080)
        │                       or on $5 VPS w/ SSH backend into rig
        ▼
Parent AIAgent ── provider: Anthropic (Claude)        ← ARCHITECT
        │
        ├── delegate_task ──▶ Subagent(s) ── provider: Ollama (local)   ← EXECUTORS
        │                        (parallel batch, isolated context,
        │                         restricted toolsets, fresh convo)
        │
        └── judge_verdict ──▶ Gemini API call          ← JUDGE
                                 (plugin tool, see §3.3)
```

**Critical mechanic to design around:** subagents start with *zero* parent context. The architect must pass complete task specs in `goal` + `context` fields. This is a feature for Claunker — it forces the architect to produce well-specified work orders, which is exactly the proposer-reviewer discipline Foundation 01 calls for.

### 3.2 The adversarial loop, expressed in Hermes primitives

1. **Dispatch:** Erick sends a task via Discord. Gateway routes to parent (Claude).
2. **Decompose:** Claude-architect breaks the task into self-contained work orders.
3. **Execute:** `delegate_task` batch fans out to Ollama subagents on the rig. Only structured summaries return to the parent's context (token-cheap by design).
4. **Review:** Claude-architect reviews summaries + artifacts (files on shared backend persist across subagents — the persistent Docker container or local backend makes executor output directly inspectable).
5. **Judge:** Claude calls `judge_verdict` (custom plugin tool) → Gemini renders accept / revise / escalate with rationale.
6. **Iterate or deliver:** Revisions re-dispatch to executors; acceptance delivers result back through the gateway to Discord.

> **Concurrency collision on the shared backend — verify before relying on step 4 (Gemini hostile review).** The `delegate_task` doc confirms each child gets isolated *context* and its own *terminal session*, but does not confirm filesystem isolation, and the default `cwd` is shared. So concurrent subagents that both write `./build_output.json` can clobber each other; the architect then reads a file that looks complete and approves a corrupted merge — state corruption presenting as green. This is **verify-first, not yet a settled fix**: before relying on parallel batch with file-writing children (Phase 2 build, Phase 6 at scale), confirm the installed Hermes build's actual filesystem semantics for concurrent children. If children share a writable `cwd`, the required pattern is **per-child isolated scratch directories or distinct Docker volumes, merged only at the architect's review layer, never at the execution layer**. Until verified, treat file-writing parallel batches as unsafe and either serialize them or give each child a distinct output path. Open question logged, not an asserted isolation model.

The loop logic itself lives in a **Claunker orchestration skill** — a SKILL.md encoding the protocol above (when to fan out, what a complete work order contains, judge criteria, revision limits). Skills are agent-readable procedure, so the loop is inspectable and editable without touching code.

### 3.3 The custom build surface (small, by design)

Only three things get written:

1. **`judge_verdict` plugin tool** (~100–200 lines Python). Registered via `~/.hermes/plugins/`. Takes (task spec, executor output, acceptance criteria) → calls Gemini → returns structured verdict JSON. This is the only net-new code with real logic.
2. **Claunker orchestration skill** (SKILL.md, no code). The protocol document for the loop in §3.2. Include Pitfalls and Verification sections per the Hermes skill template — they're institutionalized post-mortems.
3. **config.yaml** for the rig profile: Anthropic parent provider, `delegation.model`/`delegation.provider` → Ollama, `delegation.child_timeout_seconds` tuned low for fast local models, concurrency limit sized to the 3080's batching headroom, Discord adapter + DM pairing.

Everything else — memory, sessions, cron, delivery, security — is inherited configuration.

### 3.4 Patterns to adopt regardless (the strip-mine list)

- **Frozen-snapshot memory:** memory injected once at session start, never mutated mid-session → preserves Anthropic prefix cache → directly reduces Claude API cost. Adopt as a hard rule in any Claunker-side prompting.
- **Bounded memory with overflow errors:** writes that exceed capacity fail loudly, forcing same-turn consolidation. No silent decay.
- **Tool-RPC scripting:** for pipelines with many deterministic steps, have the executor write one Python script that calls tools via RPC instead of N round-trips. This is the Amdahl's-law serial-overhead fix at the tool layer.
- **Progressive disclosure for skills:** list → full content → reference files. Keep Claunker protocols token-cheap until invoked.
- **Subagent timeout diagnostics:** zero-call timeouts dump config snapshot + credential trace to logs — adopt this observability standard for the judge tool too.

---

## 4. Migration Phases

**Phase 0 — Spike (one evening).** Install Hermes on the rig. Configure Discord adapter + DM pairing. Point parent at Claude, set `delegation.model` to an Ollama model. Run one real task end-to-end from the phone. Goal: validate the topology before committing.

**Phase 1 — Loop MVP.** Write the orchestration SKILL.md and the `judge_verdict` plugin. Run the full architect → executor → judge cycle on a known task (e.g., a Plainfeed or GallagioLoot maintenance item). Measure: paid tokens per task vs. the current Claunker prototype.

**Phase 2 — Decommission.** Migrate Claunker persona/prompt material into SOUL.md + memory files. Encode Foundation 03 downtime boundaries as cron + gateway config. Shut down the GCP e2-micro relay (cost savings, one less attack surface).

**Phase 3 — Kanbantt integration.** Foundation 02's MCP/provider spec connects via Hermes's MCP client — Kanbantt board state becomes a tool the architect can read/write, closing the loop between task board and dispatch.

---

## 5. Risks & Open Questions

- **Upstream velocity.** Hermes is moving fast (v0.x). Pin a version per phase; treat `hermes update` as a deliberate event, not automatic. The plugin and skill surfaces are the stable contract — keep all Claunker logic there, never patch Hermes core.
- **Single-select delegation model.** `delegation.model` is global, not per-task. If mixed executor models are needed later (e.g., a coder model and a writer model), options are: per-profile gateways, spawn-depth-2 orchestrator children, or upstream PR. Defer until it actually hurts.
- **Judge latency/cost.** Gemini-as-judge on every executor return may be overkill for trivial tasks. The orchestration skill should define a triage rule: architect self-accepts low-risk output, judge gates only substantive work. Note a permanent tax this incurs: because the judge *must* be a different model family (the decorrelation property), judge calls cannot share the architect's prompt cache and pay full input-token freight every time. This is the price of the safety, not a leak — if cost ever bites, the lever is batch-judging (one judge call adjudicating several small units against their criteria, amortizing the fixed framing), not trimming what gets judged.
- **Security posture.** Run executors under the hardened Docker backend, keep the hardline blocklist on, and enforce DM pairing on Discord. Anything reachable from a phone over Discord is internet-facing by definition; Hermes's security doc is the baseline, not the ceiling.
- **Project-name continuity.** "Claunker" now names the orchestration layer + skill + plugin, not the whole system. Worth a one-line update to Foundation 01's framing.

---

## 5.5 Known soundness gaps

Two structural blind spots in the loop, logged here so they are recognized rather than rediscovered. Neither is urgent at Phase 1; both are invisible precisely because every component reports green while they are active — the same silent-failure class the corpus is built to catch.

**SG-1 — Decomposition is unchecked.** The judge adjudicates executor output against the architect's *own* acceptance criteria. So the loop checks execution-against-spec but has no check on whether the *spec* was right. If the architect decomposes a task wrong, the executor does the wrong work correctly, and the judge passes it — because it was built to spec. A confidently-wrong architect produces a clean-looking loop solving the wrong problem, and nothing in the current design flags it.

SG-1 is not merely *a* blind spot — it is the highest instance of the *only* blind spot this corpus keeps finding: healthy and broken emit an identical surface signal (every component green), the house invariant already documented at four lower altitudes (RC-001: judge looks healthy regardless of which model produced the verdict; AH-2: gate looks effective regardless of whether the gate did the failing; AH-3: mount looks live regardless of whether it's stale; the plugin-registry duplicate-key bug: plugin looks enabled regardless of whether it registered). SG-1 is that same shape at the top of the stack: the loop looks correct regardless of whether the decomposition was correct. It is categorically harder than the others because every prior instance was closed by an *in-loop* checker with standing to adjudicate (the judge above the executor, the allowlist above the pin, the canary above the mount) — but the architect is the top of the loop, and every downstream checker reasons *within* the architect's criteria. The judge cannot catch a wrong decomposition because the judge was handed that decomposition as its ground truth.

The trap to name: **human-at-dispatch as the decomposition check decays exactly as Claunker succeeds.** Today, at the desk, you see the breakdown before it runs. But the North Star is you off living your life while intent fires from Discord async — and in that end-state you are no longer positioned to see the decomposition at all. Choosing "human-at-dispatch" as the answer chooses a guard that erodes as the system matures into the thing it is for. The decomposition check must survive the human leaving, not depend on the human staying.

**Resolution — the Gemini framing pass.** Extend the judge's mandate to a *pre-execution* framing check: before any executor runs on a substantive decomposition, the judge (already Gemini, already decorrelated from the Claude architect) answers *"do these acceptance criteria, if satisfied, actually satisfy the request?"* This reuses the independent family already paid for — one family, two jobs (frame-check early, output-check late) — without correlating architect and executor with each other. The known limit: it is a single adjudicator on framing, the same structure the output check already runs, so it is consistent rather than a new weakness. This is the cheap gate guarding the most expensive failure (the whole executor budget spent flawlessly building the wrong thing) — the proportional-controls case in its purest form.

> **The framing pass must judge against raw intent, not architect paraphrase (Gemini hostile review).** For the check to mean anything, Gemini must receive the **exact raw input string the operator sent through the gateway**, explicitly bypassing any architect summarization or translation. If the architect hands Gemini its *own* paraphrase of the request, a confidently-wrong architect summarizes the wrong problem, derives criteria for the wrong problem, and Gemini correctly confirms the criteria satisfy the (wrong) summary — the framing check validates against corrupted ground truth and passes a wrong decomposition with full green. The original operator message is the only valid ground truth for the framing pass; it is carried verbatim from the gateway to the judge, never via the architect's restatement.

> **The framing pass needs its own proof-of-life, separate from correctness.** Catching a mis-framed spec (above) is the pass working *when it fires*; it is a different assertion from the pass *provably firing at all*. A substantive dispatch where the framing pass silently never ran looks identical to one where it ran and passed — the same green-skip failure every other gate now guards against. So execution blocks on a recorded verdict: a substantive executor cannot start until a framing verdict for that dispatch is on record, and a substantive dispatch that reached execution with no recorded framing verdict halts as a caught failure. This is the execution-blocks-on-recorded-verdict pattern — the framing pass's RC-001, the last gate to get its canary.

**SG-2 — The triage gate is LLM discretion, not a deterministic rule.** The rule "self-accept low-risk, judge substantive" is decided by the architect — an LLM, under completion pressure that biases toward self-accepting. That is the "assert the gate, not the model" anti-pattern one level up: the gate that decides whether to invoke the gate is itself a model judgment.

**Resolution — one deterministic classifier, hung with both gates (and the schedule below).** Make triage deterministic on operation class, and give the classifier a richer output than judge/don't-judge: a **tier** that routes to `{self-accept | single judge | dual sign-off | human}`. Trivial classes (read-only summaries, lookups) skip both the SG-1 framing pass and the output judge. Substantive classes (multi-work-order, live-path-touching, security-surface, irreversible) get the framing pass *and* the output pass. The same classifier feeds the authorization schedule in §5.6. One deterministic classifier closes SG-1 (gates the framing pass), closes SG-2 (replaces discretion with class), and cuts judge calls on the trivial tail — three concerns, one mechanism. Concrete amendment to the `claunker-orchestration` SKILL.md triage rule; deferred until real workloads show where the class boundaries fall.

> **The determinism mirage — the classifier must never read intent (Gemini hostile review).** "Deterministic classifier" was a label carrying weight it had not earned, and it survived several review passes precisely *because* the reassuring word pre-empted scrutiny — the most dangerous failure class in this whole corpus: a flaw hidden by its own name, not by obscurity. The inputs to triage are natural-language tasks from Discord and LLM-drafted work orders, and **you cannot statically parse intent into a class.** Keyword matching is trivially injectable — a destructive write framed as *"please summarize…"* reads as `self-accept` and sails through with zero oversight. And using an LLM to map intent → tier *is* the held-its-own-gate sin (RC-001) wearing a determinism costume. So the rule is absolute: **the classifier operates on the invoked tool schemas, never on natural-language intent.** Tier is a hardcoded mapping of tool name → severity floor (`write_file` = high, `delete_*`/`*_permissions` = human-pinned, `read_file`/`search` = low), and a dispatch is evaluated by the **highest-tier tool it can reach** — not just the tools named in the top-level work order but the transitive set any subagent or generated script could invoke (a `self-accept` task that can spawn a shell which can call `write_file` is a `write_file` task). An LLM may *raise* a tier (the advisory-up-only scorecard), never map intent to one, and never lower the tool-derived floor.

The two gaps do not block the current loop, which is built and verified (Phase 0–1). They are logged as the honest soundness ledger and resolved by a single converging mechanism — the deterministic classifier of §5.6, which hangs the SG-1 framing pass, the SG-2 tier routing, and the authorization schedule off one floor.

---

## 5.6 Authorization schedule (dispatch impact tiers)

Ported from a control the operator has actually run: in accounting, electronic transfers above a fixed dollar line required two independent signatures — partner and chief admin — neither of them the person who initiated the transfer. Dual-control keyed to a *deterministic* threshold. That determinism is the whole point, and it is what this schedule preserves.

**The classifier output is a tier, not a yes/no.** The same deterministic classifier from SG-2 assigns each dispatch a tier that routes to one of:

| Tier | Routes to | Example classes |
|---|---|---|
| `self-accept` | no gate | read-only summaries, lookups |
| `single judge` | output judge (+ framing pass if substantive) | ordinary multi-step work, non-live writes |
| `dual sign-off` | two independent frontier signers (Gemini + GPT), then execute | live-path writes, external side effects, spend-equivalents |
| `human` | non-delegable operator approval | self-modification, irreversible external effects, changes to this schedule |

**Severity is floored deterministically; models may only raise it, never lower it.** Operation classes pin a *minimum* tier off deterministic facts — does it touch a governing file, is it irreversible, does it have external side effects. A scorecard model can add nuance *upward* (promote a medium to a high) but **cannot demote below the floor**. Self-modification is floor-pinned to `human` regardless of any model's score. This is the AH-5 move again: don't ask the LLM to resist mis-scoring, remove its capacity to score below the floor. A scorer that under-rates a dispatch returns a valid-looking low number regardless of whether the number is right — the same identical-surface-signal failure — so the scorecard is advisory-up-only and never sits below a deterministic class floor.

**Dual sign-off rules (where the tier applies):**
- *Proposer-exclusion.* The model that drafted the dispatch cannot sign it (§2.4 maker-checker: no agent both proposes and commits). On a Claude-proposed dispatch the two signers are Gemini plus a third family, neither being the proposing instance. Partner + chief admin, where neither is the initiator.
- *Inconclusive is a fail, not a pass.* A signer that errors, abstains, or times out counts as a failed sign-off and routes to the human — never silently as approval. The AH-2 lesson ported once more: a non-answer is not a yes, an auth/network/timeout outcome is not a refusal-that-passed. "Either sign-off fails → route to human" includes "either sign-off did not clearly succeed."

**The recursive case — the meta-turtle.** A dispatch that modifies the severity scorer, the approval matrix, the deterministic floors, or this schedule is `human`-tier *by definition*, non-delegable. If changing-what-requires-approval were itself a low-severity config tweak, a wrong or compromised architect could route "lower the threshold" past the gate as routine work and disable the entire control from inside — AH-1 (attacker drafts a weakened gate into your sign-off queue) showing up in the *normal orchestration path*, not the security loop. The control that decides what needs approval must classify changes-to-itself as the thing that most needs approval. **And the human sign-off for a floor change must render the diff against current floors with an explicit "this reduces a control" flag** — never an opaque "approve config change." Otherwise the non-delegable gate just relocates the rubber-stamp to the operator, the same way AH-1's fix had to make the reduction legible at the sign-off surface, not merely route it there. Redefining maximum severity is itself a maximum-severity act, presented legibly.

**Keep the max class narrow and real (Foundation 05 §6).** The phone buzzing for approval stays meaningful only because it buzzes solely for genuine maximum-severity acts. The moment the `human` class creeps wide enough to catch routine work, you are rubber-stamping again — the dismissal failure the control exists to prevent. The deterministic floors keep this honest: a tight, enumerated set of always-human classes, not a model deciding when to bother you. In finance the dual-sig line was a dollar amount precisely so the approvers' attention stayed scarce. Set yours the same way.

**Executor pin allowlist — mirror the judge's gate onto delegation (FT-007, Phase 2).** The judge fails closed via `allowed_providers: [gemini]` / `allowed_models: [gemini-2.5-flash]`; the executor pin has no equivalent. FT-007 established (source + empirical) that delegation *errors* on an unresolvable pin — it never silently falls back to the parent — so the loop is gated against *accidental* correlation. But the executor's only check is "does this provider+model exist," not "is it allowed": a deliberate `delegation.provider: anthropic` + `delegation.model: claude-sonnet-4-6` would run silently on the architect's model, fully correlated, and nothing would stop it. That is the judge-gated/executor-ungated asymmetry, and it is the RC-001 shape one notch deeper — not "does it fall back on failure" (no) but "can it be pointed at the parent and run happily" (yes). The fix is the judge's own control applied one seat over: an `allowed_providers`/`allowed_models` allowlist under `delegation`, fail-closed, so a correlated-but-valid pin raises rather than runs. Cheap, already-patterned, and it makes executor decorrelation gate-asserted rather than trust-asserted. Phase 2, alongside the classifier (same authorization layer); not built at Phase 1.

**Third signer family = GPT (OpenAI, direct API) — ratified 2026-06-15.** Proposer is always Claude (the fixed architect), so the two signers are Gemini + GPT and neither is the proposer; no rotation logic is needed at this scale. Three load-bearing constraints:
- *Vendor is not family.* Copilot and Azure OpenAI are GPT under the hood. The third seat is the OpenAI API hit directly. "GPT family" is exactly one slot; never wire a Microsoft surface as a separate signer and call it independence.
- *Do not promote the executor family into the signer seat.* It is free and a different family, which makes it tempting, and it is decorrelation theater: a model strong enough to execute a well-specified order is not necessarily strong enough to adjudicate a live-path write as a peer, and open-weight models carry frontier-distillation correlation. A signer too weak to catch the bad dispatch emits "looks fine" regardless of whether it checked — FT-009/RC-001 one seat over. (This is precisely why the *scorer*, below, can be a free family and the *signer* cannot: the seats have different stakes.)
- *Cost is not a reason to cheap out here.* The full-freight token tax (a third signer shares no prompt cache) is real but barely bites: self-modification is already floor-pinned to `human`, so the seat fires only on live-path / external-effect / spend-equivalent writes. Low volume times full freight is a small absolute bill; the cache tax that hurts at judge-on-everything volume does not apply to a low-volume high-stakes tier.

**Known limit — the decorrelation ceiling (record it so nobody reads "dual sign-off" as "two uncorrelated oracles").** "Two independent labs" oversells the independence. Frontier families converge through shared benchmarks, similar RLHF, and probable cross-training. Gemini + GPT is the *best available* decorrelation, not orthogonal oracles. This is the same honesty SG-1 applies to its single-adjudicator limit: name the ceiling rather than imply the property is stronger than it is.

**The output judge for a dual-sign dispatch is GPT, not Gemini (item 2b) — with a three-family bookending ceiling.** Within a single dual-sign dispatch Gemini already touches the SG-1 framing pass (pre-execution). Routing the *output* judge to GPT decorrelates the post-execution check from that heaviest Gemini touch. But this cannot fully escape bookending: a dual-sign dispatch consumes both non-proposer families as signers (Gemini + GPT), so whichever family judges the output necessarily also co-signed the plan. The mitigation is that the *other* signer approved the plan independently; the honest statement is that this is a three-family ceiling (the two signer seats exhaust the two non-proposer families), the same class of limit as the decorrelation ceiling above — not a clean win. Escaping it entirely needs a fourth frontier family, deferred as not worth the cost at this scale.

**Materiality scorecard — the qualitative-up-only layer over the floor (ratified 2026-06-15).** The deterministic tool-schema floor is the *quantitative* materiality layer (a calculation off what the dispatch can reach). A score band is the *qualitative* layer, on the audit-materiality model: quantitative materiality is deterministic; qualitative materiality is the override that can only push **up** (a small item promoted to material for fraud / related-party reasons), never down (you never call an over-threshold item immaterial on a good feeling). Bands:

| Score | Band contributes |
|---|---|
| 0–39 | nothing — floor governs alone |
| 40–74 | `single judge` |
| 75–89 | `dual sign-off` |
| 90–100 | `human` |

The one load-bearing rule: **the score composes with the floor, it never replaces it.** `final_tier = max(deterministic_floor, score_band_tier)`. The band may ratchet a dispatch *up*; it may never pull anything *down*. A self-modification dispatch scored 30 still routes to `human` because the floor pins it and the score has no demote authority. Letting the score *determine* the tier would make a number the classifier and re-introduce the determinism mirage SG-2 caught. Keep the rubric loose if wanted, but only the scorecard layer is loose; the floor stays hardcoded and hard, and "not holding fast to the rubric" must never leak downward into the floor. Keep the bands coarse (three up-only steps is a ratchet; ten bands is a scoring function pretending to be a ratchet) and treat the boundaries 40/75/90 as instrumented hypotheses, not constants — retune from what actually fires.

**The 90–100 → `human` band is a SECONDARY safety valve, not the primary human router.** The primary router to the operator stays the enumerated deterministic floor (self-modification, schedule changes, irreversible external effects) — those reach the phone because of *what they are*, not because of a number. If a *score* alone could promote anything to `human`, then a model assigning 92 is a model deciding when to bother the operator — the "keep the max class narrow and real" rule and the Foundation 05 dismissal-fatigue failure, walking in through the scorecard. So instrument it: most of what hits the phone should arrive via the floor, not the band. If the band becomes the main path to the operator, that is dismissal fatigue accruing and a retune signal — and, because the dual-sign tier's cost scales with the same band-firing rate, it is simultaneously the cost-control signal. One instrumented metric (band-firing rate) is the canary for both dismissal fatigue and GPT spend.

**The scorer is a free, non-big-three family — decorrelation budget matched to the seat (ratified 2026-06-15).** Signing is authorization (the act that lets a live-path write proceed) and is the material seat; scoring only nudges how often that authorization is invoked, is advisory-up-only, and is floor-bounded by `max(floor, band)`. A seat whose failure cannot drop below the floor, and whose only harmful direction (over-scoring → dismissal fatigue) is already canaried, does not need a frontier model. So the scorer rides the free-tier lane the executor already uses (a free non-big-three open-weight family, ideally a *different* free family than the executor's for cheap mutual decorrelation, and reasoning-visible so its rationale feeds FT-005's log-the-attribution provenance; the specific model is selected at wiring time, not pinned here). This is *more* decorrelated than having GPT score, not less: a separate family touches only the scoring gate, leaving the score decorrelated from sign, frame, and judge alike, and it correctly puts the least-reliable infrastructure behind the most-failure-tolerant role. By SG-1's own logic the scorer must also be independent of Claude (the proposer cannot self-assess the materiality of work it is motivated to run) and by SG-2's logic independent of Gemini (which would otherwise decide whether to invoke itself). Three documented limits, all tolerable *because the seat is advisory*: (1) a weaker scorer's qualitative net has holes — subtle cases (a fraud-flavored related-party transfer that looks routine) are missed and degrade to the floor's tier, which is why the design must be **floor-complete, score-as-bonus**: fully correct with the score absent, the score only an upward nudge, never a control the design leans on; (2) distillation-correlation may make the scorer share frontier blind spots — disqualifying for a signer, tolerable for an advisory floor-bounded scorer; (3) **scorer-unavailable degrades to floor-governs** — no band, `max(floor, band)` resolves to the floor, the dispatch routes by the deterministic floor (FT-009 pre-applied: the unreliable free-tier seat is safe precisely because its absence fails toward the floor, never past it). To bound cost, the score runs only on dispatches the floor already placed at `single judge` or `dual sign-off` (the ratchet-able middle); `self-accept` reads and `human`-floored dispatches skip scoring (the former low-consequence by construction, the latter already maxed). The scorer and the GPT third signer are both unwired at Phase 1; the qualitative layer activates when they land.

**Deferred dependency (built-vs-designed honesty).** Neither the GPT third signer nor the free-family scorer is set up at Phase 1. Until the third signer is wired, the schedule degrades safely: **anything that would route to `dual sign-off` routes to `human` instead** — no model dual-sign is attempted on the strength of a provider that isn't there, and until then the 75–89 and 90–100 bands both resolve to `human` (the band distinction is latent). The tier is *designed, blocked on third-provider setup*, and must not assert itself as active before the family exists; marking it live before then would be the exact built-vs-verified gap this corpus refuses elsewhere. When GPT and the scorer land, each enters the FT-008 grant inventory as its own standing credential.

**Floor liveness — the schedule's own canary.** Every other layer has a liveness assertion (RC-001 for the judge pin, the AH-3 canary for the gate mount); the floors are the one altitude that lacked one. A floor config that reads stale, mis-parses, or loads a cached copy is mirror desync at the top of the stack — an always-`human` class silently classifies as auto-accept and the dispatch sails through with every component green. The recursive case catches a dispatch that *intends* to lower a floor; it does nothing about a floor that is silently not-in-force. Same wrong-reason-pass: the schedule looks enforced because dispatches get tiers, regardless of whether the tiers reflect the *live* floors. Without this you have built the control that decides what needs approval, but not the control that proves that control is awake.

The liveness control is **two distinct assertions, not one** — and collapsing them is itself the corpus's cardinal sin. The tempting single fix (a reserved synthetic test-class the canary promotes and watches route) checks only that *the synthetic path* works, regardless of whether *real* always-`human` classes are loaded — RC-001's trap one final altitude up: it proves the cheap thing while looking like it proved the expensive one, and (Gemini's own concern) injecting synthetic traffic into the live operator queue trains dismissal fatigue. So split:
- **Freshness check — deterministic, every dispatch, no synthetic traffic.** On each dispatch the classifier verifies the floor config it is reading is the live one: hash/mtime of the on-disk floor config compared against the loaded state, and a refuse-closed halt if they diverge. This catches stale/cached/mis-parsed floors on the real path with zero collateral escalation — it is a comparison, not an injected task.
- **Routing test — out-of-band, against a real always-`human` class, never in the live queue.** Separately, a test-harness run (not live Discord traffic) asserts that a genuine always-`human` operation deterministically routes to the operator. Run it in the regression set on every change to floors/classifier/schedule, the same cadence as the security loop's RC cases — not as synthetic injections into the operator's actual approval queue. This asserts the real classes route, not that a reserved sentinel does.

Together: the freshness check proves the floors loaded are the floors on disk; the routing test proves an always-`human` class reaches the human. Neither escalates real workloads, and neither is a sentinel that passes while the real classes are dark.

---

## 5.7 One-tap abort — the intra-execution safety primitive

Every control above gates what the system *does before and during* a dispatch. Almost nothing addresses how the operator *stops* a running dispatch from a phone, away from the rig. That is the missing half of a safety property already half-built: the SG-1 framing pass catches a wrong decomposition *before* execution; abort catches one the operator only realizes is wrong *during* execution. Pre- and intra-execution halves of the same property — and the architecture currently has only the pre- half.

For an always-on async system dispatched from Discord, "the wrong thing is running at 11pm and I am not at the desk" is a real scenario with, today, no clean answer. This is not a UX nicety; it is the operator-safety primitive for remote control of an autonomous loop.

**The requirement:** a one-tap abort reachable from the same surface the operator already has — a Discord command/button — that halts the in-flight dispatch deterministically: gateway SIGTERMs the executor container, with a `./scripts/kill-task` (or equivalent) fallback if the graceful stop does not take within a short window. Abort must not depend on the agent choosing to cooperate (the failure mode below).

**This is not yet built, and tonight's run proved its current absence empirically (FT-003).** The existing `/stop` does *not* cleanly cancel a task mid-tool-call — the gateway logged that the task "did not exit within 5s; unblocking dispatch and letting the task unwind in the background," leaving a zombie task running while the gateway accepted new work. So the current stop path is known-broken, not merely absent: a dispatch can keep executing after the operator told it to stop. Abort must therefore terminate at the *process/container* layer (SIGTERM the executor, kill the container), not rely on the agent's cooperative cancellation — because the agent mid-tool-call is exactly when cooperative cancellation fails. Build this as a Phase-2 priority; it is the highest-value item surfaced by the gateway confirmation.

## 5.8 Controls test suite — proof-of-life for the controls themselves

An acceptance criterion in §6 is a *claim* until a test fails when it is violated. Clauses (d), (e), (h), (j) and the §3.2 concurrency question all specify enforcement in prose — and prose-only enforcement is the shape-check this corpus exists to reject, one altitude up: the clause asserts the gate exists; only a red-on-violation test proves the gate still *bites* after the next edit. The controls need the same assert-don't-trust treatment the loop got from RC-001. This converts already-written acceptance criteria into executable assertions; it is finishing the engineering, not new design.

Required regression tests (run on every change to the orchestration skill, classifier, floors, or schedule — the same cadence as the security-loop RC cases):

- **Framing-pass liveness (clause j).** A substantive dispatch with the framing-verdict record removed must *halt* at execution, not proceed. Goes red if a skill refactor silently drops the execution-blocks-on-recorded-verdict check.
- **Framing-pass ground truth (clause d).** The framing pass receives the raw operator string; a test feeds a paraphrase-injecting path and asserts the raw string reached the judge.
- **Tier floor integrity (clause e).** A destructive-tool dispatch framed as a "summary" must tier by the reachable `write` tool, not the intent text; a scorecard attempting to demote below the tool-derived floor must fail to lower it.
- **Floor freshness (clause h).** An on-disk floor edit not reflected in loaded state must trigger the refuse-closed halt; the deterministic freshness check goes red on hash/mtime divergence.
- **Concurrency isolation (§3.2 open question, = FT-007's sibling).** Two parallel executors instructed to write the same filename must produce distinct artifacts in isolated scratch paths; the test asserts no clobber. Operationalizes the already-logged verify-first item.
- **Executor decorrelation (FT-007).** Two cases, post-resolution. (i) Invalid pin: a deliberately invalid `delegation.model` must cause the subagent to *error*, not fall back to the parent — verified closed by FT-007, kept as a regression guard. (ii) Correlated-but-valid pin: once the executor allowlist (§5.6) is built, a `delegation.provider: anthropic` / `model: claude-sonnet-4-6` pin must *raise* rather than run on the architect's model — the RC-001 negative test, executor edition. Until the allowlist exists, (ii) is a known-open gap, not a passing test; do not mark it green.

Each test is red-when-the-control-is-violated, not green-when-the-happy-path-works — the same distinction RC-001 drew between asserting provider attribution and asserting verdict shape.

---

## 5.9 Storage and isolation model

Storage routes by *kind*, not by one default location — code, orchestration state, and knowledge each want a different store, and the red-team sandbox wants active isolation. Status: a working model, not yet a locked decision; the diagram draws the durable tier as *proposed* until this section is ratified and the sync model is built.

**Three tiers, routed by kind:**

- **Code → GitHub, canonical.** Claude Code writes locally (low latency, the working tier); once a project is ready, commits push and GitHub *is* the source of truth. The push-discipline is a feature, not just a backup: code that is not pushed is not real, the same way the dual-sig dollar line kept approver attention scarce. The local working copy is a working copy, never the record.
- **Orchestration state (the spine) → Drive durable, local fast-read, with an explicit sync model.** Drive is the durable truth (better uptime, inherently backed up, already in the connector path). But "Drive for durability, local for latency" is not a location choice — it is a *sync model*, and choosing it naively re-introduces the exact local-cache-vs-durable-store hazard the Kanbantt convergent-merge core (`sync-merge.js`: canonicalize, CRDT merge, conflict copies, amplification guard) already solves. So the spine gets a local fast-read layer *backed by* Drive as durable truth, reusing the Kanbantt merge pattern — not an undefined "I read the local copy" cache, which is a desync waiting to happen and is exactly where FT-005's converging-or-churning question lives.
- **RAG → corpus source on Drive (durable), index built/cached locally (derived).** The cross-project knowledge index belongs in the shared durable tier — but the Drive-resident artifact is the *corpus it indexes* (docs, escalation→md→PDF artifacts), not the index itself. The index is a derived, locally-rebuilt cache, freshness-anchored to Drive truth. A stale index living on Drive would be a wrong-answer source with no liveness check (the floor-freshness failure shape again); a locally-rebuilt index from Drive truth has an obvious freshness anchor.

**Red-team sandbox isolation — two independent boundaries, defense in depth.** The Foundation 05 sandbox (red-hat profile, finding log, quarantined attack library) must be reachable by *nothing* in the live environment — that quarantine is the airgap AH-1/AH-2 depend on. A single isolation mechanism is a single point of failure, so the sandbox sits behind two independent boundaries, either of which failing alone does not open the airgap:

1. **Credential boundary.** The sandbox's storage (its own Drive folder) is reachable only by the sandbox's own scoped credential. No live Claude Code or Hermes identity holds that credential. Critically, the convenient default — "Claude Code writes locally and it pushes to Drive because it's a connector anyway" — must **not** extend to the sandbox folder: the auto-sync path is a path, and a path to the sandbox is a bridge across the airgap. Isolation is a credential scope, not a folder-naming convention.
2. **Host boundary.** The red-team Claunker runs on a *separate host* (a dedicated GCP machine), not the local rig. So even a fully compromised local environment cannot reach the sandbox: the local box holds neither the sandbox's Drive credential nor a network route to the GCP host. Credential failure is caught by host isolation; host failure is caught by credential scoping. (Status: host segregation is the best-practice end state and the operator is open to it; not yet a locked decision.)

This is the production-hardening posture: the component whose entire job is generating real attacks is isolated by two mechanisms, not one, and the manual setup to rope it off is treated as worth doing rather than approximated by a folder convention.

---

## 6. Definition of Done (DWYSYWD)
This document is satisfied when: (a) a task dispatched from Discord on mobile completes the full architect → executor → judge loop on the local rig with zero GCP involvement; (b) paid-token spend per task is measured and lower than the relay-era prototype; (c) Foundations 01–03 are annotated with what this document supersedes; (d) a substantive decomposition passes the SG-1 Gemini framing pass *before* executors run, the framing pass receives the raw operator gateway string (not an architect paraphrase), and a deliberately mis-framed decomposition (right execution, wrong spec) is caught — "loop solves the wrong problem correctly" is the failure it must flag; (e) tiers are assigned by a hardcoded tool-schema → floor mapping over the transitive reachable toolset, never by an LLM reading natural-language intent, and a scorecard model can raise but provably cannot demote below the tool-derived floor — a self-modification dispatch routes to `human` regardless of any model-assigned score, and a destructive write framed as a "summary" still tiers by the `write` tool it can reach; (f) a dispatch that would modify this schedule, the floors, or the approval matrix routes to `human` non-delegably and is presented with a diff naming *which* control changes and *by how much*, flagged when it reduces a control, never an opaque config approval; (g) while the third signer family (GPT) is unwired, every `dual sign-off`-tier dispatch routes to `human` rather than attempting a model dual-sign, and the materiality scorecard composes up-only as `final_tier = max(floor, band)` — a band score can ratchet a dispatch up but provably cannot pull it below the deterministic floor, the scorer (a free non-big-three family) runs only on the ratchet-able middle and degrades to floor-governs when unavailable, and the 90–100→`human` band is a secondary instrumented valve, not the primary human router (the floor is); (h) floor liveness is proven by two separate assertions — a deterministic per-dispatch freshness check (loaded floors match on-disk config by hash/mtime, refuse-closed on divergence, no synthetic traffic) and an out-of-band routing test against a real always-`human` class in the regression set (never injected into the live operator queue), so the absence of a control fails toward the human, not past them; (i) concurrent file-writing subagents are verified to use isolated scratch paths or distinct volumes before parallel batch is relied upon, merged only at the review layer; (j) the framing pass is proven *live*, not only correct — a substantive executor cannot start until a framing verdict for that dispatch is on record, and a substantive dispatch that reached execution with no recorded framing verdict is a caught, halting failure, not a silent skip (clause (d) asserts the pass judges the right thing; this asserts it provably ran); (k) one-tap abort from the operator's phone surface deterministically halts an in-flight dispatch at the container/process layer (SIGTERM + kill-task fallback), not via cooperative agent cancellation — verified by aborting a long-running dispatch and confirming the executor process is dead, not backgrounded (closes the FT-003 known-broken `/stop` path); (l) clauses (d), (e), (h), (j), the §3.2 concurrency question, and the FT-007 executor pin each have a red-on-violation regression test that fails when the control is removed — the controls have proof-of-life, not just prose; (m) standing tool-access grants are inventoried and intentional before Phase 4 (FT-008) — `.claude/` is gitignored per repo so per-machine grants stay local, the Hermes executor runs under the Docker backend (narrowing its grant from the operator's whole filesystem to container scope), and a single inventory records what each tool can read/write and confirms each grant was deliberate, not accreted; (n) storage routes by kind — code is canonical on GitHub (pushed, not just local), spine state is Drive-durable with a local fast-read layer governed by the Kanbantt convergent-merge model (not an undefined cache), and the RAG index is a locally-rebuilt cache derived from Drive-resident corpus truth; and the red-team sandbox is isolated by two independent boundaries — a scoped Drive credential no live identity holds (the auto-push-because-connector path explicitly excluded), and a separate host — so neither a credential leak nor a compromised local environment alone bridges the airgap. DWYSYWD includes the system doing what *you* said it would — including refusing to lower its own bar, proving the bar is awake, stopping when you say stop, knowing what can reach what, keeping the sandbox unreachable by two means not one, and never trusting a word it told itself.
