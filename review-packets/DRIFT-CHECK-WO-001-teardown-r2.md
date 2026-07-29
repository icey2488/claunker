# WO-001 adversarial teardown — round 2 (Gemini, raw)

Received 2026-07-28. Reviewed target: review-packets/DRIFT-CHECK-WO-001-review-packet-r2.md (v2 document byte-verified against git show 8a5440d, 13822 == 13822). Findings verbatim as returned; adjudication in DRIFT-CHECK-WO-001-adjudication-r2.md.

F1 [BLOCKER] Ambiguous behind-origin check silently passes drift

* Anchor: `behind-origin per its last-fetched remote refs`
* Failure: Git repositories track origin state per-branch, not per-repo. Because the document fails to specify which local branch to evaluate, a builder defaulting to checking `HEAD` will cause the freshness gate to pass when the user is currently on a fully synced local feature branch, even if the primary branch containing the source document is severely stale. This evaluates against stale bytes and silently passes drift.
* Fix: Explicitly specify that the branch being evaluated for freshness must be the repository's default branch (e.g., `main`), regardless of what `HEAD` is checked out.

F2 [BLOCKER] Impossible precedence logic between DRIFT and HEADER_UNPARSEABLE

* Anchor: `neither drift nor staleness evaluable for that copy`
* Failure: The Outcome Map mandates that `DRIFT` has higher precedence than `HEADER_UNPARSEABLE`, and Requirement 3 dictates drift is never suppressed by any abort. However, if a header is unparseable, the script mathematically cannot extract the provenance SHA required to evaluate drift. A builder executing this verbatim must either invent a fake SHA to perform the diff or violate the "never suppressed" precedence constraint.
* Fix: Change the precedence list so `HEADER_UNPARSEABLE > DRIFT`, and explicitly exempt unparseable headers from the "never suppressed" rule.

F3 [MAJOR] MISSING_COPY is permanently masked by HEADER_UNPARSEABLE

* Anchor: `HEADER_UNPARSEABLE > STALENESS > MISSING_COPY`
* Failure: If a manifest-REQUIRED copy is deleted from the filesystem, it intrinsically lacks a provenance header. Because `HEADER_UNPARSEABLE` possesses higher precedence than `MISSING_COPY`, evaluating a missing file will yield a "provenance header absent" state and exit 4, bypassing exit 2. The script will never successfully report the `MISSING_COPY` condition.
* Fix: Move `MISSING_COPY` above `HEADER_UNPARSEABLE` in the precedence hierarchy.

F4 [MAJOR] Duplicate-filename scan universe is undefined

* Anchor: `Scope is DUPLICATE-FILENAME detection across repos`
* Failure: The work order mandates scanning "across repos" but does not instruct the script on how to discover the universe of repositories on the host machine. Since the manifest only tracks repos that already contain governed files, a duplicate copy placed in a previously unmanifested repository will be entirely invisible unless the builder invents an undocumented filesystem crawling mechanism.
* Fix: Add a `monitored_repositories` list to the manifest schema to explicitly define the boundaries of the scan.

F5 [MAJOR] "Governed-looking" definition is forbidden and omitted

* Anchor: `duplicate-filename scan found a governed-looking copy with no manifest entry`
* Failure: Constraint 1 explicitly forbids using "filename globs and not a governance/ directory" to scope the scan. However, the Outcome Map introduces the phrase "governed-looking copy" without defining the criteria. Without globs or directories, the builder has no programmatic way to distinguish a coincidental filename match (e.g., a generic `README.md`) from a governed duplicate, forcing arbitrary guessing.
* Fix: Explicitly define "governed-looking" (e.g., "any file containing a valid provenance header pattern").

F6 [MAJOR] `--fix` actions break ACCEPTED_GAP manifest state

