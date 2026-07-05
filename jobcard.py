#!/usr/bin/env python3
"""``jobcard`` — a tiny CLI to log Claude Code passes as cards on the Kanbantt board.

Each "pass" becomes a Task on the live spine, in a project named ``Dispatch Log``
by default, so the board shows the run as it moves through the pipeline. It is a
thin wrapper over the :class:`spine.Spine` facade against the same SQLite file the
live spine server reads (WAL serializes the single writer), so a write here appears
on the board within a few seconds.

Usage::

    python jobcard.py create "<title>"                  # Dispatch Log, DISPATCHED
    python jobcard.py create --state created "<title>"  # Dispatch Log, CREATED
    python jobcard.py create --project "<name-or-id>" "<title>"  # named project
    python jobcard.py done      <task_id>   # set state to DELIVERED
    python jobcard.py fail      <task_id>   # set state to FAILED
    python jobcard.py set-state <task_id> <state>  # move to any ratified state
    python jobcard.py delete    <task_id>   # hard-remove the row entirely

The db path follows the server's own resolution: ``$CLAUNKER_SPINE_DB`` if set,
else the package default ``spine/spine.db``.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from spine import Spine, State, STATES, Store
from spine.storage import DB_PATH

# The default project all passes are logged under. Looked up by name (idempotent):
# created once if absent, reused otherwise.
DISPATCH_LOG = "Dispatch Log"


def _db_path() -> str:
    """The live spine db, resolved exactly as the server does: env override, else
    the package default (``spine/spine.db``)."""
    return os.environ.get("CLAUNKER_SPINE_DB", DB_PATH)


def _ensure_dispatch_log(spine: Spine):
    """Return the live ``Dispatch Log`` project, creating it once if absent. Only
    live (non-tombstoned) projects count, so a soft-deleted one is not reused."""
    for project in spine.store.projects.list_live():
        if project.name == DISPATCH_LOG:
            return project
    return spine.create_project(DISPATCH_LOG)


def _resolve_project(spine: Spine, name_or_id: str):
    """Return the live project matching ``name_or_id`` — exact id first, then exact
    name against live projects. Unknown → loud SystemExit; never create-if-missing
    (a typo must not mint a phantom project)."""
    p = spine.get_project(name_or_id)
    if p is not None and p.deleted_at is None:
        return p
    for project in spine.store.projects.list_live():
        if project.name == name_or_id:
            return project
    raise SystemExit(f"jobcard: unknown project {name_or_id!r} (no id or name match)")


def _set_state(spine: Spine, task_id: str, state: str) -> None:
    """Move an existing task to ``state`` via get → set → put (no transition
    validation needed here). Errors clearly if the id is unknown."""
    task = spine.get_task(task_id)
    if task is None:
        raise SystemExit(f"jobcard: no task with id {task_id!r} (nothing to update)")
    task.state = state
    spine.store.tasks.put(task)


def cmd_create(
    spine: Spine,
    title: str,
    *,
    state: str = State.DISPATCHED,
    project_arg: Optional[str] = None,
) -> None:
    if project_arg is None:
        project = _ensure_dispatch_log(spine)
    else:
        project = _resolve_project(spine, project_arg)
    task = spine.create_task(project.id, title, state=state)
    # ONLY the id on stdout — callers capture it (e.g. `jobcard done $(jobcard create ...)`).
    print(task.id)


def cmd_done(spine: Spine, task_id: str) -> None:
    _set_state(spine, task_id, State.DELIVERED)


def cmd_fail(spine: Spine, task_id: str) -> None:
    _set_state(spine, task_id, State.FAILED)


def cmd_delete(spine: Spine, task_id: str) -> None:
    """Hard-remove a task's row entirely — distinct from ``done``/``fail``, which
    only change state. Errors clearly if the id is unknown rather than no-op-ing."""
    try:
        spine.store.tasks.hard_delete(task_id)
    except KeyError:
        raise SystemExit(f"jobcard: no task with id {task_id!r} (nothing to delete)")


def cmd_set_state(spine: Spine, task_id: str, state: str) -> None:
    """Move a card to any ratified spine state. Deliberately permissive — no
    transition state-machine in the CLI (the spine is a ledger of already-governed
    work). Invalid state caught by argparse choices; unknown card fails with the
    spine's own KeyError, surfaced as a loud SystemExit."""
    try:
        spine.set_state(task_id, state)
    except KeyError as e:
        raise SystemExit(f"jobcard: {e}") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobcard", description="Log Claude Code passes as cards on the spine board."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="add a pass card; prints its id")
    p_create.add_argument("title", help="the card title")
    p_create.add_argument(
        "--state",
        choices=list(STATES),
        default=State.DISPATCHED,
        help="initial state (default: dispatched)",
    )
    p_create.add_argument(
        "--project",
        metavar="NAME_OR_ID",
        default=None,
        help="project name or id (default: Dispatch Log)",
    )

    p_done = sub.add_parser("done", help="mark a pass card DELIVERED")
    p_done.add_argument("task_id", help="the task id printed by create")

    p_fail = sub.add_parser("fail", help="mark a pass card FAILED")
    p_fail.add_argument("task_id", help="the task id printed by create")

    p_delete = sub.add_parser("delete", help="hard-remove a pass card's row entirely")
    p_delete.add_argument("task_id", help="the task id printed by create")

    p_set_state = sub.add_parser("set-state", help="move a card to any ratified state")
    p_set_state.add_argument("task_id", help="the task id")
    p_set_state.add_argument("state", choices=list(STATES), help="the new state")

    args = parser.parse_args(argv)

    # One writable Store for the whole command; WAL serializes us against the live
    # server's reads. ``put`` commits, so nothing is left uncommitted on close.
    with Store(_db_path()) as store:
        spine = Spine(store)
        if args.command == "create":
            cmd_create(spine, args.title, state=args.state, project_arg=args.project)
        elif args.command == "done":
            cmd_done(spine, args.task_id)
        elif args.command == "fail":
            cmd_fail(spine, args.task_id)
        elif args.command == "delete":
            cmd_delete(spine, args.task_id)
        elif args.command == "set-state":
            cmd_set_state(spine, args.task_id, args.state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
