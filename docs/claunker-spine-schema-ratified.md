# Claunker Spine — Entity Schema & Lifecycle (Ratified)

**Status:** Ratified 2026-06-16, amended 2026-06-28, amended 2026-07-05 (created_by), amended 2026-07-06 (due, depends_on, patch semantics), amended 2026-07-22 (created_by dispatch provenance), amended 2026-07-23 (description body + preserved foreign metadata; spec v0.8.0).

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
              acceptance_criteria, description, effort, impact, due, depends_on,
              order, created_at,
              created_by: { "type": "human" | "agent", "id": string,
                            model?: string, effort?: string, job_id?: string,
                            ...tolerated foreign keys } | null,
              archived_at,
              metadata: { ...unmodeled foreign Card keys } }

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

### Amendment 2026-07-22 — created_by dispatch provenance (additive, non-material; spec v0.7.0)

- **What:** `created_by` gains OPTIONAL dispatch-provenance sub-keys — `model`, `effort`, `job_id` (each a string when present) — carried INSIDE the existing identity object, plus tolerance for unknown foreign keys:
  ```
  created_by: { "type": "human" | "agent", "id": string,
                model?: string, effort?: string, job_id?: string,
                ...tolerated foreign keys } | null
  ```
  No `schema_version` bump — additive-optional on the created_by/archived_at nullable precedent; legacy blobs (identity-only, or null) load untouched.
- **Why the sub-keys live INSIDE created_by (load-bearing):** provenance describes HOW a card was MINTED (which reasoning model, which effort budget, which dispatch job), not the WORK. The Task already owns `effort`/`impact` as its **Matrix work-sizing axes** (mutable). A top-level dispatch `effort`/`model` would COLLIDE with that field's meaning. Homing provenance inside `created_by` sidesteps the collision AND inherits the write-once mint semantics. **Rule generalized:** never overload an existing field's semantics to carry provenance — additive-only, name-disjoint.
- **Write-once / immutable:** provenance is set at MINT and never mutated — same rationale that makes `tier` write-once: the audit value is "what actually ran," which a mutable stamp destroys. `update_task` has no `created_by` parameter (structurally immutable); the MCP `card_update` handler REJECTS any patch carrying `created_by` with an EXPLICIT `validation_failed` (never a silent drop).
- **Trust split at the wire (`card_create`):** IDENTITY (`type`/`id`) stays AUTHORITY-OWNED — always re-stamped from the authenticated credential, never the payload (anti-spoof, unchanged). PROVENANCE (`model`/`effort`/`job_id` + unknown non-identity keys) is descriptive metadata the minting client owns — READ from the payload and MERGED onto the credential identity. No provenance in → none stored (human intake). Validation of the merged shape happens in the entity layer (`_validate_created_by`): a non-string provenance value → `SpineError` → `validation_failed`.
- **MCP interop:** the field and every sub-key are OPTIONAL; unknown keys inside `created_by` are TOLERATED both directions (a foreign server's extra keys never break our read/write path; our keys are additive to theirs). Projection passes the whole `created_by` object through verbatim (absent → null, NEVER fabricated).
- **Downstream:** Kanbantt renders a quiet model+effort chip on the card face and a read-only provenance block in the card dialog — ONLY when provenance is present (human cards show nothing). The bridge (claude-async) emitting provenance at mint is a SEPARATE follow-up; this amendment is the RECEIVING half only.
- **Documentation-drift note (for the record):** the provenance design brief asserted `created_by` was "added during v0.6.0 card_create and is NOT in the ratified four-entity schema doc." That is inaccurate — `created_by` was added in Amendment 2026-07-05 (above) with shape `{type, id}` and HAS been in §1 since. The v0.6.0 work was the `card_create` MCP tool wiring the stamp from the credential. The brief's `{kind, actor}` naming was likewise superseded by the as-built `{type, id}`; this amendment builds on the real shape, not the brief's.
- **Materiality:** non-material, additive; log-and-proceed per governance §3.

### Amendment 2026-07-23 — Task.description + Task.metadata (additive; spec v0.8.0)

- **What:** Task gains two additive fields:
  - `description: string | null` (null default) — the spec-conformant, agent-agnostic narrative BODY (Markdown). It had been in the kanbantt-mcp-spec Card since v0.1.0 but the spine modeled NOBODY and the projection emitted a constant `""` — a silent drop, established as a spec breach by adversarial review (Gemini). Now stored, projected as the real value (null when unset — NOT coerced to `""`), and MUTABLE (unlike write-once `created_by`): `update_task`/`card_update` set it, present-`null` clears it (RFC 7386 key-presence).
  - `metadata: { ...foreign keys }` (empty-dict default) — the typed map of UNMODELED foreign Card keys the spine now PRESERVES and round-trips instead of flattening away (resolving spec Design Principle 5 / the unknown-field rule vs the old "flatten away" Create clause). The server fills it at the write boundary with every CardInput/patch key outside the known Card surface (`spine.projection.CARD_FIELD_KEYS`, plus the input-only `tier`/`project_id` carriers); the projection overlays it back onto the Card. `card_update` merges it as an RFC 7386 patch (null value removes a key).
  No `schema_version` bump — both are additive on the `created_by`/`archived_at` nullable precedent; legacy blobs (missing both keys) load untouched (`from_dict` defaults them, `metadata` None → `{}` in `__post_init__`). Confirmed against `storage.SCHEMA_VERSION` (still 1 — that constant governs the dump/load ENVELOPE, bumped only on a breaking blob-shape change; an additive nullable field is not one).
- **Write-admission caps (prevention at the boundary, like the MI-1 / created_by caps):** `description` ≤ 16384 chars; `metadata` reuses the `created_by` interop budget (≤12 keys / 512-char string values / depth 3 / 4096 bytes, aliased so they cannot drift). Over any cap → `SpineError` → `validation_failed` naming the limit; fail closed, never truncate.
- **Boundary discipline (why foreign-only):** `metadata` holds ONLY keys outside the known Card surface. Governance/authority/first-class-modeled keys (`column_id`/`order`/`tier`/`created_by`/`version`/`archived_at`/`deleted_at`/`gate_status`/`badge`/…) are excluded at write AND skipped again at the projection overlay, so a client can neither smuggle a spoofed extension field through `metadata` nor bypass a governed path. KNOWN-but-unmodeled Card fields (`priority`/`checklist`/`attachments`) keep their documented-default projection — a published divergence (SPEC-DIVERGENCES.md), not a silent drop.
- **Downstream:** projection stops emitting `""` and projects the real body; jobcard gains `--description`; the claude-async bridge derives a bounded intent summary from the dispatch prompt for the body; the board (Kanbantt) edits `description` in the MCP-writable dialog alongside the distinct `acceptance_criteria`. kanbantt-mcp-spec bumped to v0.8.0 (the spine catching up to a field the spec already declared, plus the preserve-and-round-trip behavior change).
- **Materiality:** non-material, additive; log-and-proceed per governance §3.
