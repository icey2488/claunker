# DRIFT-CHECK-WO-001 — Documentation Drift Check

Source card: `1486ea8a-f27c-41e9-8a16-7a9f552583d2` — "Drift-check design exists only in a chat transcript"

Status: drafting-complete AS WRITTEN, ratified by operator 2026-07-27. All five
structural decisions below ship as constraints. The NON-NEGOTIABLE fixture-repo
bite test is a requirement, not an option. This document is self-contained: a
builder should be able to execute it without reading any chat transcript.

Background: the documentation drift-check was designed in full on 2026-07-25
across two adversarial review rounds, and none of it made it into any repo.
This is precisely the failure DOC-PROPAGATION-POLICY exists to prevent,
recurring during the design of its own remedy. This work order is the fix for
that recurrence — it is the durable record the design was missing.

## Requirements (RATIFIED DESIGN, five items)

Quoted from the source card, verbatim:

1. **Dual-assertion model.** Two separate assertions: CONTENT DRIFT (copy body
   differs from source body at the SHA its provenance header records) and
   STALENESS (source HEAD for that path has moved past the recorded SHA). Both
   recorded incidents were the second kind, so a body-only diff catches
   neither.
2. **Abort-never-fetch.** All source-side evaluations pin to the repo's
   manifest-declared `canonical_branch` (main unless declared otherwise),
   never to HEAD or the checked-out branch. Abort if EITHER the source repo's
   canonical branch is behind `origin/<canonical_branch>` per its last-fetched
   remote refs OR its last fetch is older than the staleness threshold; never
   fetch. Before evaluating freshness, assert that the remote named `origin`
   exists and its URL matches the manifest-pinned canonical remote; an absent
   `origin` or a mismatch is its own abort condition (REMOTE_MISMATCH), never
   a freshness pass — other configured remotes are irrelevant to the pin.
   Freshness is max(FETCH_HEAD mtime, git-directory creation time), with all
   git metadata paths resolved via `git rev-parse --git-common-dir` — never a
   literal `.git` stat, because worktrees make `.git` a file and relocate
   FETCH_HEAD — and because FETCH_HEAD is absent on a never-fetched clone and
   commit timestamps measure authoring rather than syncing. The staleness
   threshold lives in the manifest as an instrumented hypothesis, not a
   constant.
3. **Total outcome map.** Every detectable condition has a distinct exit code
   and a defined report (see Outcome map). DRIFT and MISSING_COPY remain
   distinct codes; all detected conditions are reported in every run; drift is
   reported first and is never suppressed by a coverage gap or by any abort.
4. **Manifest REQUIRED/ACCEPTED_GAP pinning.** Manifest carries per-copy
   status REQUIRED or ACCEPTED_GAP, with ONE counted pin on the set. No snooze
   timers: a timer that expires silently and re-fires trains the dismissal it
   exists to prevent.
5. **Gated `--fix`, split by finding.** A `--fix` flag that prints the diff and
   requires confirmation, never run unattended. A DRIFT fix writes copy body :=
   source body at the copy's RECORDED provenance SHA; the header is untouched.
   A STALENESS fix writes copy body := source body at the tip of the source's
   manifest-declared canonical branch AND rewrites the provenance header's
   recorded SHA to that tip, re-emitted in that document's manifest-declared
   header format; body and header change together or not at all. `--fix`
   REFUSES to run on any copy carrying an ACCEPTED_GAP entry of any scope:
   resolving a gap-bearing copy is an operator manifest decision (drop the
   entry or re-fingerprint at the new SHA), never a script action. The script
   never writes the manifest — the manifest is a counted-pin control surface,
   and every change to it is an operator diff. `--fix` writes source over
   copy, so if someone edited the COPY it destroys that edit; the checker
   knows only that the two differ, not which is correct. After any fix the
   script states that only the working tree changed and a commit is required
   before the check can go green; the script never stages or commits.

## Constraints (STRUCTURAL DECISIONS, five items)

Quoted from the source card, verbatim, each with its stated rationale:

1. **Duplicate-filename detection scope.** Scope is DUPLICATE-FILENAME
   detection across repos, not filename globs and not a governance/ directory.
   Rationale: the kanbantt-mcp-spec case failed at propagation time rather
   than authoring time, and neither alternative would have caught it.
2. **Liveness = pre-push hook in claunker-ops via core.hooksPath.** Liveness
   is a pre-push hook in claunker-ops wired via `core.hooksPath`, because
   `.git/hooks` is unversioned and its absence would otherwise be invisible.
3. **Manifest and script both live in claunker-ops.** Both the manifest and
   the script live in claunker-ops.
4. **Comparison against committed normalized bytes via `git show`, never
   working-tree.** Comparison must be against COMMITTED normalized bytes via
   `git show`, never working-tree bytes, because `.gitattributes` normalizes
   line endings in the object store.
