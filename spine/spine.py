"""``Spine`` — the facade tying the four-table store, ordering, and projection
together behind semantic write paths, and the home of the server write-admission
checks (the Mutation Invariants).

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

Reads return entity objects; ``cards()`` returns the projected, soft-delete-
omitting, escalation-badged Card view.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .entity import (
    ARTIFACT_KINDS,
    STATES,
    Artifact,
    Escalation,
    Project,
    Task,
    State,
)
from .ordering import append_rank
from .projection import project
from .storage import Store, utcnow_iso


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
        task_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Task:
        """Create a task, append-at-end in board order. ``order`` is seeded after
        the current max live rank; ``rebalance`` is NEVER invoked here. Note: this
        does not validate ``project_id`` exists (no such MI is specified)."""
        if state not in STATES:
            raise ValueError(f"unknown task state {state!r}")
        last_order = max((t.order for t in self.store.tasks.list_live()), default="")
        task = Task(
            id=task_id or str(uuid.uuid4()),
            project_id=project_id,
            title=title,
            state=state,
            tier=tier,
            acceptance_criteria=acceptance_criteria,
            order=append_rank(last_order),
            created_at=created_at or utcnow_iso(),
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
        tier: Optional[int] = None,
        expected_version: Optional[str] = None,
        force: bool = False,
    ) -> Task:
        """Operator edit of a task's MUTABLE fields — ``title`` / ``acceptance_criteria``
        / ``tier`` — in a single get→set→put. NOT ``state`` and NOT ``order`` (those
        move via ``move_task``). A FREE, ungoverned operator edit (the operator is the
        tier-4 human): only input hygiene is checked, never a transition policy.

          * at least one field must be provided (all-``None`` → ``ValueError``);
          * ``tier``, when provided, must be in 1..4 (else ``ValueError``);
          * ``title``, when provided, must be non-empty (else ``ValueError``).

        Only the provided (non-``None``) fields change; ``None`` means "leave as-is".
        Unknown ``task_id`` → ``KeyError`` (→ not_found).

        OPTIMISTIC CONCURRENCY (spec §Concurrency): ``expected_version`` is checked
        against the task's current ``version`` token; on mismatch (and not ``force``)
        a ``ConflictError`` carrying the current task is raised. A tombstoned task is
        immutable — any edit raises ``ConflictError`` (the tombstone), even under
        ``force``. See ``_guard_mutable``. Input hygiene is validated BEFORE the
        concurrency gate, so a malformed edit is a ``ValueError`` (→ validation_failed)
        regardless of version/tombstone."""
        if title is None and acceptance_criteria is None and tier is None:
            raise ValueError("update_task requires at least one field to change")
        if tier is not None and not (1 <= tier <= 4):
            raise ValueError(f"tier must be an int in 1..4, got {tier!r}")
        if title is not None and not title.strip():
            raise ValueError("title cannot be updated to an empty string")
        task = self._guard_mutable(task_id, expected_version=expected_version, force=force)
        if title is not None:
            task.title = title
        if acceptance_criteria is not None:
            task.acceptance_criteria = acceptance_criteria
        if tier is not None:
            task.tier = tier
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
        tombstoned (or absent)."""
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"unknown artifact kind {kind!r}")
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
