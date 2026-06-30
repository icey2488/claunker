#!/usr/bin/env python3
"""``jobcard`` — a tiny CLI to log Claude Code passes as cards on the Kanbantt board.

Each "pass" becomes a Task on the live spine, in a project named ``Dispatch Log``,
so the board shows the run as it moves: dispatched → delivered (or failed). It is a
thin wrapper over the :class:`spine.Spine` facade against the same SQLite file the
live spine server reads (WAL serializes the single writer), so a write here appears
on the board within a few seconds.

Usage::

    python jobcard.py create "<title>"   # ensure "Dispatch Log", add a DISPATCHED
                                          # task, print ONLY the new task id
    python jobcard.py done   <task_id>    # set that task's state to DELIVERED
    python jobcard.py fail   <task_id>    # set that task's state to FAILED

The db path follows the server's own resolution: ``$CLAUNKER_SPINE_DB`` if set,
else the package default ``spine/spine.db``.
"""

from __future__ import annotations

import argparse
import os
import sys

from spine import Spine, State, Store
from spine.storage import DB_PATH

# The single project all passes are logged under. Looked up by name (idempotent):
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


def _set_state(spine: Spine, task_id: str, state: str) -> None:
    """Move an existing task to ``state`` via get → set → put (no transition
    validation needed here). Errors clearly if the id is unknown."""
    task = spine.get_task(task_id)
    if task is None:
        raise SystemExit(f"jobcard: no task with id {task_id!r} (nothing to update)")
    task.state = state
    spine.store.tasks.put(task)


def cmd_create(spine: Spine, title: str) -> None:
    project = _ensure_dispatch_log(spine)
    task = spine.create_task(project.id, title, state=State.DISPATCHED)
    # ONLY the id on stdout — callers capture it (e.g. `jobcard done $(jobcard create ...)`).
    print(task.id)


def cmd_done(spine: Spine, task_id: str) -> None:
    _set_state(spine, task_id, State.DELIVERED)


def cmd_fail(spine: Spine, task_id: str) -> None:
    _set_state(spine, task_id, State.FAILED)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobcard", description="Log Claude Code passes as cards on the spine board."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="add a DISPATCHED pass card; prints its id")
    p_create.add_argument("title", help="the card title")

    p_done = sub.add_parser("done", help="mark a pass card DELIVERED")
    p_done.add_argument("task_id", help="the task id printed by create")

    p_fail = sub.add_parser("fail", help="mark a pass card FAILED")
    p_fail.add_argument("task_id", help="the task id printed by create")

    args = parser.parse_args(argv)

    # One writable Store for the whole command; WAL serializes us against the live
    # server's reads. ``put`` commits, so nothing is left uncommitted on close.
    with Store(_db_path()) as store:
        spine = Spine(store)
        if args.command == "create":
            cmd_create(spine, args.title)
        elif args.command == "done":
            cmd_done(spine, args.task_id)
        elif args.command == "fail":
            cmd_fail(spine, args.task_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
