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

## 5. Known item — in-memory `seq`

v1 keeps `seq` in memory; it resets to 0 on a raw `.db` reopen. This is harmless: versions are opaque, compared for equality only, and content-hashed, so a reset `seq` cannot cause a false equality or a misorder that any consumer can observe. When the sync-merge layer lands, recompute `seq = max(entity seq) + 1` on open, before the merge layer runs, so the monotonic clock is restored from the stored tokens.
