# Claunker Spine — Entity Schema & Lifecycle (Ratified)

**Status:** Ratified 2026-06-16, amended 2026-06-28, amended 2026-07-05 (created_by), amended 2026-07-06 (due, depends_on, patch semantics).

This document matches the as-built `spine/entity.py`, `spine/projection.py`, and `spine/spine.py`. Field names, enum values, and shapes below are the code's, not a parallel spec.

---

## 1. The four entities

Every entity carries three universal fields:

- `id` — stable identity (client-minted UUIDv4).
- `version` — opaque, equality-only token; consumers compare it, never parse or order it.
- `deleted_at` — soft-delete tombstone (ISO-8601 string) or `null` while live.

plus its own semantic fields:

```
Project     { id, version, deleted_at, name, created_at }

Task        { id, version, deleted_at, project_id, title, state, tier,
              acceptance_criteria, effort, impact, due, depends_on,
              order, created_at,
              created_by: { "type": "human" | "agent", "id": string } | null,
              archived_at }

Artifact    { id, version, deleted_at, task_id, kind, ref, created_at }

Escalation  { id, version, deleted_at, task_id, reason, control_diff,
              resolved_at, created_at }
```

**`Task.order` — ADDED 2026-06-28.** A LexoRank board position. The Card projection passes `order` through, so a Task must store its rank. It is not one of the original spec's core semantic fields; it is required for the Card `order` passthrough and for the retained LexoRank ordering.

**`Artifact.kind` ∈ {`diff`, `file`, `verdict`, `delivery`}.** `verdict` signals *judged*; `delivery` signals *delivered* — these are distinct kinds, not synonyms, and the ingest boundary (§5) keys off `delivery` specifically.

**`Escalation.control_diff`** is `{ control_id, old_value, new_value, reduces_control } | null` — `null` when the escalation proposes no control change. `reduces_control` flags a change that *weakens* a guardrail; it is the field an approval queue prioritizes on.

---

## 2. State enum (AMENDED)

The Task lifecycle is a single pipeline axis:

```
created → tiered → dispatched → judged → delivered
                                              │
                                           failed   (terminal sibling of delivered)
```

- There is **NO `escalated` state** (2026-06-28: escalation is decoupled from the pipeline axis — see §3).
- **`failed`** is a terminal state meaning budget exhaustion or irrecoverable error. A `failed` task emits an Artifact receipt. Revival is **not** an escalation sign-off: it is a fresh `card_retry` intent that forks a new graph. A failure is recorded and closed, never silently reopened.

---

## 3. Escalation (AMENDED) — a distinct entity, not a state

An Escalation is its own entity, orthogonal to the pipeline. An **unresolved** Escalation renders as a badge on the task's pipeline column **plus** an approval-queue filter entry; the task keeps its state and its column. (This is the 2026-06-28 change from the old `escalated`-state → `blocked`-column model — escalation no longer moves the card.)

The badge carries `{ kind, id, reason, control_diff }`. When a task has multiple unresolved escalations, the **oldest wins** (min `created_at`, `id` as tiebreak) — the one that has been waiting longest drives the badge.

---

## 4. Render boundary (AMENDED)

**Six columns, 1:1 with state** (`column_id = state`). The board is a pure read-layer projection; columns are never stored. (2026-06-28 change from the old collapse onto four reserved columns.)

The orchestration mirror's whole value is pipeline *visibility*: `dispatched` vs `judged` and `delivered` vs `failed` are kept distinct so the operator can see exactly where work sits. Collapsing them would discard the observability the mirror exists to provide.

---

## 5. Ingest boundary (Hermes → Spine)

How Hermes lifecycle signals map onto Spine state:

| Hermes signal | Spine effect |
|---|---|
| `ready` / `claimed` | `created` / `tiered` (by tier presence) |
| `running` | `dispatched` |
| `done` | `judged` / `delivered` (by presence of a `delivery`-kind Artifact) |
| `blocked` | creates an **Escalation** entity — the task stays in its pipeline state, **not** a state change |
| Hermes error / budget-dead | `failed` |

The `blocked` row is the load-bearing one: a block raises an orthogonal Escalation; it does not move the task off its column.

---

## 6. Mutation invariants (AMENDED)

- **MI-1.** Reject Artifact/Escalation creation whose `task_id` resolves to a tombstoned **or absent** Task. This is a create-admission check at the server boundary, not a merge rule. (No orphan children; no late children on a dead parent.)
- **MI-2.** Escalation resolution is a single-field write of `resolved_at` — trivial now, because there is no paired state transition to keep in sync (`escalated` is no longer a state).
- **MI-3 — DISSOLVED.** There is no `escalated` state to hold in a biconditional with the Escalation table, so the invariant that maintained that pairing no longer has anything to constrain.

---

## Amendments

### Amendment 2026-07-05 — Task.created_by (additive, non-material)
- **What:** Task gains `created_by: { "type": "human" | "agent", "id": string } | null`, null default, create-time only in v1 (no mutation path). NO schema_version bump — nullable-with-null-default, the archived_at precedent.
- **Justification:** dispatch-lane ledger note (2026-07-03), Gap 1 — the ledger cannot distinguish a Hermes dispatch from a claude-async job from a hand-made card; first proven by backfill card 086a67c9. Card: d89e3f8d.
- **Upstream:** R1–R6 and MI-* untouched; the merge stays schema-dumb (a new plain field, never merge-rewritten); write-once tier unaffected.
- **Downstream:** projection passes created_by through to the spec's Card.created_by (absent → null, NEVER fabricated); jobcard gains --actor; the MCP write path continues to derive actor from authenticated context per spec (the CLI is local-trust, the wire is not); corpus mirror re-syncs after commit. The kanbantt-mcp-spec needs NO change — it has defined created_by since v0.1.0; this is the spine catching up to the spec.
- **Materiality:** non-material, additive; Gemini review on record 2026-07-05; log-and-proceed per governance §3.

### Amendment 2026-07-06 — RFC 7386 patch semantics, Task.due, Task.depends_on, edit-audit ledger (spec v0.5.0)

See full record: `docs/claunker-amendment-2026-07-06-v050.md`.

- **A. card_update RFC 7386 patch semantics** — key-presence replaces value-presence: absent=unchanged, present-null=clear for `{due, effort, impact}`; `depends_on` clears via `[]` (null→validation_failed); guarded set `{tier, archived_at, deleted_at}` present-null→validation_failed naming the governed tool. Material 3(b).
- **B. Task.depends_on** — `[task_id]`, empty-list default, additive. Display-only v1; write-admission rejects self-reference; cycles flagged at render. Clears via `[]`. Material 3(b).
- **C. Task.due** — `ISO-8601 | null`, null default, additive drift closure (spec Card has declared `due` since v0.1.0). Non-material.
- **D. Edit-audit ledger** — append-only `{id, card_id, field, old, new, actor, ts}`, one row per change on `{due, effort, impact, depends_on}`, atomic with mutation, no read API v1. Non-material.
