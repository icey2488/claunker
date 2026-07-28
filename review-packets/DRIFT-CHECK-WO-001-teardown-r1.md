# WO-001 adversarial teardown — round 1 (Gemini, raw)

Received 2026-07-27. Reviewed target: review-packets/DRIFT-CHECK-WO-001-review-packet.md (document section byte-verified against git show f9f4423). Findings verbatim as returned; adjudication in DRIFT-CHECK-WO-001-adjudication-r1.md.

F1 [BLOCKER] Pre-push hook location cannot intercept drifted pushes from other repositories

* Anchor: "Liveness is a pre-push hook in claunker-ops wired via core.hooksPath"
* Failure: 1. A developer modifies a governed document in a different repository (e.g., `kanbantt-app`). 2. The developer runs `git push` from inside `kanbantt-app`. 3. Because the hook lives exclusively in `claunker-ops` and Git only executes hooks for the repository initiating the push, the hook never fires. 4. Drifted content is pushed upstream without the check ever executing.
* Fix: Specify that `core.hooksPath` must be configured globally or installed individually within every governed repository.

F2 [BLOCKER] Gated `--fix` mutations are invisible to the mandatory committed-bytes check

* Anchor: "Comparison must be against COMMITTED normalized bytes via git show, never working-tree bytes"
* Failure: 1. The script detects drift during a push attempt. 2. The user executes the script with `--fix` and confirms the prompt. 3. The script overwrites the working-tree copy with correct bytes. 4. The user attempts to push again. 5. The pre-push hook runs the check again, reading only COMMITTED bytes via `git show`, bypassing the uncommitted fix in the working tree, failing the check, and trapping the user in a rejection loop.
* Fix: Require the script to either refuse `--fix` if the working tree is dirty, or explicitly stage and commit the mutated copy automatically.

F3 [BLOCKER] Resolving staleness with `--fix` orphans the provenance header SHA

* Anchor: "`--fix` writes source over copy"
* Failure: 1. The script detects STALENESS because the source HEAD has advanced past the recorded SHA. 2. The user runs `--fix`. 3. The script overwrites the copy body with the new source body but completely fails to update the provenance header. 4. The script exits successfully. 5. The very next run compares the new body against the old recorded SHA, sees a mismatch, and immediately fails with CONTENT DRIFT.
* Fix: Require `--fix` to parse and update the provenance header's recorded SHA to the new source HEAD when repairing staleness.

F4 [BLOCKER] Duplicate-filename detection scope fundamentally precludes missing-copy detection

* Anchor: "Scope is DUPLICATE-FILENAME detection across repos... Two exit codes separating DRIFT from MISSING_COPY"
* Failure: 1. A required document copy is accidentally deleted or never created in a target repository. 2. The script executes its dynamic duplicate-filename discovery scan to find files to check. 3. Because the file is missing, no duplicate filename exists. 4. The script skips the expected target entirely, rendering the mandated MISSING_COPY exit code technically impossible to reach.
* Fix: Base the discovery scope strictly on static paths declared in the manifest rather than dynamic filename matching.

F5 [BLOCKER] Non-negotiable acceptance criteria validates and passes a fundamentally broken control

* Anchor: "Build at least one fixture repo pair (source + propagated copy) where the copy is deliberately mutated"
* Failure: 1. A builder implements a script that checks ONLY for content drift, completely ignoring the STALENESS, MISSING_COPY, and Abort-never-fetch requirements. 2. The script runs against the mandated mutated and clean fixture repos. 3. It passes all stated criteria perfectly. 4. A control that silently ignores 75% of its defined failure modes is shipped as fully verified and trusted.
* Fix: Expand the mandatory fixture bite test to include explicit test runs for staleness, missing copies, and stale fetch aborts.

F6 [BLOCKER] Cross-repository execution paths and object stores are functionally undefined

* Anchor: "Comparison must be against COMMITTED normalized bytes via git show"
* Failure: 1. The script discovers a governed file in a neighboring repository. 2. It executes `git show <SHA>:<path>` to read the source bytes. 3. Git throws a fatal error because the source SHA does not exist in the executing directory's current object store. 4. The script crashes because the work order provides no mechanism to locate or map the `--git-dir` of sibling repositories.
* Fix: Require the manifest to define relative filesystem paths to sibling repositories so git commands can target the correct object stores.

