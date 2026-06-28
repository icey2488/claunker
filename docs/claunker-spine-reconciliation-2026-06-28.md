# Claunker Spine — Reconciliation Record (2026-06-28)

**Status:** Reconciliation record, 2026-06-28.

---

## 1. Why this exists

On 2026-06-28 a fresh Step-3 design and a Spine data-core build diverged from the 2026-06-16 ratified storage and schema docs. The cause was structural: those ratified docs lived **only** in the Drive Corpus, not in this repo, so the build could not read them and the adversarial review ran without them. A corpus check caught the divergence after the fact. This note records the conscious reconciliation that followed; the two amended docs (`claunker-spine-storage-ratified.md`, `claunker-spine-schema-ratified.md`) are its output.

---

## 2. The five divergences and their resolutions

**1. Persistence.**
Build: SQLite event-log. Ratified: JSON-blob-CRDT.
**Resolved:** the ratified blob-CRDT substance wins (it reuses Kanbantt's `sync-merge.js`). The physical medium is clarified to SQLite-as-container (see the storage doc). The event-log is dropped.

**2. Entity model.**
Build: a single `TaskEntity` with escalation carried as a flag. Ratified: four entities.
**Resolved:** the ratified four-entity model wins (`Project` / `Task` / `Artifact` / `Escalation`, distinct).

**3. Lifecycle states.**
Build: `created / queued / executing / judging / delivered / failed`. Ratified: `created / tiered / dispatched / judged / delivered` + `escalated`.
**Resolved:** the ratified names win (`tiered` / `dispatched` / `judged`). `failed` is **ADDED** — the build's one good contribution, a failure terminal the ratified enum lacked. `escalated` is dropped (see #5).

**4. Column render.**
Today's build: six columns, 1:1 with state. Ratified: collapse onto four reserved columns.
**Resolved:** the six-column render **SUPERSEDES** the collapse, for orchestration-mirror observability. A conscious supersession, not an accident.

**5. Escalation render.**
Today's build: an orthogonal entity + badge. Ratified: `escalated` state → `blocked` column.
**Resolved:** the orthogonal entity + badge **SUPERSEDES** the `escalated` state. `escalated` is dropped from the enum; MI-3 is dissolved; MI-2 reduces to a single-field write. A conscious supersession.

---

## 3. Review discipline

This is deliberate reconciliation, not drift. The 2026-06-16 decisions were dual-Gemini-reviewed. The 2026-06-28 supersessions were re-reviewed on the same discipline: architect recommendation → Gemini scrutiny → reconciliation. Gemini's storage intervention — SQLite-as-container over a raw blob file — is folded into the storage doc. Every change above survived an adversarial pass before being written down.

---

## 4. Process fix

The root cause was siloing: the ratified docs lived in Drive, invisible to the build.

**Fix:** the canonical Spine design docs now live in **this repo** (build-visible) as the source of truth. The Drive Corpus mirrors them, and the 2026-06-16 originals move to a "Superseded" folder. The build can no longer diverge from a doc it cannot see, because the doc is now where the build is.
