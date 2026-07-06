# Claunker — Amendment Record 2026-07-06 (spec v0.5.0 release)

**Status:** Ratified 2026-07-06. Operator sign-off + Gemini architectural sign-off on record (session, 2026-07-06). Effects documented BEFORE implementation per governance §4. Four entries, one release.

---

## A. card_update adopts RFC 7386 key-presence patch semantics — MATERIAL 3(b)

- **What:** `card_update.patch` interpretation changes: key ABSENT → field unchanged (as today); key PRESENT with `null` → **clear the field**. Clearable set, enumerated: `due`, `effort`, `impact`. `depends_on` clears via `[]` (type-strict, never null). **Guarded set:** `tier`, `archived_at`, `deleted_at` — present-null → `validation_failed`; these move only through their governed tools (card_retier, card_archive/unarchive, card_delete).
- **Justification:** the null-collapse defect (board backlog card; surfaced by Matrix SET-ONLY constraint 2026-07-05): nullable fields were roach motels — settable, never clearable — because null collapsed to omission in the pipeline. Key-presence is the standard (RFC 7386), beats $unset operators and sentinel strings, and matches what JSON clients naturally send.
- **Upstream:** merge untouched (patch semantics are write-admission, not merge); write-once tier preserved (value-change rule unchanged; null now explicitly guarded); governed pairs' exclusivity STRENGTHENED (back-door lifecycle mutation via patch-null closed).
- **Downstream:** spine card_update handler (key-presence checks); spec v0.5.0 (both repos + corpus mirror); Kanbantt client gains legal clear operations (due clear button, Matrix drag-to-unsorted, effort/impact unset); client obligation codified: never send a key you do not mean.
- **Materiality:** 3(b) wire-interpretation change. Surfaced pre-implementation; operator + Gemini ratified 2026-07-06.

## B. Task.depends_on / Card.depends_on — MATERIAL 3(b)

- **What:** `depends_on: [task_id]`, empty-list default, additive (no schema_version bump). Plain refs the merge never rewrites (R3); dangling refs to tombstoned/unknown tasks greyed at read, never stripped from storage (R4/R5). **Display-only v1**: timeline renders edges, board may badge "waiting on"; the spine does NOT gate state transitions on deps (the ledger does not re-govern ordering). Write-admission rejects self-reference only; cycles flagged at render, never blocked at write. Clearing = `[]`.
- **Justification:** operator feature request 2026-07-05 (timeline as true project visualization: "B + C before D"); proposal ratified by Gemini same day (display-only ruling: gating would turn a kanban into a DAG scheduler and force dummy transitions).
- **Upstream:** R3/R4/R5 honored by construction; MI-1 untouched (deps are peer refs, not children); convergence unaffected (plain field, dumb merge).
- **Downstream:** entity + projection + card_update (list-typed, string entries); spec v0.5.0 Card schema; Kanbantt dependency editor + timeline edges + cycle flagging; edit-audit ledger covers dep changes.
- **Materiality:** 3(b) new wire field consumed by clients. Surfaced pre-implementation; operator + Gemini ratified 2026-07-06.

## C. Task.due — additive drift closure — NON-MATERIAL

- **What:** `due: ISO-8601 | null`, null default, additive on the archived_at/created_by pattern, no schema_version bump. Projection passes through; absent stays null, never fabricated.
- **Justification:** the spec's Card has declared `due` since v0.1.0; the spine never stored it (drift surfaced by the due-fabrication forensics 2026-07-05, where the phantom chips proved no server-side due existed). This closes spine→spec drift; zero-debt per Gemini review.
- **Upstream/Downstream:** none beyond entity/projection/patch-clearable-set; existing blobs load untouched. Log-and-proceed per governance §3.

## D. Generic card-edit audit ledger — NON-MATERIAL (control addition)

- **What:** append-only `{card_id, field, old, new, actor, ts}` stream, written atomically with each set/change/clear on `due`, `effort`, `impact`, `depends_on`. Actor from authenticated context (client:bearer placeholder, per tier/archive ledgers). Record-now-render-later: no read API this version. Field-agnostic: future triage fields inherit logging without migration.
- **Justification:** operator requirement 2026-07-05 ("changes always logged"); Gemini ratified the unified stream over per-field tables (schema-sprawl avoidance).
- **Upstream/Downstream:** strengthens audit posture; no wire change; failed guards/invariants write no row. Log-and-proceed.

---

**Execution sequence (authorized):** this record → spine implementation + deploy (Job 1) → spec v0.5.0 + mirror resync (Job 2) → client wave: due picker with Clear, drag-to-unsorted, dependency editor + timeline edges (Job 3). Cards close each job with commit receipts per the dispatch-lane discipline.
