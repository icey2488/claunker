# DRIFT-CHECK-WO-001 — ratified v3 amendment set (round 2)

RATIFICATION: Round-2 dispositions (F1–F12 plus A2, per DRIFT-CHECK-WO-001-adjudication-r2.md) ratified by operator 2026-07-28, in-session, in the operator's own voice: "Accepted. Proceed with v3 amendment." Sentence-level drafting delegated to the architect within the ratified dispositions. AMD-11 and AMD-12 amend Requirements text held on source card 1486ea8a-f27c-41e9-8a16-7a9f552583d2 (items 2 and 5, previously amended in round 1) and are applied to the card in the same arc (--expected-version, prior wording preserved in edit_audit) — card ride-along explicitly covered by the accepted adjudication. All amendments are specification-tightening, strictness up-or-neutral.

---

AMD-11 [F1, F11, A2] CARD — Requirement 2 rewrite (v3): canonical-branch pin, origin-named remote pin.
Replaces the full Requirements item 2 (begins "2. **Abort-never-fetch.**").

```
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
```

AMD-12 [F6, A2] CARD — Requirement 5 rewrite (v3): fix refusal on gap-bearing copies, never-writes-manifest, canonical-branch fix target.
Replaces the full Requirements item 5 (begins "5. **Gated `--fix`, split by finding.**").

```
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
```

AMD-13 [F2] WO — Outcome map, precedence clarification.
Append as a new final paragraph of the "## Outcome map (total)" section.

```
Precedence is run-wide over DETECTED conditions: it selects the exit code
when multiple conditions are detected in one run, with every detected
condition still reported. It is not a per-copy evaluation order. For a copy
whose header is unparseable, drift and staleness are UNEVALUATED for that
copy — unevaluated is not suppressed, and the never-suppressed rule governs
detected drift only. A run with one unparseable header and a detected drift
on another copy exits DRIFT, with HEADER_UNPARSEABLE in the report.
```

AMD-14 [F3, A2] WO — Outcome map table, two row replacements.
The fenced payload holds two lines. Line 1 replaces the table row beginning "| STALENESS | 3 |". Line 2 replaces the table row beginning "| HEADER_UNPARSEABLE | 4 |".

```
| STALENESS | 3 | source canonical-branch tip moved past recorded SHA |
| HEADER_UNPARSEABLE | 4 | file exists but its provenance header is absent or fails its declared format; drift and staleness unevaluated for that copy — a missing file is MISSING_COPY, never HEADER_UNPARSEABLE |
```

AMD-15 [F4, F5] WO — Discovery composition, scan universe and governed-looking definition.
Append as a new final paragraph of the "## Discovery composition" section.

```
The scan universe is exactly the manifest's per-repo entries: every
manifest-listed repo is scanned, and a repo absent from the manifest is
outside governance entirely — a known limit, recorded, not a detection
promise. "Governed-looking" is defined as: a file whose basename exactly
matches the basename of any manifest-declared governed document.
Exact-name matching against the manifest's own document set is neither a
filename glob nor a directory convention, so Constraint 1 stands.
```

AMD-16 [F7] WO — Manifest schema, expected_divergence pipeline pinned.
Replaces the entire Per-copy fields bullet beginning "- ACCEPTED_GAP entries additionally carry:" (through the end of that bullet).

```
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
```

AMD-17 [F1, F11] WO — Manifest schema, per-repo canonical_branch field.
Insert as a new bullet immediately after the Per-repo fields bullet beginning "- canonical_remote_url:".

```
- canonical_branch: the branch all source-side evaluations pin to (main
  unless declared otherwise). Freshness reads `origin/<canonical_branch>`;
  the canonical_remote_url pin applies to the remote named `origin`.
```

AMD-18 [F8] WO — Open dependency, block scoped to go-live.
Append as a new final paragraph of the "## Open dependency" section.

```
Scope of the block: fixture repos declare their own header_format values in
the fixture manifest, so the checker and the FULL bite-test suite are
buildable and provable now, without 2c450718. What blocks on 2c450718 is
go-live against real documents: populating the production manifest's
header_format values and running `--fix` header re-emission on real copies.
The self-containedness rule is scoped accordingly: this work order is
self-contained for the build; 2c450718 is the declared, load-bearing gate
for production activation only.
```

AMD-19 [F9] WO — Out of scope, kanbantt-app exclusion scoped to the build.
Replaces the Out-of-scope bullet reading "- Touching kanbantt-app." in its entirety.

```
- Touching kanbantt-app — scoped to the BUILD: this work order ships no
  changes inside kanbantt-app. The shipped checker READS kanbantt-app at
  runtime (duplicate-filename scan and `git show` against its object store;
  it hosts governed copies, so excluding it from reads would blind the
  check's main purpose). A runtime `--fix` write to a kanbantt-app copy's
  working tree stays under the standard confirm-and-operator-commits rule.
```

AMD-20 [F10] WO — Acceptance criteria, worktree fixture.
Insert as a new bullet immediately after the bullet beginning "- CLEAN fixture:" and before the bullet beginning "- Every outcome-map condition".

```
- WORKTREE fixture: at least one fixture repo is exercised through a linked
  git worktree (checker invoked against the worktree path). An
  implementation that stats a literal `.git` path MUST fail it —
  red-before-green like every other assertion.
```

AMD-21 [F12] WO — Installation gate, entry point named.
Append as a new final paragraph of the "## Installation gate (core.hooksPath)" section.

```
Entry point: the installation gate ships as an explicit install script in
claunker-ops alongside the checker (a deliverable of this work order),
invoked manually once per clone; it performs the inventory above before
pointing `core.hooksPath`. Nothing runs it automatically: a fresh clone has
no hook until the operator invokes it — recorded in the Known limit section
as part of the liveness ceiling.
```

AMD-22 [F12] WO — Known limit, clone-propagation clause.
Append as a new final paragraph of the Known limit section.

```
A third limit, same family: `core.hooksPath` is local git config and does
not propagate on clone. A fresh clone of claunker-ops has no pre-push hook
until the install script is run by hand; until then the checker's liveness
guarantee is absent on that clone.
```

AMD-23 WO — Provenance, append.

```
Amended to v3 2026-07-28 per adversarial review round 2: twelve reviewer
findings (F1–F12, round-2 numbering) plus one adjudication-surfaced item
(A2, canonical-branch consistency sweep) dispositioned; raw teardown and
adjudication committed alongside in review-packets/. Requirements 2 and 5
amended on source card 1486ea8a in the same arc, prior wording preserved in
edit_audit. Round-2 headline: no design-invalidating flaw — both BLOCKERs
resolved as specification clarifications.
```