5. **Manifest declares provenance header format per document.** The manifest
   must declare the provenance header format PER DOCUMENT. Two incompatible
   conventions are live (markdown blockquote on HARNESS-FIDELITY-CONTRACT.md,
   self-describing HTML comment on kanbantt-mcp-spec.md) and no single parser
   handles both. See card `2c450718`.

## Outcome map (total)

| Condition | Exit | Meaning |
|---|---|---|
| CLEAN | 0 | all assertions green |
| DRIFT | 1 | copy body ≠ source body at recorded SHA (or ACCEPTED_GAP fingerprint mismatch) |
| MISSING_COPY | 2 | manifest-REQUIRED copy absent |
| STALENESS | 3 | source canonical-branch tip moved past recorded SHA |
| HEADER_UNPARSEABLE | 4 | file exists but its provenance header is absent or fails its declared format; drift and staleness unevaluated for that copy — a missing file is MISSING_COPY, never HEADER_UNPARSEABLE |
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

Precedence is run-wide over DETECTED conditions: it selects the exit code
when multiple conditions are detected in one run, with every detected
condition still reported. It is not a per-copy evaluation order. For a copy
whose header is unparseable, drift and staleness are UNEVALUATED for that
copy — unevaluated is not suppressed, and the never-suppressed rule governs
detected drift only. A run with one unparseable header and a detected drift
on another copy exits DRIFT, with HEADER_UNPARSEABLE in the report.

## Discovery composition

The manifest is the sole authority for REQUIRED coverage: MISSING_COPY is
evaluated against manifest entries, never against scan results. The
duplicate-filename scan is a coverage net for UNMANIFESTED duplicates only —
a governed filename appearing across repos with no manifest entry. A missing
manifest-REQUIRED copy therefore cannot be masked by the scan finding
nothing; an unmanifested duplicate is its own reportable condition per the
Outcome map. The scan discovers gaps in the manifest; it never defines the
check's coverage.

The scan universe is exactly the manifest's per-repo entries: every
manifest-listed repo is scanned, and a repo absent from the manifest is
outside governance entirely — a known limit, recorded, not a detection
promise. "Governed-looking" is defined as: a file whose basename exactly
matches the basename of any manifest-declared governed document.
Exact-name matching against the manifest's own document set is neither a
filename glob nor a directory convention, so Constraint 1 stands.

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
- canonical_branch: the branch all source-side evaluations pin to (main
  unless declared otherwise). Freshness reads `origin/<canonical_branch>`;
  the canonical_remote_url pin applies to the remote named `origin`.

Per-document fields:
- source repo + path; header_format per structural decision 5 (schema now,
  values pending card 2c450718); copies[].

Per-copy fields:
- repo, path, status REQUIRED | ACCEPTED_GAP.
- ACCEPTED_GAP entries additionally carry: scope ∈ {DRIFT, MISSING_COPY} —
  an entry suppresses exactly one assertion for exactly one copy, never both —
  and, for DRIFT scope, expected_divergence: the SHA-256 hex digest of the
  accepted diff, computed by a pinned pipeline: materialize
  source-at-recorded-SHA to a temp file via git show; run
  `git diff --no-index --no-color <temp> <copy>`; strip the path-bearing
  header lines (`diff --git`, `index`, `---`, `+++`); hash the remaining
  bytes with SHA-256. Actual divergence not matching the fingerprint fires
  DRIFT anyway: an accepted gap is a specific known difference, never a
  blind spot for new drift on top of it.

## Installation gate (core.hooksPath)

`core.hooksPath` replaces `.git/hooks` wholesale. Before pointing it,
installation MUST inventory the repo's current effective hooks: the existing
`core.hooksPath` value if set, and any non-sample files under `.git/hooks`.
Empty → proceed. Non-empty → chain-load or explicitly migrate each existing
hook, and record the disposition in the install notes. Silently orphaning a
pre-existing hook is a failed installation, not a side effect.

Entry point: the installation gate ships as an explicit install script in
claunker-ops alongside the checker (a deliverable of this work order),
invoked manually once per clone; it performs the inventory above before
pointing `core.hooksPath`. Nothing runs it automatically: a fresh clone has
no hook until the operator invokes it — recorded in the Known limit section
as part of the liveness ceiling.

## Open dependency

Structural decision 5 references card `2c450718` — "Two incompatible
provenance conventions coexist across governed docs" (state: created,
unratified as of this draft).

The manifest **schema** carries a per-document header-format field now, as
part of this work order. The **values** for propagated docs (which format
each document actually uses) await ratification of `2c450718`.

As a builder-verifiable suggestion — **not a ratified decision** — ACCEPTED_GAP
is a plausible day-one mechanism for the known non-material
HARNESS-FIDELITY-CONTRACT header-wording divergence (the source card records
this as a 27-byte size delta that is header wording only, with bodies already
verified byte-identical across all three repos as of job
doc-drift-baseline-20260725, card `a5c14f90`). The builder should confirm this
is still true at implementation time rather than assume it.

