# Claunker Foundation Document 00 — Corpus Reconciliation & Terminology

**Status:** Reading frame — read before 01–05
**Date:** 2026-06-12
**Role:** This is an adjusting entry, not a restatement. Documents 01–03 are preserved exactly as authored on 2026-06-06. Nothing in them is edited. This note records how the Hermes decision (Document 04, 2026-06-12) changes the way they should be read, and reconciles the terminology drift that decision introduced.

---

## 1. Why this note exists

Claunker was originally conceived as a **free-standing system** — its own gateway, its own session/state store, its own scheduler, its own messaging intake. Documents 01–03 were written under that assumption and are correct as of their authorship.

On 2026-06-12, evaluation of Nous Research's Hermes Agent (MIT) led to a deliberate pivot: **compose on Hermes as a chassis rather than rebuild its infrastructure** (Document 04). This saves substantial development work and lets Claunker focus exclusively on what differentiates it. The pivot changed the *build strategy*. It did **not** change the mission, the role stack, the orchestration-state model, or the human-boundary commitments.

The result is a terminology gap: 01–03 use "Claunker" to mean the whole system; 04–05 narrowed it to mean the layer built on the chassis. This note closes that gap without amending the originals.

---

## 2. What is preserved, unchanged

These are load-bearing and survive the pivot intact. Where 01–03 describe them, those descriptions remain authoritative.

| Concept | Source | Status after pivot |
|---|---|---|
| **North Star — maximize time with the people you care about** | 01 | Unchanged. The reason the whole thing exists. |
| **Compound institutional memory** (an automated team that gets smarter like a senior engineer carries context) | 01 | Unchanged as the design premise. Hermes's bounded memory + FTS5 session search are *how* it's now realized, not a replacement for the goal. |
| **Role stack** — Commander (Erick), Architect/Reviewer (Claude), Judge (Gemini), Executor (Ollama), Orchestrator (Claunker); Opus as operator-invoked fresh eyes | 01 | Unchanged. 04 maps each role onto a Hermes primitive without altering the roles themselves. |
| **Routine tasks never reach the operator; only ambiguity/high-stakes escalate** | 01 | Unchanged governing principle. |
| **MCP orchestration-state spine** (Project / Task / Artifact / Escalation) | 01, 02 | Unchanged and clarified — see §4. This remains Claunker's, not Hermes's. |
| **Kanbantt as a generic MCP-compatible board** with no hard Claunker dependency | 02 | Unchanged. 04 Phase 3 connects it via Hermes's MCP *client*; the server it connects to is still the Claunker MCP server from 01/02. |
| **Downtime semantics** — buffer / block / urgent-only modes, schedule entity, escalation buffering, override audit log, right-to-disconnect posture | 03 | Unchanged as policy. 04 implements the *scheduling mechanism* on Hermes cron + gateway, but the mode logic and override log are Claunker-side. See §3 caveat. |

---

## 3. What changed: infrastructure ownership only

The pivot moved one thing — **who builds the plumbing** — from Claunker to Hermes. The differentiation stayed home.

| 01–03 assumed Claunker would build… | Now inherited from Hermes chassis | Net effect |
|---|---|---|
| Discord intake / messaging gateway | Gateway + 20 platform adapters | Gains Telegram, Signal, etc. for free |
| Session / conversation state store | SQLite + FTS5 sessions with lineage | Replaced by a more mature implementation |
| Scheduler for downtime/cron | First-class cron agent tasks + delivery | Mechanism inherited; **mode logic still Claunker** (caveat below) |
| Agent runtime / tool dispatch | Single `AIAgent` core, 70+ tools, 6 backends | Replaced wholesale |
| Knowledge persistence plumbing | Bounded MEMORY.md/USER.md, prompt-cache-stable | Mechanism inherited; what-to-persist policy still Claunker |
| Security floor | Approval modes, hardline blocklist, DM pairing, container hardening | Inherited as the deterministic floor under the red-hat/white-hat loop (05) |

**Caveat on downtime (Document 03):** Hermes cron provides scheduling and `cron_mode: deny`, but it does **not** natively model 03's three-mode semantics (buffer/block/urgent-only), escalation buffering with a "Held — outside active hours" state, or the override audit log. Those remain Claunker logic layered on top of Hermes cron + gateway. Document 03's spec is therefore still the source of truth for *behavior*; Hermes only supplies the timer and the delivery channel. This should be made explicit when 03's downtime work is scheduled (04 Phase 2).

---

## 4. Terminology resolution

The word "Claunker" does not need to fracture. The brand stays whole; the architecture just gained a named substrate. Use this precision vocabulary when disambiguation matters:

| Term | Meaning | Use when |
|---|---|---|
| **Claunker** (unqualified) | The whole platform the operator experiences — intake, orchestration, the board, the agents working on your behalf. The sense used throughout 01–03. | Default. Talking about the product, the mission, the operator's experience. |
| **Claunker layer** (or **the brain**) | The differentiated logic composed on the chassis: orchestration skill, `judge_verdict` + `draft_policy_diff` plugins, the MCP orchestration-state server, config, the two logs. | Architecture discussions where the line between inherited and built matters (04, 05). |
| **Hermes chassis** | The inherited runtime: gateway, sessions, memory, cron, security floor, terminal backends. | Naming what Claunker no longer builds itself. |

So Document 01's sentence "Claunker is the orchestration layer between mobile and the local machine" is still true at the platform level. When 04 says "Claunker becomes the brain," it means the *Claunker layer* specifically. Both are correct; they operate at different zoom levels.

**One clarification 04 left implicit, resolved here:** Document 01 says "Claunker exposes its internal state as an MCP server" and "the MCP schema is the spine." Hermes is an MCP *client*. These compose without conflict — Hermes (client) connects to the Claunker MCP orchestration-state server (server), which is the same server Kanbantt's MCPProvider connects to (02). The Project/Task/Artifact/Escalation spine is **not** replaced by Hermes's session storage; the two hold different things. Hermes sessions hold conversation/agent state; the Claunker MCP server holds orchestration-domain state. This is the cleanest reading and it makes 01, 02, and 04 mutually consistent.

---

## 5. How to read the corpus now

1. **Documents 01–03** are the *why* and the *domain model* — mission, role stack, MCP spine, board, human boundaries. Read them as authoritative on intent and behavior. Mentally substitute "the infrastructure Claunker provides" with "the infrastructure the chassis provides" wherever they describe gateway/session/scheduler plumbing; everything else stands as written.
2. **Document 04** is the hinge — the build-vs-compose decision and the mapping of 01's role stack onto Hermes primitives.
3. **Document 05** is the adaptive security function on top of 04's chassis.
4. **The index** is the one-page map across all of it.
5. **This note (00)** is the lens that makes 01–03 and 04–05 read as one coherent corpus rather than two eras in tension.

---

## 6. Provenance statement

Documents 01–03 (`claunker-foundation.md`, `kanbantt-provider-spec.md`, `claunker-downtime-spec.md`, all v0.1.0, 2026-06-06, authored by Erick M. Gonzales) are retained in the corpus byte-for-byte as the historical record of the free-standing design. The Hermes pivot is documented forward in 04, 05, and this note — never by editing the originals. This preserves the audit trail of how and why the architecture evolved, which is itself an instance of the compounding-institutional-memory principle the corpus is built on.
