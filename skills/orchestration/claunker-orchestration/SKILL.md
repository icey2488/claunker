---
name: claunker-orchestration
description: >
  The architect -> executor -> judge protocol for the Claunker loop. Load this
  when acting as the architect: decomposing a dispatched task, fanning work out
  to executor subagents, and deciding what gets adjudicated by judge_verdict
  versus self-accepted. Defines the work-order contract and the triage rule.
version: 0.1.0
metadata:
  hermes:
    tags: [orchestration, delegation, multi-model, claunker]
    category: orchestration
    requires_toolsets: [claunker_judge]
---

# Claunker Orchestration

## When to Use
Load this whenever you are the **architect** handling a task dispatched through
the gateway (Discord) and the work is non-trivial enough to delegate. You are
Claude. You decompose and review; you do not execute the bulk yourself. Executor
subagents (local Ollama, or a stand-in during early phases) do the volume work.
A decorrelated judge (Gemini, via `judge_verdict`) adjudicates substantive
output. The operator (the human) decides only what genuinely needs human
judgment.

Skip delegation entirely for tasks you can answer correctly in one turn — a
quick question, a one-line fix, a lookup. Orchestration overhead is not free;
spend it only when fan-out or independent review earns its cost.

## Procedure

### 1. Decompose into self-contained work orders
Subagents start with **zero** of your context. Every work order you hand to
`delegate_task` must stand alone. A complete work order contains:

- **Goal** — one sentence, unambiguous, the single thing this unit produces.
- **Context** — everything the executor needs that it cannot see: file paths,
  the relevant constraints, prior decisions, the data contract. Paste it; do not
  reference "the thing we discussed."
- **Acceptance criteria** — explicit, checkable conditions for done. If you
  cannot write these, the unit is underspecified — refine it before dispatching.
  These are the SAME criteria you will pass to `judge_verdict`.
- **Risk tier** — `low` / `standard` / `high` (see triage rule below).

Decomposition forcing function: if a unit cannot be specified standalone, it is
too entangled — split it or pull it back to yourself.

### 2. Fan out
Dispatch independent units in parallel via `delegate_task` (batch). Keep units
independent so parallelism is real; sequence only genuine dependencies. Only
structured summaries return to your context — that is intentional and keeps your
context cheap. Shared artifacts (files) persist on the backend; inspect them
directly rather than asking the executor to echo large output back.

### 3. Triage — the rule that protects the loop's cost
For each returned unit, decide: **self-accept or judge?**

- **Self-accept (no judge call)** when ALL hold: risk tier is `low`, the output
  is directly verifiable by you in one look (it compiles / the diff is obviously
  correct / the answer is checkable), AND a silent error would be cheap to
  reverse. Trivial and low-stakes work does not earn a judge call.
- **Call `judge_verdict`** for substantive work: code changes of any real size,
  multi-step deliverables, anything touching a security surface or irreversible
  effect, anything where a confident-but-wrong executor would be costly. Pass the
  task spec, the acceptance criteria from step 1, the executor output, and the
  risk tier.

The judge has latency and token cost. Gating *everything* through it re-creates
the bottleneck the loop exists to avoid; gating *nothing* removes the
decorrelated check that catches confident errors. The tier system is how you
spend judgment proportionally. When unsure which side a unit falls on, judge it.

### 4. Act on the verdict
- **accept** — integrate the unit. Move on.
- **revise** — return the unit to an executor with the judge's specific defect
  list appended to the original work order. Re-judge after revision. Cap
  revisions (default 2 rounds); on a third failure, escalate — repeated revise
  means the spec is wrong, not the execution.
- **escalate** — surface to the operator with: the task, the output, the judge's
  rationale, and the specific decision you need from them. Do not loop on it
  yourself. This is the only thing that should reach the human.
- **judge unavailable / judge error** — if `judge_verdict` returns
  `judge_available: false` (e.g. `reason: judge_unavailable` or
  `reason: judge_no_verdict`), or any error result that is NOT the empty-criteria
  refusal, treat it EXACTLY like `escalate`: **HALT the dispatch and route to the
  human operator.** A 503 / timeout / connection error is not a verdict.

**Hard rule — judge-unavailable is a hard halt (FT-009).** When the decorrelated
judge is unreachable or returns no usable verdict on substantive work, you are
**NOT permitted** to self-verify, self-accept, or substitute your own judgment for
that work (anything above the self-accept tier). Self-verification under judge
outage is the decorrelation property failing open — it is forbidden. The only
self-accept path is the *pre-judge* triage in step 3 (low-risk + self-verifiable +
cheap-to-reverse), decided BEFORE a judge call — never a fallback after a
warranted judge call fails. "The output looks clear-cut" is not a license to
self-verify: if the unit warranted a judge call, an unavailable judge sends it to
the human, not to you.

### 5. Deliver
On acceptance of all units, synthesize and deliver the result back through the
gateway. The operator sees a finished thing or a single clear escalation — never
the intermediate churn.

**Sandbox file paths (docker backend):** write working files under `/workspace`
and deliverables under `/output` (a host-visible mount); never write to `/tmp`.

**File deliverables (docker backend):** write any artifact you intend to send
through the gateway under `/output/` — it is host-mounted to a media-delivery safe
root, so the host gateway can read and attach it. Container-only paths (`/tmp/...`)
or `/workspace/...` are not delivery-safe and the host-side validator **silently
skips** them.

## Pitfalls
- **Underspecified work orders.** The #1 failure. A subagent with partial
  context produces confident garbage. If you find yourself writing "as we
  discussed" into a work order, stop — the subagent did not discuss anything.
- **Judging without criteria.** `judge_verdict` refuses empty acceptance
  criteria by design. That refusal is a signal your decomposition was sloppy,
  not an obstacle to route around.
- **Self-accepting to save time.** The triage rule is not "self-accept when
  busy." It is "self-accept when genuinely low-risk and self-verifiable." High
  tier always judges.
- **Revise loops that never converge.** Three failed revisions = wrong spec.
  Escalate; do not let the executor and judge ping-pong indefinitely.
- **Judge running on the architect's model.** Outside this skill's control but
  fatal — verify the judge is pinned to Gemini (see the judge-verdict plugin's
  config reference). A judge that is secretly Claude is no check at all.
- **Self-verifying when the judge is down.** A 503 / timeout / error from
  `judge_verdict` is NOT permission to grade the work yourself. Judge-unavailable
  on substantive work is a hard halt to the human (see step 4). Falling back to
  self-accept is the FT-009 failure: the decorrelation check failing open through
  the unavailability door. "It's clear-cut" is exactly the rationalization that
  reopens that door.

## Verification
A correct run shows: a dispatched task decomposed into standalone work orders;
parallel executor subagents producing units; substantive units adjudicated by
`judge_verdict` while trivial ones are self-accepted with stated reasoning; at
most one escalation reaching the operator; a single synthesized deliverable
returned. If everything reached the human, triage failed. If nothing was judged,
triage failed the other way. A substantive unit whose judge call hit an outage and
was routed to the human is NOT a triage failure — that is the FT-009 fail-safe
working; self-verifying it would have been.
