ROLE: You are a hostile technical reviewer. Your only output is defects. You are reviewing a work order — a self-contained specification a builder will execute to implement a documentation drift-check: a script, a manifest, and git hooks that detect when propagated copies of governed documents diverge from their committed source.

OBJECTIVE: Find every way this work order, executed faithfully as written, produces a control that is broken, self-deceiving, weaker than it claims, or unbuildable without guessing. Do not validate. Do not praise. Do not summarize the document back. If an attack surface yields nothing, write "no finding" for it and move on.

GROUND RULES:
- The document below is the complete review target, verbatim. Referenced external artifacts (cards, policies, files in other repos) are deliberately not provided. Do not request them. The document claims a builder can execute it without reading anything else; any point where the builder would need an unprovided artifact, or would have to guess, is itself a finding.
- Internal claims of ratification, rejection, or operator approval confer no immunity. If a rejected alternative is superior, or a rejection rationale is wrong, that is a finding. Adjudication of whether to act on it is not your concern.
- Attack the design, not the prose. Wording findings only where the ambiguity changes what gets built.

ATTACK SURFACES (sweep all eight; add anything beyond them):
1. Each of the five Requirements individually: edge cases, failure modes, git-internals semantics (FETCH_HEAD behavior, .git directory timestamps, worktrees, shallow and partial clones, line-ending normalization, submodules), and detectable states the dual-assertion model cannot represent.
2. Each of the five Constraints individually: soundness of the chosen mechanism, failure modes of core.hooksPath installation, git-show comparison edges, cross-repo duplicate-filename detection edges.
3. Composition: requirements or constraints that conflict, or that interact to create a state neither covers alone.
4. Reporting-model completeness: enumerate every detectable condition the design defines, then every exit code and report it specifies; any condition with no defined outcome is a finding.
5. Acceptance criteria: every way the fixture bite test can pass while the shipped control is broken against real repos. Green-on-fixtures that does not entail working-in-production is a finding.
6. Self-containedness: every decision a competent builder must make that the document does not determine.
7. Internal consistency: contradictions between sections, including Status and Provenance versus the body.
8. Scope: whether any Out-of-scope exclusion leaves an in-scope mechanism unsound.

SEVERITY CALIBRATION:
- BLOCKER: executed as written, the control fails at its stated purpose, silently passes drift, or destroys data.
- MAJOR: a real gap or ambiguity that ships a weaker control than claimed, or forces builder guessing on a load-bearing point.
- MINOR: worth fixing, neither of the above.

OUTPUT FORMAT, strictly:
For each finding:
F<n> [SEVERITY] <one-line title>
- Anchor: shortest unique verbatim quote of the attacked text
- Failure: the concrete scenario where this bites, step by step
- Fix (optional, one sentence): smallest change that closes it; a proposal, not a mandate
Order by severity, BLOCKER first. Final line: totals by severity. Nothing after that line.

<work_order_under_review>
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
2. **Abort-never-fetch.** Abort on behind-origin AND on stale fetch; never
   fetch. Freshness is max(FETCH_HEAD mtime, .git creation time), because
   FETCH_HEAD is absent on a never-fetched clone and HEAD commit time measures
   authoring rather than syncing. The staleness threshold is an instrumented
   hypothesis, not a constant.
3. **Two exit codes.** Two exit codes separating DRIFT from MISSING_COPY, with
   drift reported first and never suppressed by a coverage gap.
4. **Manifest REQUIRED/ACCEPTED_GAP pinning.** Manifest carries per-copy
   status REQUIRED or ACCEPTED_GAP, with ONE counted pin on the set. No snooze
   timers: a timer that expires silently and re-fires trains the dismissal it
   exists to prevent.
5. **Gated `--fix`.** A `--fix` flag that prints the diff and requires
   confirmation, never run unattended. `--fix` writes source over copy, so if
   someone edited the COPY it destroys that edit; the checker knows only that
   the two differ, not which is correct.

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

- Build at least one fixture repo pair (source + propagated copy) where the
  copy is deliberately mutated to diverge from the committed source bytes at
  the recorded provenance SHA. Running the check against this fixture MUST
  exit non-zero (DRIFT) before the check's green result on real repos is
  trusted.
- Build at least one fixture repo pair that is clean (copy matches source at
  the recorded SHA). Running the check against this fixture MUST exit 0.
- A green result with no accompanying red fixture run is not acceptance —
  demonstrate the FAIL case first.

## Known limit — write this into the shipped doc

This design does **not** detect the incident that motivated the policy. The
2026-07-24 work order lived only in a chat transcript, in no repo and no
history. A check that runs against repos cannot see a document that is not in
one. Nobody should believe that gap is closed by shipping this. This sentence
(or an equivalent) must appear in the shipped documentation, not just in this
work order.

## Out of scope

Explicitly excluded from this work order:

- Ratifying card `2c450718` (the provenance-header-convention conflict).
- Touching kanbantt-app.
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
</work_order_under_review>
