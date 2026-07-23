"""``Spine`` — the facade tying the store (four versioned entity tables + the
append-only ``tier_audit`` and ``archive_audit`` ledgers), ordering, and projection
together behind semantic write paths, and the home of the server write-admission
checks (the Mutation Invariants and the governed tier and archive controls).

The ``Store`` / ``EntityStore`` layer is dumb persistence: it stamps versions and
writes blobs, no cross-entity policy. The Spine is the "server" boundary that
admits or rejects writes:

    MI-1  No late children on a tombstoned parent. ``create_artifact`` /
          ``create_escalation`` reject a ``task_id`` that resolves to a
          soft-deleted Task (and, as referential hygiene, an absent Task).
    MI-2  Resolving an escalation records the human decision (``resolution``,
          ``resolution_rationale``, ``actor``) and stamps ``resolved_at`` in a
          single put — one write, no paired Task.state transition (escalation is
          not a state). The resolving ``actor`` is an operator-only invariant: the
          server derives it from the authenticated credential, never the payload,
          and ``resolve_escalation`` hard-aborts on any other actor.

(There is no MI-3 — it dissolved when 'escalated' left the state enum.)

GOVERNED TIER CONTROL (v0.3.0). The tier is the one field with a control gradient
(tier 1 self-accept .. tier 4 human), so it gets governance the free edits do not:

    retier_task   change an ALREADY-SET tier ONLY through an audited path — one
                  append-only ``tier_audit`` row per change (who/when/why + whether it
                  REDUCES oversight), written ATOMICALLY with the tier. No ``force``: a
                  re-tier re-decides against fresh state, never clobbers.
    write-once    ``update_task`` refuses to change a SET tier (only the untiered → N
                  initial classification stays free) — so the governed path is the ONLY
                  way to move a set tier, even if a client bypasses the UI.

GOVERNED ARCHIVE CONTROL (v0.4.0). ``archive_task`` / ``unarchive_task`` set/clear the
orthogonal ``archived_at`` flag ONLY through an audited path — one append-only
``archive_audit`` row per change, written atomically with the flag; loud idempotency
(already-archived / not-archived are rejections, never silent no-ops); and an
escalation gate on archive (an open escalation blocks it). No ``force``, same as
re-tier.

Reads return entity objects; ``cards()`` returns the projected, soft-delete-
omitting, escalation-badged Card view.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime as _datetime
from typing import Any, Dict, List, Optional

# R6 — durable-ref validation: reject refs that are local filesystem paths.
# A durable ref survives outside the executor environment (git hash, URL, content
# address). A local absolute path is executor-specific and will not resolve on
# replay. The check is structural (prefix-based), not semantic.
#
# Patterns rejected:
#   /path     — Unix absolute path (starts with /)
#   ~/path    — user-home relative (starts with ~)
#   C:\path   — Windows drive letter + backslash
#   C:/path   — Windows drive letter + forward slash (but NOT C://  which is URL-like)
#   \\server  — UNC path
#
# URL-like refs (e.g. https://, git://, f://) are NOT rejected:
# the :/(?!/) pattern requires the slash to NOT be followed by another slash,
# so scheme:// (double slash) passes through cleanly.
_LOCAL_PATH_RE = re.compile(r'^(?:[/~]|[a-zA-Z]:\\|[a-zA-Z]:/(?!/)|\\\\)')


def _is_durable_ref(ref: str) -> bool:
    """True iff ``ref`` is not a local filesystem path (R6)."""
    return not bool(_LOCAL_PATH_RE.match(ref))

from .entity import (
    ARTIFACT_KINDS,
    STATES,
    Artifact,
    Escalation,
    Project,
    SpineError,
    Task,
    State,
    check_created_by_limits,
)
from .ordering import append_rank
from .projection import project
from .storage import Store, utcnow_iso

# Sentinel distinguishing "not provided" from "explicitly passed as None (=clear)".
# Used in update_task for the clearable fields: effort, impact, due, depends_on.
_UNSET = object()


def _validate_due(v: Any) -> None:
    """Raise ValueError if v is not a valid ISO-8601 datetime string."""
    if not isinstance(v, str) or not v:
        raise ValueError(f"due must be a non-empty ISO-8601 string, got {v!r}")
    try:
        _datetime.fromisoformat(v)
    except ValueError:
        raise ValueError(f"due must be a valid ISO-8601 datetime string, got {v!r}")


# Actor recorded on every tier_audit row. A PLACEHOLDER: every authenticated client
# shares the single Bearer token today, so "client:bearer" is the most specific TRUE
# attribution the server can assert (the token IS the identity — light transport-level
# only). The audit column is typed (a plain string) to accept a per-user UUID/string
# later with NO schema change: at Stage 2 (distinct per-user credentials) the server
# derives the real actor from the authenticated identity and passes it to
# ``retier_task(actor=...)`` — the parameter already exists for that seam.
RETIER_ACTOR = "client:bearer"

# Actor recorded on every archive_audit row — the same placeholder pattern as
# RETIER_ACTOR, for the same reason (one shared Bearer token today; the ``actor=``
# parameter on ``archive_task`` / ``unarchive_task`` is the Stage-2 seam for a real
# per-credential identity).
ARCHIVE_ACTOR = "client:bearer"

# Actor recorded on every edit_audit row — same placeholder, same Stage-2 seam.
EDIT_ACTOR = "client:bearer"


class ConflictError(Exception):
    """Optimistic-concurrency failure on a card-write path (``update_task`` /
    ``move_task`` / ``soft_delete_task``). Raised when:

      * the target is a TOMBSTONE — immutable, per the spec; NOT even ``force``
        bypasses this (there is no undelete in v1); OR
      * not ``force`` and a supplied ``expected_version`` does not equal the task's
        current opaque ``version`` token (the spec's sole concurrency primitive,
        compared by equality only).

    Carries the FRESHLY-READ current ``Task`` so the MCP tool layer can project it
    into the spec's ``conflict`` envelope (``meta.current``), handing Kanbantt's
    reconcile immediate ground truth without an extra round trip."""

    def __init__(self, current: Task) -> None:
        self.current = current
        super().__init__(
            f"version conflict on task {current.id!r} "
            f"(current version {current.version!r}, deleted_at={current.deleted_at!r})"
        )


class Spine:
    def __init__(self, store: Optional[Store] = None) -> None:
        self.store = store or Store()

    # ── reads ────────────────────────────────────────────────────────────────
    def get_project(self, project_id: str) -> Optional[Project]:
        return self.store.projects.get(project_id)

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.store.tasks.get(task_id)

    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        return self.store.artifacts.get(artifact_id)

    def get_escalation(self, escalation_id: str) -> Optional[Escalation]:
        return self.store.escalations.get(escalation_id)

    def cards(self) -> List[Dict[str, Any]]:
        """The projected board view: soft-deleted tasks omitted, unresolved
        escalations rendered as approval badges (orthogonal to the column)."""
        return project(self.store.tasks.list_all(), self.store.escalations.list_all())

    # ── project / task writes ──────────────────────────────────────────────────
    def create_project(
        self, name: str, *, project_id: Optional[str] = None, created_at: Optional[str] = None
    ) -> Project:
        project_obj = Project(
            id=project_id or str(uuid.uuid4()),
            name=name,
            created_at=created_at or utcnow_iso(),
        )
        return self.store.projects.put(project_obj)

    def create_task(
        self,
        project_id: str,
        title: str,
        *,
        state: str = State.CREATED,
        tier: Optional[int] = None,
        acceptance_criteria: Optional[Any] = None,
        due: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
        task_id: Optional[str] = None,
        order: Optional[str] = None,
        created_at: Optional[str] = None,
        created_by: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """Create a task, append-at-end in board order. ``order`` defaults to a seed
        after the current max live rank (``rebalance`` is NEVER invoked here); a
        caller-supplied ``order`` (the spec's client-minted LexoRank CardInput field)
        is honored verbatim — opaque, sort-by-string, exactly as ``reposition`` treats
        it. Note: this does not validate ``project_id`` exists (no such MI is
        specified). ``created_by`` is write-once and create-time only in v1; the Task
        constructor validates the shape when non-null (SpineError on malformed).
        ``due`` must be null or a valid ISO-8601 string. ``depends_on`` must be a
        list of non-empty strings; self-reference (own id in list) → SpineError."""
        if state not in STATES:
            raise ValueError(f"unknown task state {state!r}")
        if due is not None:
            _validate_due(due)
        actual_deps: List[str] = depends_on if depends_on is not None else []
        if depends_on is not None:
            for entry in depends_on:
                if not isinstance(entry, str) or not entry:
                    raise SpineError(
                        f"depends_on entries must be non-empty strings, got {entry!r}"
                    )
        tid = task_id or str(uuid.uuid4())
        if actual_deps and tid in actual_deps:
            raise SpineError(f"task {tid!r} cannot depend on itself")
        # created_by ADMISSION CAPS (write-boundary, like MI-1): bound the provenance
        # payload BEFORE minting so an unbounded/hostile created_by is rejected at create
        # rather than stored immutably forever. Shape (value types) is enforced by the
        # Task constructor; this bounds size. Restore/load bypass this — already admitted.
        check_created_by_limits(created_by)
        if order is None:
            last_order = max((t.order for t in self.store.tasks.list_live()), default="")
            order = append_rank(last_order)
        task = Task(
            id=tid,
            project_id=project_id,
            title=title,
            state=state,
            tier=tier,
            acceptance_criteria=acceptance_criteria,
            due=due,
            depends_on=actual_deps,
            order=order,
            created_at=created_at or utcnow_iso(),
            created_by=created_by,
        )
        return self.store.tasks.put(task)

    def set_state(self, task_id: str, state: str) -> Task:
        """Move a task to a new state (its board column). Validates the target is a
        known state; transition legality is not guarded in this slice."""
        if state not in STATES:
            raise ValueError(f"unknown task state {state!r}")
        task = self._require_task(task_id)
        task.state = state
        return self.store.tasks.put(task)

    def assign_tier(self, task_id: str, tier: int) -> Task:
        task = self._require_task(task_id)
        task.tier = tier
        return self.store.tasks.put(task)

    def reposition(self, task_id: str, order: str) -> Task:
        """Set a task's LexoRank board ``order`` (out-of-band move)."""
        task = self._require_task(task_id)
        task.order = order
        return self.store.tasks.put(task)

    def update_task(
        self,
        task_id: str,
        *,
        title: Optional[str] = None,
        acceptance_criteria: Optional[Any] = None,
        effort: Any = _UNSET,
        impact: Any = _UNSET,
        due: Any = _UNSET,
        depends_on: Any = _UNSET,
        tier: Optional[int] = None,
        expected_version: Optional[str] = None,
        force: bool = False,
    ) -> Task:
        """Operator edit of a task's MUTABLE fields in a single get→set→put.

        RFC 7386 KEY-PRESENCE SEMANTICS (amendment 2026-07-06): for the clearable
        fields ``effort``, ``impact``, ``due``, and ``depends_on``, the caller uses
        the sentinel ``_UNSET`` default to mean "not provided / leave unchanged",
        and ``None`` (or ``[]`` for ``depends_on``) to mean "clear the field". The
        server handler maps patch key-absence to ``_UNSET`` and patch key-presence to
        the patch value (None clears; value sets).

          * at least one field must be provided (all _UNSET/None → ``ValueError``);
          * ``tier`` in 1..4 when provided (else ``ValueError``);
          * ``title`` non-empty when provided (else ``ValueError``);
          * ``due`` null or valid ISO-8601 when provided (else ``ValueError``);
          * ``depends_on`` a list of non-empty strings when provided (else ``ValueError``);
          * self-reference in ``depends_on`` → ``SpineError`` (→ validation_failed);
          * WRITE-ONCE TIER: ``tier`` here may only set an UNTIERED task's INITIAL tier;
            changing a SET tier → ``ValueError`` — governed path is ``retier_task``.

        EDIT-AUDIT LEDGER (amendment 2026-07-06): one ``edit_audit`` row per field that
        ACTUALLY changes (old ≠ new) among ``{effort, impact, due, depends_on}``,
        staged atomically with the mutation (``commit=False`` + put commits both). A
        failed guard or invariant writes NO row.

        OPTIMISTIC CONCURRENCY: ``expected_version`` checked against current token;
        mismatch → ``ConflictError``. Tombstone → ``ConflictError`` even under ``force``."""
        if (
            title is None
            and acceptance_criteria is None
            and effort is _UNSET
            and impact is _UNSET
            and due is _UNSET
            and depends_on is _UNSET
            and tier is None
        ):
            raise ValueError("update_task requires at least one field to change")
        if tier is not None and not (1 <= tier <= 4):
            raise ValueError(f"tier must be an int in 1..4, got {tier!r}")
        if title is not None and not title.strip():
            raise ValueError("title cannot be updated to an empty string")
        # due: null is valid (clear); non-null must be valid ISO-8601
        if due is not _UNSET and due is not None:
            _validate_due(due)
        # depends_on: must be a list of non-empty strings (null is rejected at the handler)
        if depends_on is not _UNSET:
            if not isinstance(depends_on, list):
                raise ValueError("depends_on must be a list of task-id strings")
            for entry in depends_on:
                if not isinstance(entry, str) or not entry:
                    raise ValueError(
                        f"depends_on entries must be non-empty strings, got {entry!r}"
                    )

        task = self._guard_mutable(task_id, expected_version=expected_version, force=force)

        # WRITE-ONCE TIER GUARD (spec v0.3.0 §Re-tier)
        if tier is not None and task.tier is not None and tier != task.tier:
            raise ValueError("tier is write-once; use card_retier to change a set tier")

        # SELF-REFERENCE CHECK: needs current task.id, done after gate so tombstone
        # immutability fires first (ConflictError > SpineError, preserving existing order).
        if depends_on is not _UNSET and task_id in depends_on:
            raise SpineError(f"task {task_id!r} cannot depend on itself")

        # Capture old values for the edit-audit ledger BEFORE mutation.
        now = utcnow_iso()
        audit_entries: List[Dict[str, Any]] = []
        if effort is not _UNSET and task.effort != effort:
            audit_entries.append({"field": "effort", "old": task.effort, "new": effort})
        if impact is not _UNSET and task.impact != impact:
            audit_entries.append({"field": "impact", "old": task.impact, "new": impact})
        if due is not _UNSET and task.due != due:
            audit_entries.append({"field": "due", "old": task.due, "new": due})
        if depends_on is not _UNSET and task.depends_on != depends_on:
            audit_entries.append({
                "field": "depends_on",
                "old": list(task.depends_on),
                "new": list(depends_on),
            })

        # Apply mutations.
        if title is not None:
            task.title = title
        if acceptance_criteria is not None:
            task.acceptance_criteria = acceptance_criteria
        if effort is not _UNSET:
            task.effort = effort
        if impact is not _UNSET:
            task.impact = impact
        if due is not _UNSET:
            task.due = due
        if depends_on is not _UNSET:
            task.depends_on = depends_on
        if tier is not None:
            task.tier = tier

        # Stage edit-audit rows (commit=False); put commits everything atomically.
        for entry in audit_entries:
            self.store.append_edit_audit({
                "id": str(uuid.uuid4()),
                "card_id": task.id,
                "field": entry["field"],
                "old": entry["old"],
                "new": entry["new"],
                "actor": EDIT_ACTOR,
                "ts": now,
            }, commit=False)

        return self.store.tasks.put(task)

    def move_task(
        self,
        task_id: str,
        to_state: str,
        *,
        order: Optional[str] = None,
        expected_version: Optional[str] = None,
        force: bool = False,
    ) -> Task:
        """Operator move: set ``state`` (the board column) and, if ``order`` is given,
        the LexoRank board position — in a single get→set→put. The move is FREE: NO
        transition-legality check (any state in ``STATES`` is accepted, adjacent or
        not), per the ratified 'manual operator edits are ungoverned' stance. Only the
        target ``to_state`` is validated (a known state, else ``ValueError``). Unknown
        ``task_id`` → ``KeyError`` (→ not_found).

        OPTIMISTIC CONCURRENCY: as ``update_task`` — ``expected_version`` is checked
        (skippable by ``force``); a tombstone is immutable even under ``force``. The
        ``to_state`` validation precedes the concurrency gate. See ``_guard_mutable``."""
        if to_state not in STATES:
            raise ValueError(f"unknown task state {to_state!r}")
        task = self._guard_mutable(task_id, expected_version=expected_version, force=force)
        task.state = to_state
        if order is not None:
            task.order = order
        return self.store.tasks.put(task)

    def soft_delete_task(self, task_id: str, *, expected_version: Optional[str] = None) -> Task:
        """Tombstone a task (soft delete): stamp ``deleted_at`` and re-put, so the row
        and its data are RETAINED (auditable, recoverable) while the card is omitted
        from the board projection. Unknown ``task_id`` → ``KeyError`` (→ not_found).

        OPTIMISTIC CONCURRENCY (spec §Concurrency / §Deletion): ``expected_version`` is
        checked against the current ``version`` token; on mismatch a ``ConflictError``
        carrying the current task is raised. Deletion has NO ``force`` — the spec's
        deliberate asymmetry: destructive ops never get a bypass. Deleting an ALREADY-
        tombstoned task raises ``ConflictError`` (the tombstone) — tombstones are
        immutable. See ``_guard_mutable``."""
        task = self._guard_mutable(task_id, expected_version=expected_version, force=False)
        task.deleted_at = utcnow_iso()
        return self.store.tasks.put(task)

    # ── governed re-tier (audited control) ─────────────────────────────────────
    def retier_task(
        self,
        task_id: str,
        new_tier: int,
        *,
        reason: str,
        expected_version: Optional[str] = None,
        actor: str = RETIER_ACTOR,
    ) -> Task:
        """Governed re-tier (kanbantt-mcp-spec v0.3.0 §Re-tier): change a task's
        ALREADY-SET tier to a DIFFERENT valid tier, atomically recording one append-only
        ``tier_audit`` row. UNLIKE the free operator edits this is a GOVERNED control —
        the change is audited (who / when / why, and whether it REDUCES oversight) and
        has NO ``force``: a re-tier always runs against FRESH state. A version mismatch
        raises ``ConflictError`` (re-fetch + re-decide); it NEVER clobbers.

        Gate THEN invariants:

          1. ``_guard_mutable`` (force always False): unknown ``task_id`` → ``KeyError``
             (→ not_found); a TOMBSTONE → ``ConflictError`` (immutable); a supplied
             ``expected_version`` that does not match → ``ConflictError`` carrying the
             fresh card. Failing here writes NO audit row.
          2. its own invariants, each → ``ValueError`` (→ validation_failed):
             * the card must CURRENTLY be tiered — else
               "card is untiered; set the initial tier via card_update" (RE-TIER ONLY:
               there is no N → null clear in v1);
             * ``new_tier`` must be in 1..4 — else out of range;
             * ``new_tier`` must DIFFER from the current tier — else "new_tier equals
               current tier; nothing to change" (NO no-op audit row);
             * ``reason`` must be non-empty after ``.strip()`` — else "retier requires a
               non-empty reason".

        On success, in a SINGLE transaction (one commit): rewrite the tier (the
        projection re-emits the ``"tier:N"`` tag; every OTHER tag is derived and left
        untouched) AND append one ``tier_audit`` row —

            reduces_control = (new_tier < old_tier)   # int 0/1; tier 1 = self-accept
                              (weakest oversight) .. tier 4 = human (strongest), so a
                              LOWER new tier REDUCES control
            actor           = the authenticated-client placeholder (see ``RETIER_ACTOR``)
            ts              = ISO-8601 UTC

        Returns the re-tiered Task (freshly version-stamped). ``reason`` is recorded
        verbatim (validated, not trimmed — the ledger preserves what was submitted)."""
        task = self._guard_mutable(task_id, expected_version=expected_version, force=False)
        if task.tier is None:
            raise ValueError("card is untiered; set the initial tier via card_update")
        if not (1 <= new_tier <= 4):
            raise ValueError(f"new_tier must be an int in 1..4, got {new_tier!r}")
        if new_tier == task.tier:
            raise ValueError("new_tier equals current tier; nothing to change")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("retier requires a non-empty reason")

        old_tier = task.tier
        audit_row = {
            "id": str(uuid.uuid4()),
            "card_id": task.id,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "reduces_control": 1 if new_tier < old_tier else 0,
            "actor": actor,
            "reason": reason,
            "ts": utcnow_iso(),
        }
        # ATOMIC: stage the ledger insert (no commit), then the task put commits BOTH in
        # ONE transaction — the audit row can never diverge from the tier it records.
        self.store.append_tier_audit(audit_row, commit=False)
        task.tier = new_tier
        return self.store.tasks.put(task)

    # ── governed archive / unarchive (audited controls) ────────────────────────
    def archive_task(
        self,
        task_id: str,
        *,
        reason: str,
        expected_version: Optional[str] = None,
        actor: str = ARCHIVE_ACTOR,
    ) -> Task:
        """Governed archive (kanbantt-mcp-spec v0.4.0 §Archive): set ``archived_at``
        on an ACTIVE (non-archived) task, atomically recording one append-only
        ``archive_audit`` row. Mirrors ``retier_task``'s shape verbatim: GOVERNED,
        audited, NO ``force`` — an archive always runs against fresh state; a version
        mismatch raises ``ConflictError`` (re-fetch + re-decide), never clobbers.
        ``archived_at`` is ORTHOGONAL to ``state`` (not a lifecycle move — the card
        keeps its column and merely leaves the default list view).

        Gate THEN invariants:

          1. ``_guard_mutable`` (force always False): unknown ``task_id`` → ``KeyError``
             (→ not_found); a TOMBSTONE → ``ConflictError`` (immutable); a supplied
             ``expected_version`` that does not match → ``ConflictError`` carrying the
             fresh card. Failing here writes NO audit row.
          2. its own invariants, each → ``ValueError`` (→ validation_failed):
             * the task must NOT already be archived — else "already archived". LOUD
               idempotency, deliberately: a healthy archive and a re-archive of an
               already-archived card must not emit the same signal (sweepers filter
               their own targets);
             * the task must have NO OPEN escalation — else "cannot archive a task
               with an unresolved escalation". OPEN means a live (``deleted_at is
               None``), unresolved (``resolved_at is None``) Escalation whose
               ``task_id`` is this task — the same predicate the projection's badge
               uses. Archiving would bury a card awaiting human attention; resolve
               the escalation first. (The gate applies to archive ONLY — unarchive
               has no reason to be blocked.)
          3. the LEDGER invariant: ``append_archive_audit`` REJECTS (``ValueError``)
             an empty/whitespace ``reason`` before staging anything — the row-layer
             NOT NULL. The tool layer defaults an omitted reason, so this fires only
             on explicit garbage.

        On success, in a SINGLE transaction (one commit): stage the ``archive_audit``
        row (``{id, card_id, action: "archive", actor, reason, ts}``) with
        ``commit=False``, stamp ``archived_at``, and ``put`` — the put commits BOTH,
        so the ledger can never diverge from the flag it records. The put mints a
        fresh version token (``archived_at`` rides ``content()``). Returns the
        archived Task."""
        task = self._guard_mutable(task_id, expected_version=expected_version, force=False)
        if task.archived_at is not None:
            raise ValueError(f"task {task_id!r} is already archived")
        if self._has_open_escalation(task_id):
            raise ValueError("cannot archive a task with an unresolved escalation")

        audit_row = {
            "id": str(uuid.uuid4()),
            "card_id": task.id,
            "action": "archive",
            "actor": actor,
            "reason": reason,
            "ts": utcnow_iso(),
        }
        # ATOMIC: stage the ledger insert (no commit), then the task put commits BOTH
        # in ONE transaction — mirroring retier_task's idiom precisely.
        self.store.append_archive_audit(audit_row, commit=False)
        task.archived_at = utcnow_iso()
        return self.store.tasks.put(task)

    def unarchive_task(
        self,
        task_id: str,
        *,
        reason: str,
        expected_version: Optional[str] = None,
        actor: str = ARCHIVE_ACTOR,
    ) -> Task:
        """Governed unarchive: clear ``archived_at`` on an ARCHIVED task, atomically
        recording one append-only ``archive_audit`` row (``action: "unarchive"``).
        Same gate-then-invariants shape as ``archive_task``:

          1. ``_guard_mutable`` (no force): not_found / tombstone / stale version
             exactly as archive — failing here writes NO audit row.
          2. the task MUST currently be archived — else ``ValueError``
             "not archived" (loud idempotency, same rationale as archive).
             There is NO escalation gate on unarchive — restoring a card to view
             never buries anything.
          3. the ledger's non-empty-``reason`` invariant, as archive.

        On success: one staged ledger row + the cleared flag, committed together by
        the put (fresh version token — unarchiving moves it again). Returns the
        unarchived Task."""
        task = self._guard_mutable(task_id, expected_version=expected_version, force=False)
        if task.archived_at is None:
            raise ValueError(f"task {task_id!r} is not archived")

        audit_row = {
            "id": str(uuid.uuid4()),
            "card_id": task.id,
            "action": "unarchive",
            "actor": actor,
            "reason": reason,
            "ts": utcnow_iso(),
        }
        self.store.append_archive_audit(audit_row, commit=False)
        task.archived_at = None
        return self.store.tasks.put(task)

    def _has_open_escalation(self, task_id: str) -> bool:
        """True iff the task has a live, unresolved Escalation — the archive gate's
        predicate, built on the SAME linkage the projection badge uses
        (``Escalation.task_id`` + ``resolved_at is None`` while pending +
        ``deleted_at is None`` for liveness; ``resolve_escalation`` stamping
        ``resolved_at`` is what closes one)."""
        return any(
            e.task_id == task_id and e.deleted_at is None and e.resolved_at is None
            for e in self.store.escalations.list_all()
        )

    # ── artifact writes (MI-1) ─────────────────────────────────────────────────
    def create_artifact(
        self,
        task_id: str,
        kind: str,
        ref: str,
        *,
        artifact_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Artifact:
        """Attach an artifact to a live task. MI-1: rejected if the task is
        tombstoned (or absent). R6: rejected if ``ref`` is a local filesystem path."""
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"unknown artifact kind {kind!r}")
        if not _is_durable_ref(ref):
            raise ValueError(
                f"non_durable_ref: {ref!r} is a local filesystem path and will not "
                f"resolve outside this executor — use a git hash, URL, or content address"
            )
        self._require_live_parent(task_id)  # MI-1
        artifact = Artifact(
            id=artifact_id or str(uuid.uuid4()),
            task_id=task_id,
            kind=kind,
            ref=ref,
            created_at=created_at or utcnow_iso(),
        )
        return self.store.artifacts.put(artifact)

    # ── escalation writes (MI-1 on create, MI-2 on resolve) ────────────────────
    def create_escalation(
        self,
        task_id: str,
        reason: str,
        *,
        control_diff: Optional[Dict[str, Any]] = None,
        escalation_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Escalation:
        """Raise an escalation on a live task. MI-1: rejected if the task is
        tombstoned (or absent)."""
        self._require_live_parent(task_id)  # MI-1
        escalation = Escalation(
            id=escalation_id or str(uuid.uuid4()),
            task_id=task_id,
            reason=reason,
            control_diff=control_diff,
            created_at=created_at or utcnow_iso(),
        )
        return self.store.escalations.put(escalation)

    def resolve_escalation(
        self,
        escalation_id: str,
        *,
        resolution: str,
        resolution_rationale: str,
        actor: str,
        resolved_at: Optional[str] = None,
    ) -> Escalation:
        """MI-2: resolve an escalation in a single put — record the human decision
        (``resolution`` + ``resolution_rationale`` + ``actor``) and stamp
        ``resolved_at``. Still one write with NO paired Task.state change
        ('escalated' is not a state). The persisted escalation IS the override
        receipt, so it records WHO resolved it.

        This is a human-gated control override, so it validates HARD:

          * ``resolution`` ∈ {'approve','deny'}        else ``ValueError``
            (→ ``validation_failed`` at the tool layer).
          * ``resolution_rationale``: a string with >=10 chars after ``.strip()``
            else ``ValueError`` — a SEMANTIC floor (a justification, not merely a
            non-empty string).
          * ``actor`` == 'operator'                     else ``PermissionError`` —
            the ACTOR INVARIANT (→ ``unauthorized`` at the tool layer). ``actor`` is
            NOT a free client parameter: the server derives it from the
            authenticated credential, and the only credential today is the operator
            token. A future agent credential (the v2 write path) must be REFUSED
            here. This is a hard-abort, deliberately distinct from the ordinary
            argument ``ValueError``s, so a stolen/forged actor never reaches the
            store even when the value arguments are well-formed.

        An unknown ``escalation_id`` raises ``KeyError`` (→ ``not_found``)."""
        if resolution not in ("approve", "deny"):
            raise ValueError(f"resolution must be 'approve' or 'deny', got {resolution!r}")
        if not isinstance(resolution_rationale, str) or len(resolution_rationale.strip()) < 10:
            raise ValueError("resolution_rationale must be a string of >=10 non-whitespace characters")
        if actor != "operator":
            raise PermissionError(
                f"actor {actor!r} may not resolve escalations (operator-only invariant)"
            )
        escalation = self.store.escalations.get(escalation_id)
        if escalation is None:
            raise KeyError(f"escalation {escalation_id!r} does not exist")
        escalation.resolved_at = resolved_at or utcnow_iso()
        escalation.resolution = resolution
        escalation.resolution_rationale = resolution_rationale
        escalation.actor = actor
        return self.store.escalations.put(escalation)

    # ── admission helpers ──────────────────────────────────────────────────────
    def _require_task(self, task_id: str) -> Task:
        task = self.store.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} does not exist")
        return task

    def _guard_mutable(self, task_id: str, *, expected_version: Optional[str], force: bool) -> Task:
        """The shared optimistic-concurrency + tombstone-immutability gate for the
        operator card-write paths. Returns the live task to mutate, or raises:

          * ``KeyError``      — unknown ``task_id`` (→ ``not_found`` at the tool layer);
          * ``ConflictError`` — the task is a TOMBSTONE (immutable; NOT even ``force``
            bypasses, per the spec), OR (not ``force`` and an ``expected_version`` was
            supplied that does not equal the current ``version`` token).

        ``expected_version is None`` opts OUT of the optimistic check — the internal,
        non-Kanbantt caller path (test fixtures / direct API use). The MCP tool layer
        ALWAYS supplies one (it is REQUIRED on the wire), so the wire contract has no
        silent last-write-wins. Tombstone immutability is enforced regardless of
        ``expected_version`` or ``force``."""
        task = self._require_task(task_id)
        if task.deleted_at is not None:
            raise ConflictError(task)  # tombstone: immutable — force cannot resurrect
        if not force and expected_version is not None and task.version != expected_version:
            raise ConflictError(task)  # optimistic-concurrency mismatch
        return task

    def _require_live_parent(self, task_id: str) -> Task:
        """MI-1 gate: the parent task must exist and not be tombstoned."""
        task = self.store.tasks.get(task_id)
        if task is None:
            raise ValueError(f"task {task_id!r} does not exist (no orphan children)")
        if task.deleted_at is not None:
            raise ValueError(f"task {task_id!r} is tombstoned (MI-1: no late children)")
        return task
