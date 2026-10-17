# Claunker Spine — Reconciliation Record (2026-10-16)

**Status:** Reconciliation record, 2026-10-16. **Materiality: MATERIAL** (a ratified
invariant — "resetting `seq` to 0 is harmless" — was proven false).

---

## 1. Why this exists

`claunker-spine-storage-ratified.md` §5 ("Known item — in-memory `seq`") held
that v1's in-memory `seq`, reset to 0 on every raw `.db` reopen, was harmless:
versions are opaque, compared for equality only, and content-hashed, so a reset
could not cause a false equality or a misorder any consumer could observe. A
design review of the sync seam (job
`claunker.seam-seq-design-review-20261016-0wfhs673`) accepted that premise
as-is — verdict **accept-as-is** — without testing it directly. A subsequent
Gemini adversarial pass raised the ABA hazard the accept-as-is verdict had not
considered: if a `Task`'s content ever reverts (A→B→A), and the content hash
covers everything except `version`, the token minted for state A the second
time could coincide with the token minted for state A the first time. A
read-only repro job, `claunker.spine-aba-verify-20261016-syjhcgcm`, confirmed
it: `T1 == T3` byte-for-byte, a stale write carrying `T1` was accepted as
current, and with 97% of live tasks carrying `seq=1` tokens the collision
surface was broad, not theoretical. This note records the reconciliation that
followed.

---

## 2. The divergence and its resolution

**Divergence.** The ratified claim ("reset to 0 is harmless") was false under
a condition the ratifying review did not test: a content revert that produces
a byte-identical version token across two different points in time, with a
stale write landing in between.

**Resolution — two layers, both in `spine/storage.py` so every writer
(`spine_server`, `jobcard`, `drive_sync`/`load`) inherits them (fix commits
`6bf0040` / `ec0142e`):**

1. **Persisted `seq` high-water mark.** A `meta(key, value)` table holds one
   row under key `seq_hwm`, seeded once from `max(entity seq)` across every
   stored token (`storage.py::_max_entity_seq`, capped at 2⁶³−1), then
   incremented inside the write transaction (`storage.py::Store._next_seq`,
   under `BEGIN IMMEDIATE`). `seq` is therefore monotonic **across processes**
   — a content revert can never reproduce an earlier token, closing the ABA
   hazard at the root.
2. **Write-time compare-and-swap.** Guarded card-write paths thread
   `expected_version` down to `storage.py::EntityStore.put`, which writes with
   `UPDATE ... WHERE id=? AND json_extract(data,'$.version')=?`. A zero-row
   result rolls back and raises `ConflictError` carrying the fresh row,
   closing the separate lost-update race between two concurrent writers that
   read the same version.

Three doc amendments followed, each dated 2026-10-16 and part of this same
motion:

- **Storage doc §5** (`claunker-spine-storage-ratified.md`) — replaces the
  "reset to 0 is harmless" premise with the finding, the persisted-HWM design,
  and the CAS, citing the repro job.
- **Schema doc §1** (`claunker-spine-schema-ratified.md`) — adds the
  producer-side clarification: the minting authority (the store) may read the
  `seq` prefix of tokens it minted to seed/restore its own counter; consumers
  remain equality-only and the token shape stays an implementation detail.
- **`spine-drive-sync.md` §3** — describes `Store.load()`'s recompute as now
  also advancing the persisted `meta.seq_hwm` forward-only, so a Drive-merge
  load can never undercut the CAS invariant.

**Upstream effects.** The storage and schema docs' ratified invariants are the
governing contracts here; both are amended in the same motion above, confirmed
intact otherwise (the `{seq}:{hash}` token shape, opacity-to-consumers, and
the write-once/last-write-wins semantics for unguarded callers are all
unchanged).

**Downstream effects.** Every writer that goes through `spine/storage.py`
(`spine_server`, `jobcard.py`, `drive_sync.py`/`load()`) inherits both layers
automatically — no call-site changes needed beyond threading
`expected_version` through to `put()` (already done in `spine/spine.py`'s
guarded mutation paths in commit `6bf0040`). The on-disk token format
`{seq}:{hash}` is unchanged, so no wire client is affected. This also closes a
conformance gap against `kanbantt-mcp-spec.md`'s `expected_version` conflict
semantics (lines ~267–268: "the mandatory `expected_version` guard... a stale
concurrent writer gets `conflict`, never a silent clobber") — before this fix,
the ABA hazard meant a stale writer could, in the narrow content-revert case,
be accepted instead of rejected, undercutting that guarantee at the storage
layer even though the wire contract text was already correct. The fix makes
the storage layer actually honor what the spec already promised; the spec
text itself required no change (`kanbantt-mcp-spec.md` is drift-check-governed
and was not edited).

**Card.** `76718b7c-d971-47ef-a975-dcbabb155ab5` (created via `jobcard.py
create`, Dispatch Log project), titled "Reconciliation: seq ABA hazard
(persisted HWM + CAS)".

---

## 3. Review discipline

This is the lesson, not just the fix: the 2026-10-16 design review
(`claunker.seam-seq-design-review-20261016-0wfhs673`) reaffirmed a known gap
as "ratified-harmless" without testing the premise itself — it re-examined
the *conclusion* the 2026-06-16-era storage doc had already reached, not
whether that conclusion still held. The Gemini adversarial pass broke that
pattern by asking the one question the accept-as-is verdict had skipped: is
the harmlessness claim actually true? The repro job
(`claunker.spine-aba-verify-20261016-syjhcgcm`) answered no. The discipline
going forward: a review that labels a known gap "ratified-harmless" must have
that premise tested, not merely re-affirmed — a repro attempt against the
specific claim, not another pass over the reasoning that produced it.

---

## 4. Process fix

The root cause was a design review that accepted a known gap as
"ratified-harmless" without testing whether the premise itself was true. A
subsequent adversarial pass (Gemini) questioned the premise, and the repro
job (`claunker.spine-aba-verify-20261016-syjhcgcm`) proved it false — an ABA
hazard where identical version tokens were produced on a content revert, and
a stale write was accepted.

**Fix:** any review that labels a known gap as harmless or safe must be
accompanied by, or followed by, an adversarial attempt to break that specific
premise — a repro attempt — before the "ratified-harmless" label is allowed
to stand in a foundational doc. Re-affirming a conclusion is not the same as
testing it; this record exists because the review discipline let that
distinction slip once, and the fix is to close that gap in the discipline
itself, not only in the code.
