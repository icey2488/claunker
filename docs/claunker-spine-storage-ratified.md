# Claunker Spine — Storage Architecture (Ratified)

**Status:** Ratified 2026-06-16, amended 2026-06-28 (supersedes the 2026-06-16 version, archived in the Drive Corpus "Superseded" folder).

---

## 1. What this document decides

How the Spine's orchestration-state durably persists, and how that persistence reconciles across devices. The 2026-06-16 ratification settled the *representation* (a merge-able JSON blob, CRDT-style); the 2026-06-28 amendment settles the physical *medium* beneath it (SQLite, not a raw `.json` file). The two are layered, not in conflict: the amendment changes where bytes land on the local disk, never the shape that crosses the sync boundary.

---

## 2. The 2026-06-28 amendment — physical storage medium

The durable/sync representation **stays** a single merge-able JSON blob:

```
{ schema_version, seq, projects, tasks, artifacts, escalations }
```

reusing Kanbantt's `sync-merge.js` (unchanged). That is the contract that crosses the wire and reconciles between devices, and it does not move.

The amendment is strictly about the **local** store. The local store is **SQLite** (`spine.db`, WAL mode) holding the blob's entity collections as **JSON-per-row** across four tables:

```
projects     ( id TEXT PRIMARY KEY, data TEXT NOT NULL )
tasks        ( id TEXT PRIMARY KEY, data TEXT NOT NULL )
artifacts    ( id TEXT PRIMARY KEY, data TEXT NOT NULL )
escalations  ( id TEXT PRIMARY KEY, data TEXT NOT NULL )
```

Each row's `data` is one entity's opaque JSON blob. This was chosen over the two rejected alternatives:

- **NOT a raw `.json` file** — it clobbers under concurrent writes (a process flushing the whole file races any other writer; last-write-wins silently drops state).
- **NOT an event log** — an append-only event store can't reuse the blob-CRDT merge; you'd have to invent a second reconciliation mechanism and forfeit the convergence proof.

---

## 3. The resolved decisions

### R1 (amended) — representation vs. medium

- **Durable/sync representation** = the JSON blob, with `sync-merge.js` reused verbatim as the merge core.
- **Local store** = a SQLite/WAL four-table blob container.
- **The seam:** the blob is materialized for sync via `dump()` (SELECT `data` per table) → `sync-merge.js` → `load()` (INSERT OR REPLACE).
- **Option B remains rejected.** A normalized/relational store with foreign keys and joins is *not* what SQLite buys here. SQLite is a concurrency-safe blob container; entities are opaque JSON; the merge stays schema-dumb. We get SQLite's write serialization and crash safety without taking on relational semantics the merge can't tolerate.

### R2 — write volume (carried over)

Resolved on write-volume: the Spine takes dispatch-frequency writes — seconds to minutes between mutations. Ephemeral or high-frequency state (per-token agent chatter, session scratch) does **not** belong in the Spine; it belongs in Hermes's session store. The Spine is the durable orchestration ledger, not a hot path.

### R3 — the merge core stays schema-DUMB (carried over)

The merge blindly unions id-keyed collections and resolves same-id conflicts via a content-addressed `conflictId`, with **zero** relational logic. This is the property that preserves the convergence proof: a merge that understood foreign keys could diverge; a merge that only unions opaque id-keyed sets cannot.

### R4 — parent deletion tombstones the parent ONLY (carried over)

A parent delete sets a tombstone on the parent and nothing else. Children keep their refs; the **read layer** filters them out of live views. This is an audit-ledger property: a parent delete never destroys child receipts. The receipts survive the parent's removal.

### R5 — cross-entity conflicts SURFACE (carried over)

Cross-entity conflicts are surfaced for resolution; the merge **never** rewrites foreign keys to "fix" a dangling reference. Reconciliation is a read-time and human-in-the-loop concern, never a silent merge-time rewrite.

### R6 — artifact refs must be DURABLE (carried over)

An Artifact `ref` must point at durable content: Spine-owned Drive content, or a permanent URI such as a git hash or a Drive id. It must **never** be an executor-local sandbox path, which evaporates when the sandbox is torn down.

---

## 4. Rationale for SQLite-as-container (2026-06-28)

Even a single-authority server doing read-modify-write-flush needs **write serialization** and **crash safety**. SQLite in WAL mode gives both for free, and it transparently covers any second process that happens to open the store. A raw `.json` file gives neither; an event log gives them but at the cost of the blob-CRDT merge.

**Decorrelated-review note.** The multi-actor "file clobbering" framing that originally motivated this amendment is imprecise for v1's single-writer server: the actors are *request sources*, not concurrent file writers. The accurate grounds are write-serialization and crash-safety. Same remedy (SQLite-as-container), more accurate mechanism. The decision stands; only its justification is sharpened.

---

## 5. Persisted `seq` high-water mark + write-time CAS (amended 2026-10-16)

**Superseded premise.** An earlier version of this section held that keeping `seq` in memory — resetting it to 0 on every raw `.db` reopen — was *harmless* because versions are opaque, equality-only, and content-hashed. That was **wrong**, and read-only repro job `claunker.spine-aba-verify-20261016-syjhcgcm` proved it.

**The finding (ABA hazard).** Every writer opens a *fresh* `Store` per call (`with Store(config.db_path) as store:`), so the in-memory `seq` restarted at 1 on each open. The content hash covers the entity content minus the `version` field, and a `Task` carries no per-write timestamp, so reverting content A→B→A re-minted the **identical** token — the first and third tokens were byte-for-byte equal (in the repro, `1:a641b109ad0c61c4`). Because the tokens coincided, a stale writer whose `expected_version` matched the earlier token was **accepted**: a silent lost update. With 97% of live tasks carrying `seq=1` tokens, the collision surface was broad.

**The fix — two layers.**

1. **Persisted high-water mark.** A `meta(key TEXT PRIMARY KEY, value TEXT)` table holds one row under key `seq_hwm`. It is seeded **once** from the maximum `seq` prefix across all stored entity tokens (reusing `_max_entity_seq`, capped at 2⁶³−1), then every mint increments it **inside the write transaction** (`BEGIN IMMEDIATE`, so writers serialize on the reserved lock). `seq` is therefore monotonic *across processes*, and a reverted content can never reproduce an earlier token. `Store.load()` still recomputes `seq = max(entity seq)` as a backstop and advances the persisted HWM too (it never regresses below what is stored).

2. **Write-time compare-and-swap.** The guarded card-write paths thread their `expected_version` down to the store, which writes with `UPDATE … WHERE id=? AND json_extract(data,'$.version')=?`. A zero-row result means a concurrent writer moved the token between the caller's read (a *separate* autocommit transaction) and this write; the whole transaction rolls back and the existing `ConflictError` is raised carrying the fresh row.

Both layers exist on purpose and cover different failures: the HWM prevents **identical tokens** on a content revert; the CAS prevents a **lost update** between two concurrent writers that read the same version. The token format `{seq}:{hash}` is **unchanged**, so clients are unaffected.
