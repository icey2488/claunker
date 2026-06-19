# Claunker Foundations — Index & Map (v3)

**Purpose:** One-page orientation across the foundation documents. Hand this alongside any single doc when dispatching to Claude Code or Gemini so the reader knows where the piece fits.
**Date:** 2026-06-12 · **Revision:** v3 — adds the Build Roadmap (execution plan). v2 added Document 00 (reconciliation), led with the North Star, resolved terminology.

---

## North Star

**Maximize time with the people you care about.** Not productivity, not throughput, not velocity — time. Claunker removes friction from execution so execution takes less of you, and the hours reclaimed go back to life, not back into more work. Every architectural decision across these documents exists in service of that one thing. (Source: Document 01.)

## The one-sentence architecture thesis

**Hermes Agent is the chassis; Claunker is the brain** — the heterogeneous, adversarial orchestration and security layer that Hermes does not have and is not building, composed on top using Hermes's own plugin, profile, skill, cron, and approval-gate surfaces.

---

## The stack at a glance

```
  00  RECONCILIATION — read first. The lens that makes the
      pre-Hermes and post-Hermes docs read as one corpus.
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  01  VISION — North Star, why Claunker exists, role stack,   │
│      MCP orchestration spine, local-inference economics      │
│                          │                                   │
│                          ▼                                   │
│  04  HERMES COMPOSITION — the build-vs-compose decision.     │
│      Chassis = Hermes. Brain = Claunker.                     │
│      Architect (Claude) → Executors (Ollama) → Judge (Gemini)│
│      ├── 02  KANBANTT MCP/PROVIDER SPEC — task board as      │
│      │       orchestration surface, connects via MCP client  │
│      ├── 03  DOWNTIME BOUNDARIES — quiet hours & check-ins;  │
│      │       mode logic Claunker-side, timer/delivery Hermes │
│      └── 05  SECURITY LOOP — red-hat / white-hat.            │
│              Controls testing the controls.                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Document register

| # | Title | Role | Status | Key dependency |
|---|---|---|---|---|
| 00 | Corpus Reconciliation & Terminology | Reading frame; bridges pre/post-Hermes eras without amending originals | Reading frame | Read before all |
| 01 | Vision | North Star; why Claunker exists; multi-model role stack; MCP spine; local-economics thesis | Foundational (2026-06-06); infra sections re-framed by 00/04 | — |
| 02 | Kanbantt MCP / Provider Spec | Task-board-driven dispatch; the board as a tool the architect reads/writes | Spec (2026-06-06) | Connects via Hermes MCP client to the Claunker MCP server (04, Phase 3) |
| 03 | Intentional Downtime Boundaries | Quiet hours, scheduled check-ins, restraint on always-on operation | Policy (2026-06-06) | Mode logic Claunker-side; timer/delivery on Hermes cron + gateway (04, Phase 2) |
| 04 | Hermes Composition | The hinge document. Decision to compose on Hermes; role stack mapped to Hermes primitives | Decision made (Option 3) | Hermes chassis |
| 05 | Security Loop (Red-Hat / White-Hat) | Adaptive security function; sandboxed attacker + write-blocked remediator | Design | Prereq: 04 |
| — | Build Roadmap | The execution plan: 8 phases, rig-dependency tagged, executor-substitution strategy | Active build plan | Foundations 00–05 |

---

## Terminology (resolved in Document 00)

The brand stays whole; the architecture gained a named substrate. Disambiguate only when it matters:

- **Claunker** (unqualified) — the whole platform the operator experiences. The sense used throughout 01–03. Default usage.
- **Claunker layer** / the brain — the differentiated logic on the chassis: orchestration skill, `judge_verdict` + `draft_policy_diff` plugins, the MCP orchestration-state server, config, the two logs.
- **Hermes chassis** — the inherited runtime: gateway, sessions, memory, cron, security floor, terminal backends.

01's "Claunker is the orchestration layer" is true at the platform level; 04's "Claunker becomes the brain" means the *layer* specifically. Different zoom levels, both correct.

---

## What each layer owns

**Hermes (the chassis) supplies:** gateway + 20 platform adapters (Discord, Telegram, …), single `AIAgent` core, SQLite+FTS5 sessions, bounded prompt-cache-stable memory, agentskills.io skills, first-class cron agent tasks, MCP client, 6 terminal backends, and the deterministic security floor (approval modes, hardline blocklist, DM pairing, container hardening, SSRF validation, profile isolation).

**Claunker (the brain) builds — small and bounded:**
- `judge_verdict` plugin (04) — Gemini adjudication of executor output
- `draft_policy_diff` plugin (05) — write-blocked, layer-routed remediation drafts
- Orchestration skill (04) — the architect → executor → judge protocol
- Security orchestration skill + `redteam` profile (05) — the red-hat/white-hat loop
- The **MCP orchestration-state server** (01/02) — Project/Task/Artifact/Escalation spine; what Kanbantt connects to. *Not* replaced by Hermes session storage; they hold different state.
- Downtime mode logic + override audit log (03) — layered on Hermes cron
- config.yaml: Claude parent, `delegation.model` → Ollama, Discord adapter, cadence
- Two append-only logs (05): finding log + policy diff log

Everything else is inherited configuration, not code.

---

## Cross-cutting principles (true across all docs)

1. **Compose, don't compete.** Anything Hermes ships is a dependency, not a rebuild target. All Claunker logic lives in the plugin / skill / config / MCP-server surfaces — never patch Hermes core. Those surfaces are the stable contract against upstream velocity.
2. **Heterogeneous adversarial loops are the differentiation.** Architect/executor/judge (04) and red-hat/white-hat (05) are the same shape: decorrelated models checking each other. That's the thing Hermes structurally lacks.
3. **Assert the gate, not the model.** A control that only holds because an LLM chose well lacks a deterministic gate. Applies to the judge loop and the security loop alike.
4. **Separation of duties.** No agent both proposes and commits to its own governing rules. Maker-checker, with you on the policy-tier sign-off — partly inherited via Hermes `approvals.mode: manual`.
5. **Proportional controls.** The architecture must not outgrow the thing it controls. Cheap everyday workhorses (regression set, architect self-accept on low-risk) carry the load; expensive deep audits (generative red-teamer, full Gemini adjudication) run on a slow drumbeat. Never train the operator to dismiss alerts.
6. **Compound institutional memory.** An automated team should get smarter the way a senior engineer carries context — remembering why the last approach failed. Preserving the corpus byte-for-byte (00) is itself an instance of this.
7. **DWYSYWD.** Do What You Said You Would Do — extended to the system doing what *you* said it would. Every doc closes on a deterministic Definition of Done.

---

## Reading order

- **New to the stack:** 00 → 01 → 04 → 05, then 02 and 03 as the integration details.
- **Building it:** start with the Build Roadmap, which sequences the work across all foundations and tags rig dependencies.
- **Implementing the loop:** 04 (Phase 0 spike) → 04 (Phases 1–2) → 05 → 04 Phase 3 (Kanbantt).
- **Reviewing security only:** 05, with 04 §3 as the topology it sits on.

---

## Open threads (not yet a document)

- ~~Project-name continuity~~ — **resolved in Document 00** (terminology section).
- Mixed executor models (coder vs. writer) — deferred in 04 §5 until it actually hurts.
- Judge/red-team triage thresholds — defined in skills, but exact severity cutoffs are tuning to be set empirically in Phase 1.
- Corpus format uniformity — 01–03 are PDFs, 00 and 04–06 are markdown/Docs. Cosmetic; unify only if a single-format hand-off to a tool requires it.
