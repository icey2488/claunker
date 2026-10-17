# Claunker Spine — Drive Merge-Sync (card 3ae179b1)

**Status:** Built and tested, DORMANT. No SA key has been minted at time of
writing; live sync has never touched Drive. Card stays dispatched until
Erick mints the SA key, pushes, and verifies a first live sync.

---

## 1. What this is

A second, independent Google Drive channel for the Claunker Spine, alongside
the existing immutable-snapshot backup:

| Channel | Module | Folder | Blob | Purpose |
|---|---|---|---|---|
| Backup | `spine/drive_backup.py` | `claunker-spine-backups/` | `claunker_spine_v1.json.gz` (+ daily snapshots) | immutable, point-in-time durability — **untouched by this work** |
| Sync | `spine/drive_sync.py` | `claunker-spine-sync/` | `claunker_spine_sync_v1.json.gz` | live, mutable, convergent cross-device merge |

They are separate folders and separate blobs on purpose: the backup channel's
job is "never lose a point-in-time copy," the sync channel's job is "converge
concurrent edits across devices." Mixing them would make the backup's
immutable history depend on merge correctness, and vice versa.

Both are DORMANT until `CLAUNKER_SPINE_SA_KEY` (the same env var for both
channels — same credential, different folder/blob) points at an existing SA
key file. Dormant means: one INFO log line, every public method is a no-op,
nothing touches Drive.

---

## 2. Architecture

```
spine/vendor/sync-merge.js     <- vendored VERBATIM from icey2488/Kanbantt (never edited here)
spine/vendor/merge_runner.js   <- thin new stdin/stdout CLI wrapper (spine-owned, editable)
spine/vendor/tests/            <- the vendored file's own upstream tests
spine/merge_bridge.py          <- Python -> node subprocess bridge (run_merge())
spine/drive_common.py          <- shared Drive auth/folder/blob plumbing (factored out of drive_backup.py)
spine/drive_backup.py          <- unchanged behavior; now a thin wrapper over drive_common
spine/drive_sync.py            <- the pull-merge-push cycle (DriveSync)
spine/storage.py               <- Store.load() recomputes seq on open (storage doc §5)
```

**The merge core is a black box.** `sync-merge.js` is copied byte-for-byte
from the Kanbantt repo and is never edited in this repo — see
`spine/vendor/README.md` for the exact source commit and the re-vendor
procedure. Its collection registry (`UNION_COLLECTIONS`) already includes
`projects`/`tasks`/`artifacts`/`escalations` — exactly the spine's four entity
tables — so **no shape adapter was needed** on the vendored file. Any future
reconciliation between the spine's blob and what the merge core expects
belongs in `drive_sync.py`/`merge_bridge.py`, never in the vendored file.

**The bridge is a subprocess, not a library.** `merge_bridge.run_merge()`
shells out to `node spine/vendor/merge_runner.js`, sending `{"a": blob,
"b": blob}` on stdin and reading `{"merged": blob, "conflicts": [...]}` (or
`{"error": "..."}`) from stdout. Node's path is resolved **once**, at import
time, via `CLAUNKER_NODE_PATH` (if set) or `shutil.which("node")`
(`where`/`which`) — this is the same "a dispatched job's PATH is not an
interactive shell's PATH" lesson `drive_backup.py`'s SA-key resolution
already follows. **Fail-closed:** if node cannot be resolved, every call to
`run_merge` raises `MergeBridgeError` immediately — sync never silently
no-ops when its dependency is missing.

**The cycle** (`DriveSync.run_cycle()`):
1. **pull** — look up `claunker_spine_sync_v1.json.gz` in `claunker-spine-sync/`.
   Absent → **bootstrap**: the local `Store.dump()` becomes the seed, pushed
   as-is, no merge call.
