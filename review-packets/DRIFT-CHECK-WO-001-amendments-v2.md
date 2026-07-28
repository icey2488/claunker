# DRIFT-CHECK-WO-001 — ratified v2 amendment set (round 1)

RATIFICATION: Ratified by operator 2026-07-27, in-session, in the operator's own voice ("Fire as is"): all ten amendments as written, including staleness_threshold_hours starting value 168 and explicit authorization for AMD-4's supersession of the card-verbatim "Two exit codes" wording. AMD-1, AMD-3, AMD-4 amend Requirements text quoted verbatim from source card 1486ea8a-f27c-41e9-8a16-7a9f552583d2 and are applied to the card in the same arc (--expected-version, prior wording preserved in edit_audit). All amendments compose strictness up-only: more conditions detected, more codes, wider abort trigger, more red fixtures. No Constraints text changes.

---

AMD-1 [F3, F2] CARD — Requirement 5 rewrite: fix semantics split by finding.
Replaces the full Requirements item 5.

```
5. **Gated `--fix`, split by finding.** A `--fix` flag that prints the diff and
   requires confirmation, never run unattended. A DRIFT fix writes copy body :=
   source body at the copy's RECORDED provenance SHA; the header is untouched.
   A STALENESS fix writes copy body := source body at source HEAD AND rewrites
   the provenance header's recorded SHA to that HEAD, re-emitted in that
   document's manifest-declared header format; body and header change together
   or not at all. `--fix` writes source over copy, so if someone edited the
   COPY it destroys that edit; the checker knows only that the two differ, not
   which is correct. After any fix the script states that only the working
   tree changed and a commit is required before the check can go green; the
   script never stages or commits.
```

AMD-2 [F5] WO — Acceptance criteria rewrite: one red fixture per assertion.
Replaces the three bullets under "Concretely:" (intro sentences stay).

```
- DRIFT fixture: a source + copy pair where the copy is deliberately mutated
  against the committed source bytes at the recorded provenance SHA. MUST exit
  DRIFT.
- STALENESS fixture: header records SHA A, source HEAD advanced to B, copy
  body byte-identical to source at A. MUST exit STALENESS. (A body-only
  checker passes this fixture green — that is the red it exists to force.)
- MISSING_COPY fixture: a manifest-REQUIRED copy absent from its repo. MUST
  exit MISSING_COPY.
- FRESHNESS fixture: a source repo whose freshness signal exceeds the
  threshold. MUST exit FRESHNESS_ABORT — and a DRIFT planted under stale
  freshness MUST still exit DRIFT (drift is never suppressed by an abort).
- CLEAN fixture: everything green. MUST exit 0.
- Every outcome-map condition beyond these gets its own red fixture when its
  code path lands; a code path with no red fixture is not accepted.
- Red before green, per assertion: no assertion's green is trusted until its
  red fixture has been demonstrated to fail.
```

AMD-3 [F8, F9, F7, F11] CARD — Requirement 2 rewrite: trigger disambiguated, metadata resolution, remote pin, threshold home.
Replaces the full Requirements item 2.

```
2. **Abort-never-fetch.** Abort if EITHER the source repo is behind-origin per
   its last-fetched remote refs OR its last fetch is older than the staleness
   threshold; never fetch. Before evaluating freshness, assert the repo's
   remote URL matches the manifest-pinned canonical remote; a mismatch is its
   own abort condition (REMOTE_MISMATCH), never a freshness pass. Freshness is
   max(FETCH_HEAD mtime, git-directory creation time), with all git metadata
   paths resolved via `git rev-parse --git-common-dir` — never a literal
   `.git` stat, because worktrees make `.git` a file and relocate FETCH_HEAD —
   and because FETCH_HEAD is absent on a never-fetched clone and HEAD commit
   time measures authoring rather than syncing. The staleness threshold lives
   in the manifest as an instrumented hypothesis, not a constant.
```

AMD-4 [F10, F12] CARD — Requirement 3 rewrite: two codes -> total outcome map.
Replaces the full Requirements item 3. Supersedes the card-verbatim "Two exit codes" wording per explicit operator authorization (see RATIFICATION).

```
3. **Total outcome map.** Every detectable condition has a distinct exit code
   and a defined report (see Outcome map). DRIFT and MISSING_COPY remain
   distinct codes; all detected conditions are reported in every run; drift is
   reported first and is never suppressed by a coverage gap or by any abort.
```

AMD-5 [F10, F12, F3, F4, F7] WO — new section after Constraints: Outcome map.