* Anchor: `A STALENESS fix writes copy body := source body at source HEAD`
* Failure: If a copy has an `ACCEPTED_GAP` for `DRIFT`, it has an `expected_divergence` fingerprint stored in the manifest. When `--fix` executes for staleness, it overwrites the copy body to match the source body identically, destroying the accepted gap in the working tree. Because the script does not rewrite the manifest to remove the `ACCEPTED_GAP` entry, the subsequent run will detect a fingerprint mismatch and fail with `DRIFT`.
* Fix: Require the STALENESS fix to also purge any corresponding `ACCEPTED_GAP` entries for that copy within the manifest.

F7 [MAJOR] Missing cryptographic parameters for expected_divergence

* Anchor: `expected_divergence: a hash of the accepted diff`
* Failure: The document dictates storing "a hash" but omits both the cryptographic algorithm (e.g., SHA-256) and the exact format of the diff to be hashed (e.g., raw unified diff, `git patch-id`). A builder must invent these parameters, guaranteeing the control will falsely report `DRIFT` when evaluated by another operator whose environment or tools assume a different hashing standard.
* Fix: Define the exact hashing pipeline, such as `git diff <source> <copy> | git patch-id`.

F8 [MAJOR] Out-of-scope header formats make `--fix` unbuildable

* Anchor: `re-emitted in that document's manifest-declared header format`
* Failure: The document explicitly claims a builder can execute it without reading unprovided artifacts. However, it leaves the actual header format values out of scope (pending card `2c450718`). Because `--fix` must re-emit the header in the correct format, a builder cannot implement the fix functionality for real documents without halting to guess the format or acquiring the excluded external artifact.
* Fix: Provide the exact header formats within this work order, or block execution until card `2c450718` is ratified.

F9 [MAJOR] Exclusion of kanbantt-app breaks duplicate-filename scan

* Anchor: `Touching kanbantt-app.`
* Failure: The duplicate-filename scan must operate "across repos" to detect unmanifested files. However, the Out-of-scope section strictly forbids "touching kanbantt-app". If the script respects the exclusion and skips kanbantt-app, it creates a fatal blind spot for duplicates. If it scans kanbantt-app to verify filenames, it violates the exclusion. This leaves the duplication mechanism structurally unsound.
* Fix: Clarify whether "touching" strictly prohibits read-only scanning, and explicitly exempt the duplicate-filename scanner if so.

F10 [MAJOR] FRESHNESS fixture allows shipping broken worktree support

* Anchor: `FRESHNESS fixture: a source repo whose freshness signal exceeds the threshold.`
* Failure: Requirement 2 explicitly forces git metadata paths to be resolved via `git rev-parse --git-common-dir` to gracefully handle git worktrees. However, the `FRESHNESS fixture` only mandates testing against "a source repo". A builder can pass the green-on-fixture test using a standard primary clone while failing to implement the worktree logic, shipping a script that breaks in production.
* Fix: Mandate that the `FRESHNESS fixture` must be constructed and executed as a git worktree.

F11 [MINOR] Remote URL matching logic is incomplete

* Anchor: `assert the repo's remote URL matches the manifest-pinned canonical remote`
* Failure: Git repositories commonly possess multiple configured remotes (e.g., `origin`, `upstream`). The document does not specify whether the canonical URL must match a specific named remote or any remote in the configuration. A builder might assume `origin`, which will break the freshness control for an operator legitimately using `upstream`.
* Fix: Specify that the script must check all configured remotes for a match.

F12 [MINOR] Liveness installation lacks an execution trigger

* Anchor: `Before pointing it, installation MUST inventory the repo's current effective hooks`
* Failure: Git configurations such as `core.hooksPath` do not propagate automatically upon running `git clone`. While the document specifies the logic of the installation gate, it fails to define what command actually triggers this installation on a fresh machine. If a developer clones the repository and pushes without manually discovering the installation script, the pre-push control is bypassed entirely.
* Fix: Specify the explicit entry point and trigger for the installation gate (e.g., "Triggered automatically via a Makefile initialization").

BLOCKER: 2, MAJOR: 8, MINOR: 2