2. **merge** — local `dump()` + the downloaded remote blob, through the
   injectable `merge_fn` (defaults to `merge_bridge.run_merge`; tests inject a
   mock, mirroring `drive_backup.py`'s `drive_service` injection pattern).
3. **load** — `Store.load(merged)` writes the merged collections back. `seq`
   is recomputed as `max(entity seq)` from the merged entities' own version
   tokens (never taken from the blob's own top-level `seq` field), so a merge
   can never regress the store's monotonic counter below tokens already
   minted locally. See §5 below.
4. **push** — gzip + preflight (structural validation: mandatory root keys
   present) + sha256, then upload (create if no existing blob, update
   in-place otherwise).

**Split-brain guard:** 2+ files named `claunker_spine_sync_v1.json.gz` in the
sync folder raises `RuntimeError` and halts the cycle — the same guard
`drive_backup.py` uses for its active blob, generalized into
`drive_common.find_blob_id()`.

**Debounce/lifecycle** mirrors `drive_backup.py` exactly: `mark_dirty()`
schedules a debounced `run_cycle()`, `startup_flush()` runs one full cycle at
process start, `shutdown_flush()` is a best-effort final cycle if dirty.

---

## 3. seq recompute-on-open (storage doc §5)

**Before this work:** `Store.load()` trusted the blob's top-level `seq` field
verbatim (`self.seq = blob.get("seq", self.seq)`).

**Now:** `Store.load()` recomputes `self.seq = max(self.seq,
max(entity version-token seq))` by parsing the `{seq}:{hash}` prefix off every
loaded entity's version token (`storage.py::_max_entity_seq`), and ignores the
blob's own `seq` field entirely. This is the storage doc's ratified §5 item:
"recompute `seq = max(entity seq) + 1` on open, before the merge layer runs" —
the "+1" in that note describes the *next* seq a following `put` will mint;
`self.seq` itself holds the *last-used* value (matching `_next_seq`'s
pre-increment convention), so restoring it to the bare max keeps that
invariant intact (verified by the existing `test_dump_load_blob_round_trip`).

Why this matters for sync specifically: `sync-merge.js`'s `mergeBlobs` carries
a blob's top-level `seq` field through as an ordinary tie-broken value (see
its interface notes in `spine/vendor/README.md`) — it has no awareness of the
store's version-token scheme. Trusting that field verbatim after a merge
could hand the local store a `seq` lower than tokens it already minted,
producing colliding version tokens on the next `put`. Recomputing from the
entities themselves closes that gap.

**Amended 2026-10-16:** the in-memory `seq` this section describes is no
longer the whole story. `seq` is now backed by a persisted high-water mark —
a `meta` table row under key `seq_hwm` — seeded once from the max entity
token and incremented inside each write's transaction (the ABA fix landed in
6bf0040/ec0142e; see `claunker-spine-storage-ratified.md` §5). `Store.load()`
still runs the recompute above (`storage.py::Store.load`, via
`storage.py::_max_entity_seq`) as a backstop, but now also advances the
persisted `meta.seq_hwm` to the recomputed value — and never regresses it
below what is already persisted, only ever forward. That backstop is what
keeps a Drive-merge load honest: even if a merged blob somehow carried
entity tokens ahead of this store's persisted HWM, `load()` raises the HWM
to match before any further `put` runs, so a post-merge write can never mint
a token the write-time compare-and-swap would later misjudge — the merge
seam cannot undercut the CAS invariant the persisted HWM exists to enforce.

---

## 4. Setup (Erick's moves — not done as part of this card)

1. **Mint a fresh SA key** (JSON key file) for a service account with Drive
   API access.
2. **Folder-ownership caveat:** a folder the service account creates is
   *owned by the service account*, not by a human Google account. Unless the
   `claunker-spine-sync/` folder (and, if reused, `claunker-spine-backups/`)
   is explicitly shared to Erick's Drive account — or the SA is provisioned
   under a Shared Drive Erick already has access to — only the SA credential
   itself can see or manage it from the Drive UI. Handle this at the same
   time as minting the key: either (a) share the auto-created folder to
   Erick's account with Editor access once it exists, or (b) pre-create the
   folder under a Shared Drive and have the SA operate within it.
3. **Set the env var** (same one `drive_backup.py` already uses):
   `CLAUNKER_SPINE_SA_KEY=/path/to/key.json`
4. **Optional:** `CLAUNKER_NODE_PATH=/path/to/node` if the process running the
   spine server does not have `node` on its PATH (dispatched jobs can have a
   sparser PATH than an interactive shell).
5. Start the server. `DriveBackup` and `DriveSync` both activate off the same
   key; `DriveSync.startup_flush()` runs the first pull-merge-push cycle
   (bootstrap, since no sync blob exists yet).

**Nothing above was performed as part of this work.** No key was set, no
Drive folder was touched, no live sync ran.

---

## 5. Vendoring rule

`spine/vendor/sync-merge.js` is copied byte-for-byte from
`icey2488/Kanbantt`'s `src/lib/sync-merge.js` and is **never edited in this
repo**. To pick up an upstream change: re-copy the file from the source repo
at the new commit, update `spine/vendor/README.md`'s recorded commit hash and
date, and re-copy its test file into `spine/vendor/tests/`. Any adapter code
needed to reconcile the spine's blob shape with what the merge core expects
lives on the Python side (`spine/drive_sync.py` / `spine/merge_bridge.py`),
never in the vendored file itself. See `spine/vendor/README.md` for the exact
source commit this vendoring was done at.