F7 [BLOCKER] Freshness heuristic is bypassed by fork clones, silently passing drift

* Anchor: "Freshness is max(FETCH_HEAD mtime, .git creation time)"
* Failure: 1. A user freshly clones a deeply outdated personal fork of the source repository. 2. The `.git` creation time is evaluated as "now", rendering it perfectly fresh. 3. The user is technically not "behind-origin" because their origin is their own stale fork. 4. The script tests the copy against the stale source, finds no drift, and silently exits 0 on fundamentally drifted documentation.
* Fix: Require validating freshness against a explicitly configured canonical remote and a minimum absolute commit timestamp.

F8 [BLOCKER] Abort-never-fetch condition is a logical paradox that never triggers

* Anchor: "Abort on behind-origin AND on stale fetch; never fetch."
* Failure: 1. The script evaluates if the local repository is `behind-origin`. 2. Because the script is strictly forbidden to fetch ("never fetch"), the local tracking refs (e.g., `origin/main`) remain entirely static. 3. Unless the user manually fetches out-of-band, git has no awareness of upstream changes, meaning `behind-origin` consistently evaluates to false. 4. Because the abort requires BOTH conditions (`AND`), the script will mathematically never abort.
* Fix: Change the logical operator to OR, or remove the `behind-origin` requirement if fetching is strictly forbidden.

F9 [MAJOR] Git worktree topologies silently break the freshness invariant

* Anchor: "because FETCH_HEAD is absent on a never-fetched clone"
* Failure: 1. The repository is cloned or checked out using a git worktree. 2. The script stats `.git`, which in a worktree is a text file pointing elsewhere rather than a directory. 3. `FETCH_HEAD` does not exist adjacent to this file. 4. Its creation time represents the local worktree creation, not the repository sync time. 5. The script either crashes attempting to read paths or evaluates freshness incorrectly.
* Fix: Mandate using `git rev-parse --git-dir` to locate the true git directory before statting metadata files.

F10 [MAJOR] Unmapped exit codes for STALENESS and abort conditions

* Anchor: "Two exit codes separating DRIFT from MISSING_COPY"
* Failure: 1. The script detects STALENESS or a stale fetch. 2. The builder reaches the exit condition, but the requirements rigidly mandate exactly "Two exit codes" mapped specifically to DRIFT and MISSING_COPY. 3. The builder must blindly guess whether to overload the drift code, overload the missing-copy code, or violate the explicitly constrained two-code limit to report the other states.
* Fix: Explicitly define and map exit codes for STALENESS and the abort-never-fetch condition.

F11 [MAJOR] Staleness threshold requires blind builder hallucination

* Anchor: "The staleness threshold is an instrumented hypothesis, not a constant."
* Failure: 1. The builder attempts to implement the staleness abort check. 2. The document claims the threshold is a hypothesis but provides absolutely no starting value, variable, or external source to read it from. 3. The builder is completely blocked and must invent an arbitrary threshold integer to make the script functional.
* Fix: Provide a concrete default threshold value or specify the exact environment variable the script must parse.

F12 [MAJOR] "Counted pin" manifest schema mechanism is entirely ambiguous

* Anchor: "with ONE counted pin on the set."
* Failure: 1. The builder attempts to define the manifest schema for an `ACCEPTED_GAP`. 2. The phrase "ONE counted pin" has no data type or technical definition. 3. The builder is forced to guess whether this means a global integer threshold array, a specific Jira ticket ID string, or a boolean flag, guaranteeing the script will fail to parse the operator's intended manifest.
* Fix: Define the precise JSON/YAML schema data type and validation rule for the "counted pin" field.

F13 [MAJOR] Scope exclusion for hooksPath installation orphans existing repository hooks

* Anchor: "only the shared core.hooksPath installation coupling is in scope here"
* Failure: 1. The builder creates a new directory for hooks and points `core.hooksPath` to it. 2. The builder places only the `pre-push` script inside, as the commit-msg hook is out of scope. 3. Because `core.hooksPath` overrides the default `.git/hooks` entirely, any pre-existing hooks in the repository are instantly bypassed and disabled without warning upon deployment.
* Fix: Require the builder to either append the pre-push script to the existing `.git/hooks/pre-push` file or build a chain-loading mechanism for the new hooksPath.

BLOCKER: 8
MAJOR: 5