Scope of the block: fixture repos declare their own header_format values in
the fixture manifest, so the checker and the FULL bite-test suite are
buildable and provable now, without 2c450718. What blocks on 2c450718 is
go-live against real documents: populating the production manifest's
header_format values and running `--fix` header re-emission on real copies.
The self-containedness rule is scoped accordingly: this work order is
self-contained for the build; 2c450718 is the declared, load-bearing gate
for production activation only.

## Rejected on record

Carried forward from the source card so a builder does not re-propose them:

- **The `[BUNDLE]` override token.** Rejected as an uncounted exemption
  surface.
- **A post-hoc commit-message completeness check inside this script.**
  Rejected because pushed messages are immutable, so a violation has no path
  back to green. This assertion is NOT abandoned — it moved venue. See
  "commit-msg hook coupling" below.

## Commit-msg hook coupling

The commit-message completeness assertion rejected above moved to card
`f546bb7d` — "RATIFIED REVERSAL: commit-message assertion moves to a
commit-msg hook." The commit-msg hook shares the same `core.hooksPath`
installation as the pre-push liveness hook (structural decision 2 above); the
two land together per operator plan. The commit-msg hook's own content is a
separate work order — see Out of scope.

## Acceptance criteria — NON-NEGOTIABLE fixture-repo bite test

Fixture repos where a deliberately drifted copy yields exit 1 and a clean copy
yields exit 0. This is not optional and not deferrable: without a bite test,
this is a control asserting its own correctness. Concretely:

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
- WORKTREE fixture: at least one fixture repo is exercised through a linked
  git worktree (checker invoked against the worktree path). An
  implementation that stats a literal `.git` path MUST fail it —
  red-before-green like every other assertion.
- Every outcome-map condition beyond these gets its own red fixture when its
  code path lands; a code path with no red fixture is not accepted.
- Red before green, per assertion: no assertion's green is trusted until its
  red fixture has been demonstrated to fail.

## Known limit — write this into the shipped doc

This design does **not** detect the incident that motivated the policy. The
2026-07-24 work order lived only in a chat transcript, in no repo and no
history. A check that runs against repos cannot see a document that is not in
one. Nobody should believe that gap is closed by shipping this. This sentence
(or an equivalent) must appear in the shipped documentation, not just in this
work order.

A second limit: the pre-push hook is a liveness guarantee for the checker on
claunker-ops pushes only — it is not universal push-time interception. Drift
introduced and pushed from another repo is caught at the next
claunker-ops-side run, not at that repo's push. Detection latency for
foreign-repo pushes is bounded by claunker-ops activity; this is accepted,
not fixed, and a global core.hooksPath is explicitly rejected as the remedy
(it would hijack every repo on the machine and orphan their hooks).

A third limit, same family: `core.hooksPath` is local git config and does
not propagate on clone. A fresh clone of claunker-ops has no pre-push hook
until the install script is run by hand; until then the checker's liveness
guarantee is absent on that clone.

## Out of scope

Explicitly excluded from this work order:

- Ratifying card `2c450718` (the provenance-header-convention conflict).
- Touching kanbantt-app — scoped to the BUILD: this work order ships no
  changes inside kanbantt-app. The shipped checker READS kanbantt-app at
  runtime (duplicate-filename scan and `git show` against its object store;
  it hosts governed copies, so excluding it from reads would blind the
  check's main purpose). A runtime `--fix` write to a kanbantt-app copy's
  working tree stays under the standard confirm-and-operator-commits rule.
- The commit-msg hook's own content/implementation (tracked separately per
  card `f546bb7d`; only the shared `core.hooksPath` installation coupling is
  in scope here).

## Provenance

Drafted 2026-07-27 from card `1486ea8a-f27c-41e9-8a16-7a9f552583d2`, per
operator ratification recorded the same day: the card is drafting-complete AS
WRITTEN with five structural decisions (a "three structural decisions" figure
in the 2026-07-25/26 handoff was a transcription error, now superseded).
Header-format values for propagated documents are pending ratification of card
`2c450718`.

Amended to v2 2026-07-27 per adversarial review round 1: thirteen reviewer
findings (F1–F13) plus one adjudication-surfaced item (A1, ACCEPTED_GAP
scope) dispositioned; raw teardown and adjudication committed alongside this
document in review-packets/. Requirements 2, 3, and 5 amended on source card
1486ea8a in the same arc, prior wording preserved in edit_audit.

Amended to v3 2026-07-28 per adversarial review round 2: twelve reviewer
findings (F1–F12, round-2 numbering) plus one adjudication-surfaced item
(A2, canonical-branch consistency sweep) dispositioned; raw teardown and
adjudication committed alongside in review-packets/. Requirements 2 and 5
amended on source card 1486ea8a in the same arc, prior wording preserved in
edit_audit. Round-2 headline: no design-invalidating flaw — both BLOCKERs
resolved as specification clarifications.