```
## Outcome map (total)

| Condition | Exit | Meaning |
|---|---|---|
| CLEAN | 0 | all assertions green |
| DRIFT | 1 | copy body ≠ source body at recorded SHA (or ACCEPTED_GAP fingerprint mismatch) |
| MISSING_COPY | 2 | manifest-REQUIRED copy absent |
| STALENESS | 3 | source HEAD moved past recorded SHA |
| HEADER_UNPARSEABLE | 4 | provenance header absent or fails its declared format; neither drift nor staleness evaluable for that copy |
| UNMANIFESTED_DUPLICATE | 5 | duplicate-filename scan found a governed-looking copy with no manifest entry |
| PIN_MISMATCH | 6 | accepted_gap_count ≠ actual ACCEPTED_GAP entries |
| REMOTE_MISMATCH | 7 | repo remote URL ≠ manifest-pinned canonical remote |
| FRESHNESS_ABORT | 8 | last fetch older than threshold, or behind-origin per last-fetched refs |

Codes are identities; precedence is the explicit list DRIFT >
HEADER_UNPARSEABLE > STALENESS > MISSING_COPY > UNMANIFESTED_DUPLICATE >
PIN_MISMATCH > REMOTE_MISMATCH > FRESHNESS_ABORT. Exit code = highest-
precedence condition detected; ALL detected conditions appear in the report
regardless of which sets the exit. Content-drift verdicts are valid under
stale freshness (the comparison is against the recorded SHA, fetch-
independent); STALENESS verdicts are not — under FRESHNESS_ABORT the
staleness assertion is not evaluated and the run cannot exit 0.
```

AMD-6 [F4] WO — new section after Outcome map: Discovery composition.

```
## Discovery composition

The manifest is the sole authority for REQUIRED coverage: MISSING_COPY is
evaluated against manifest entries, never against scan results. The
duplicate-filename scan is a coverage net for UNMANIFESTED duplicates only —
a governed filename appearing across repos with no manifest entry. A missing
manifest-REQUIRED copy therefore cannot be masked by the scan finding
nothing; an unmanifested duplicate is its own reportable condition per the
Outcome map. The scan discovers gaps in the manifest; it never defines the
check's coverage.
```

AMD-7 [F6, F7, F11, F12, A1] WO — new section after Discovery composition: Manifest schema.

```
## Manifest schema (consolidated)

Global fields:
- staleness_threshold_hours: integer, the Requirement-2 threshold. An
  instrumented hypothesis — retune from what actually fires. Ratified
  starting value: 168 (ratified 2026-07-27).
- accepted_gap_count: integer, the ONE counted pin. The checker counts actual
  ACCEPTED_GAP entries; any mismatch is PIN_MISMATCH. Widening the exemption
  set forces touching this number in the same diff.

Per-repo fields:
- path: filesystem location relative to a single configured root, never a
  hardcoded absolute path. All git commands against that repo run via
  `git -C <path>`; `git show` is never assumed to resolve in the invoking
  repo's object store.
- canonical_remote_url: the pinned remote asserted by Requirement 2.

Per-document fields:
- source repo + path; header_format per structural decision 5 (schema now,
  values pending card 2c450718); copies[].

Per-copy fields:
- repo, path, status REQUIRED | ACCEPTED_GAP.
- ACCEPTED_GAP entries additionally carry: scope ∈ {DRIFT, MISSING_COPY} —
  an entry suppresses exactly one assertion for exactly one copy, never both —
  and, for DRIFT scope, expected_divergence: a hash of the accepted diff
  between source-at-recorded-SHA and the copy. Actual divergence not matching
  the fingerprint fires DRIFT anyway: an accepted gap is a specific known
  difference, never a blind spot for new drift on top of it.
```

AMD-8 [F13] WO — new section after Manifest schema: Installation gate.

```
## Installation gate (core.hooksPath)

`core.hooksPath` replaces `.git/hooks` wholesale. Before pointing it,
installation MUST inventory the repo's current effective hooks: the existing
`core.hooksPath` value if set, and any non-sample files under `.git/hooks`.
Empty → proceed. Non-empty → chain-load or explicitly migrate each existing
hook, and record the disposition in the install notes. Silently orphaning a
pre-existing hook is a failed installation, not a side effect.
```

AMD-9 [F1] WO — Known limit section, append second paragraph.

```
A second limit: the pre-push hook is a liveness guarantee for the checker on
claunker-ops pushes only — it is not universal push-time interception. Drift
introduced and pushed from another repo is caught at the next
claunker-ops-side run, not at that repo's push. Detection latency for
foreign-repo pushes is bounded by claunker-ops activity; this is accepted,
not fixed, and a global core.hooksPath is explicitly rejected as the remedy
(it would hijack every repo on the machine and orphan their hooks).
```

AMD-10 WO — Provenance section, append.

```
Amended to v2 2026-07-27 per adversarial review round 1: thirteen reviewer
findings (F1–F13) plus one adjudication-surfaced item (A1, ACCEPTED_GAP
scope) dispositioned; raw teardown and adjudication committed alongside this
document in review-packets/. Requirements 2, 3, and 5 amended on source card
1486ea8a in the same arc, prior wording preserved in edit_audit.
```
