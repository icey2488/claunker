"""``Spine`` — the facade tying the four-table store, ordering, and projection
together behind semantic write paths, and the home of the server write-admission
checks (the Mutation Invariants).

The ``Store`` / ``EntityStore`` layer is dumb persistence: it stamps versions and
writes blobs, no cross-entity policy. The Spine is the "server" boundary that
admits or rejects writes:

    MI-1  No late children on a tombstoned parent. ``create_artifact`` /
          ``create_escalation`` reject a ``task_id`` that resolves to a
          soft-deleted Task (and, as referential hygiene, an absent Task).
    MI-2  Resolving an escalation sets ``resolved_at`` in a single put — one
          write, no paired Task.state transition (escalation is not a state).

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

    def soft_delete_task(self, task_id: str) -> Task:
        return self.store.tasks.soft_delete(task_id)

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

    def resolve_escalation(self, escalation_id: str, *, resolved_at: Optional[str] = None) -> Escalation:
        """MI-2: set ``resolved_at`` in a single put. No paired Task.state change —
        'escalated' is not a state, so this is a trivial single-field write."""
        escalation = self.store.escalations.get(escalation_id)
        if escalation is None:
            raise KeyError(f"escalation {escalation_id!r} does not exist")
        escalation.resolved_at = resolved_at or utcnow_iso()
        return self.store.escalations.put(escalation)

    # ── admission helpers ──────────────────────────────────────────────────────
    def _require_task(self, task_id: str) -> Task:
        task = self.store.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} does not exist")
        return task

    def _require_live_parent(self, task_id: str) -> Task:
        """MI-1 gate: the parent task must exist and not be tombstoned."""
        task = self.store.tasks.get(task_id)
        if task is None:
            raise ValueError(f"task {task_id!r} does not exist (no orphan children)")
        if task.deleted_at is not None:
            raise ValueError(f"task {task_id!r} is tombstoned (MI-1: no late children)")
        return task
