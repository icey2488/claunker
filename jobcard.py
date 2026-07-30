#!/usr/bin/env python3
"""``jobcard`` — a tiny CLI to log Claude Code passes as cards on the Kanbantt board.

Each "pass" becomes a Task on the live spine, in a project named ``Dispatch Log``
by default, so the board shows the run as it moves through the pipeline. It is a
thin wrapper over the :class:`spine.Spine` facade against the same SQLite file the
live spine server reads (WAL serializes the single writer), so a write here appears
on the board within a few seconds.

Usage::

    python jobcard.py create "<title>"                  # Dispatch Log, CREATED
    python jobcard.py create --state dispatched "<title>"  # explicit non-default state
    python jobcard.py create --project "<name-or-id>" "<title>"  # named project
    python jobcard.py create --actor claude-code --model claude-sonnet-5 \
        --effort medium --job-id 1234-abcd "<title>"    # dispatch provenance
    python jobcard.py create --description-file <path> "<title>"  # binary-safe long body
    python jobcard.py list                              # every live card: id/state/title
    python jobcard.py list --state created               # filtered to one state
    python jobcard.py done      <task_id>   # set state to DELIVERED
    python jobcard.py fail      <task_id>   # set state to FAILED
    python jobcard.py set-state <task_id> <state> --expected-version <token>  # move to any ratified state
    python jobcard.py delete    <task_id> --expected-version <token>  # tombstone (soft delete)
    python jobcard.py show      <task_id>   # id/title/state/version/description/artifacts (full id or unambiguous prefix)
    python jobcard.py update    <task_id> --title "<text>" --expected-version <token>
    python jobcard.py update    <task_id> --description "<text>" --expected-version <token>
    python jobcard.py update    <task_id> --description-file <path> --expected-version <token>

CARD-STATE CONVENTION: ``create`` defaults to ``created`` (any other initial state
needs an explicit ``--state``). A finding/radar card (a defect discovered rather
than dispatched as work) is not job-lifecycle-shaped — it stays in ``created``
until someone actively works it, then moves straight to ``delivered`` when
resolved; ``{tiered, dispatched, judged}`` simply don't apply to it. This is a
CONVENTION over the existing six-state enum, not a new state (see README.md and
card 269144d9).

The db path follows the server's own resolution: ``$CLAUNKER_SPINE_DB`` if set,
else the package default ``spine/spine.db``.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from spine import ConflictError, Spine, State, STATES, Store
from spine.entity import ARTIFACT_KINDS
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
    actor: Optional[str] = None,
    actor_type: str = "agent",
    model: Optional[str] = None,
    effort: Optional[str] = None,
    job_id: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    if project_arg is None:
        project = _ensure_dispatch_log(spine)
    else:
        project = _resolve_project(spine, project_arg)
    created_by = {"type": actor_type, "id": actor} if actor is not None else None
    if created_by is not None:
        # Optional dispatch-provenance sub-keys (spec v0.7.0, entity._PROVENANCE_STR_KEYS).
        # Absent means absent — no empty-string/null placeholders in created_by.
        if model is not None:
            created_by["model"] = model
        if effort is not None:
            created_by["effort"] = effort
        if job_id is not None:
            created_by["job_id"] = job_id
    # description: the narrative body (spec v0.8.0). Omitted = absent (None), never a
    # coerced empty string — matches the wire card_create's absent-means-null handling.
    task = spine.create_task(project.id, title, state=state, created_by=created_by,
                             description=description)
    # ONLY the id on stdout — callers capture it (e.g. `jobcard done $(jobcard create ...)`).
    print(task.id)


def cmd_done(spine: Spine, task_id: str) -> None:
    _set_state(spine, task_id, State.DELIVERED)


def cmd_fail(spine: Spine, task_id: str) -> None:
    _set_state(spine, task_id, State.FAILED)


def cmd_delete(spine: Spine, task_id: str, *, expected_version: str) -> None:
    """Tombstone a task via the governed ``Spine.soft_delete_task`` path — no more
    raw ``hard_delete`` (card b467851e: the old path bypassed the ledger and any
    concurrency check). Recoverable (row retained, ``deleted_at`` stamped), and
    requires the caller's ``expected_version`` to match or the delete is rejected."""
    try:
        spine.soft_delete_task(task_id, expected_version=expected_version)
    except KeyError:
        raise SystemExit(f"jobcard: no task with id {task_id!r} (nothing to delete)")
    except ConflictError as e:
        raise SystemExit(f"jobcard delete: {e}") from None


def cmd_artifact(spine: Spine, task_id: str, kind: str, ref: str) -> None:
    """Attach an artifact to a task. R6 durable-ref validation and MI-1 zombie-append
    are enforced by the spine — the CLI surfaces those errors loudly and exits non-zero."""
    try:
        artifact = spine.create_artifact(task_id, kind, ref)
    except KeyError as e:
        raise SystemExit(f"jobcard artifact: not_found: {e}") from None
    except ValueError as e:
        raise SystemExit(f"jobcard artifact: {e}") from None
    print(artifact.id)


def cmd_set_state(spine: Spine, task_id: str, state: str, *, expected_version: str) -> None:
    """Move a card to any ratified spine state. Deliberately permissive — no
    transition state-machine in the CLI (the spine is a ledger of already-governed
    work). Invalid state caught by argparse choices; unknown card fails with the
    spine's own KeyError, surfaced as a loud SystemExit. ``expected_version`` is
    required (card f2b52250, mirroring ``update``'s mandatory argparse pattern) —
    a stale token is rejected with no write."""
    try:
        spine.set_state(task_id, state, expected_version=expected_version)
    except KeyError as e:
        raise SystemExit(f"jobcard: {e}") from None
    except ConflictError as e:
        raise SystemExit(f"jobcard set-state: {e}") from None


def _resolve_show_id(spine: Spine, task_id: str):
    """Resolve ``task_id`` for ``show``: an exact id match takes precedence (the
    hot path stays a single indexed ``get``); otherwise ``task_id`` is treated as an
    unambiguous prefix scanned across every task, tombstones included, since ``show``
    itself already prints tombstoned cards by full id.

    Zero matches and 2+ matches are both loud, non-zero, and DISTINCT from each
    other, and an ambiguous prefix NEVER falls back to a first/newest/any-other
    tiebreak — silently resolving to the wrong card costs far more than the
    ergonomics gained. No minimum prefix length is imposed; ambiguity handling alone
    covers that risk."""
    task = spine.get_task(task_id)
    if task is not None:
        return task
    matches = [t for t in spine.store.tasks.list_all() if t.id.startswith(task_id)]
    if not matches:
        raise SystemExit(f"jobcard: no card matches id/prefix {task_id!r}")
    if len(matches) > 1:
        ids = ", ".join(sorted(m.id for m in matches))
        raise SystemExit(
            f"jobcard: ambiguous prefix {task_id!r} matches {len(matches)} cards: {ids}"
        )
    return matches[0]


def cmd_list(spine: Spine, state: Optional[str] = None) -> None:
    """Enumerate live cards, one per line: ``<8-char id prefix> <state> <title>``.
    Tombstones are excluded — this is a discovery surface over the working board,
    the same live set ``board_get`` renders, not a full-history read. ``state``
    narrows to that column when given; an off-enum value is rejected by argparse
    (``choices``) before this ever runs, so no validation is needed here.

    ORDERING (deterministic, this CLI's choice — see --help): board position
    (``Task.order``, the LexoRank string the board itself sorts columns by), then
    ``id`` as a total-order tiebreak for two cards sharing an ``order``."""
    tasks = spine.store.tasks.list_live()
    if state is not None:
        tasks = [t for t in tasks if t.state == state]
    for t in sorted(tasks, key=lambda t: (t.order, t.id)):
        print(f"{t.id[:8]} {t.state} {t.title}")


def cmd_show(spine: Spine, task_id: str) -> None:
    """Print id/title/state/version, then the FULL description body and every
    attached artifact (card 269144d9 remedies 2 and 5) — the CLI's only read
    surface for a single card, so nothing governed content lives in without being
    visible here. ``task_id`` may be a full uuid or any unambiguous prefix (see
    ``_resolve_show_id``). The description is printed verbatim between clear
    delimiters (never truncated or reformatted) since it may itself contain
    Markdown that would otherwise blur into this output."""
    task = _resolve_show_id(spine, task_id)
    print(f"id: {task.id}")
    print(f"title: {task.title}")
    print(f"state: {task.state}")
    print(f"version: {task.version}")
    print("description:")
    print("--- description start ---")
    if task.description is not None:
        print(task.description)
    print("--- description end ---")
    artifacts = [a for a in spine.store.artifacts.list_all() if a.task_id == task.id]
    print(f"artifacts ({len(artifacts)}):")
    for a in artifacts:
        print(f"  {a.id} {a.kind} {a.ref}")


def cmd_update(spine: Spine, task_id: str, patch: dict, *, expected_version: str) -> None:
    """Correct a card's title/description via ``spine.update_task``'s optimistic-
    concurrency get→set→put. ``patch`` carries ONLY the keys the operator actually
    supplied on the command line (argparse.SUPPRESS keeps an omitted flag out of
    the namespace) — update_task's key-presence contract treats an absent key as
    "leave unchanged", so a naive always-present patch would forward None and clear
    the other field. No retry on conflict: the caller re-reads via ``show`` and
    resubmits deliberately."""
    if not patch:
        raise SystemExit("jobcard update: at least one of --title/--description is required")
    try:
        task = spine.update_task(task_id, expected_version=expected_version, **patch)
    except KeyError as e:
        raise SystemExit(f"jobcard update: {e}") from None
    except ConflictError as e:
        raise SystemExit(f"jobcard update: {e}") from None
    print(task.id)


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
    p_create.add_argument(
        "--actor",
        metavar="ID",
        default=None,
        help="actor id to record as created_by (omit to leave null)",
    )
    p_create.add_argument(
        "--actor-type",
        choices=["human", "agent"],
        default="agent",
        dest="actor_type",
        help="actor type (default: agent); ignored when --actor is omitted",
    )
    p_create.add_argument(
        "--model",
        default=None,
        help="dispatch provenance: reasoning model to record in created_by (optional; "
             "ignored when --actor is omitted)",
    )
    p_create.add_argument(
        "--effort",
        default=None,
        help="dispatch provenance: reasoning effort to record in created_by (optional; "
             "ignored when --actor is omitted)",
    )
    p_create.add_argument(
        "--job-id",
        dest="job_id",
        default=None,
        help="dispatch provenance: originating claude-async job id to record in created_by "
             "(optional; ignored when --actor is omitted)",
    )
    p_create.add_argument(
        "--description",
        default=None,
        help="the card's narrative body (spec v0.8.0); a short Markdown summary of what "
             "the card is about. Omitted = no body (never an empty string).",
    )

    p_done = sub.add_parser("done", help="mark a pass card DELIVERED")
    p_done.add_argument("task_id", help="the task id printed by create")

    p_fail = sub.add_parser("fail", help="mark a pass card FAILED")
    p_fail.add_argument("task_id", help="the task id printed by create")

    p_delete = sub.add_parser("delete", help="tombstone a pass card via the governed soft-delete path")
    # Full uuid only — deliberately NO prefix resolution here (unlike `show`). Card
    # b467851e recorded prefix-resolved deletion as a standing extension hazard: a
    # destructive op must never be one fat-fingered/ambiguous prefix from hitting the
    # wrong card.
    p_delete.add_argument("task_id", help="the task id printed by create (full id only)")
    p_delete.add_argument(
        "--expected-version", dest="expected_version", required=True,
        help="version token from `jobcard show` (required — no silent last-write-wins)",
    )

    p_artifact = sub.add_parser("artifact", help="attach a durable artifact receipt to a card")
    p_artifact.add_argument("task_id", help="the task id")
    p_artifact.add_argument("--kind", choices=list(ARTIFACT_KINDS), required=True,
                            help="artifact kind: diff, file, verdict, delivery")
    p_artifact.add_argument("--ref", required=True,
                            help="durable ref (git hash, URL, content address — local paths rejected)")

    p_set_state = sub.add_parser("set-state", help="move a card to any ratified state")
    p_set_state.add_argument("task_id", help="the task id")
    p_set_state.add_argument("state", choices=list(STATES), help="the new state")
    p_set_state.add_argument(
        "--expected-version", dest="expected_version", required=True,
        help="version token from `jobcard show` (required — no silent last-write-wins)",
    )

    p_list = sub.add_parser(
        "list", help="enumerate live cards: id/state/title",
        description="Enumerate live (non-tombstoned) cards, one per line: "
                     "'<8-char id prefix> <state> <title>'. Ordered by board position "
                     "(Task.order), then id as a tiebreak.",
    )
    p_list.add_argument(
        "--state", choices=list(STATES), default=None,
        help="only list cards in this state (default: every live state)",
    )

    p_show = sub.add_parser("show", help="print a card's id/title/state/version/description/artifacts")
    p_show.add_argument("task_id", help="the task id, or any unambiguous prefix of it")

    p_update = sub.add_parser("update", help="correct a card's title/description")
    p_update.add_argument("task_id", help="the task id")
    p_update.add_argument(
        "--title", default=argparse.SUPPRESS, help="new title (omit to leave unchanged)",
    )
    p_update.add_argument(
        "--description", default=argparse.SUPPRESS,
        help="new narrative body (omit to leave unchanged)",
    )
    p_update.add_argument(
        "--expected-version", dest="expected_version", required=True,
        help="version token from `jobcard show` (required — no silent last-write-wins)",
    )

    args = parser.parse_args(argv)

    # One writable Store for the whole command; WAL serializes us against the live
    # server's reads. ``put`` commits, so nothing is left uncommitted on close.
    with Store(_db_path()) as store:
        spine = Spine(store)
        if args.command == "create":
            cmd_create(spine, args.title, state=args.state, project_arg=args.project,
                       actor=args.actor, actor_type=args.actor_type,
                       model=args.model, effort=args.effort, job_id=args.job_id,
                       description=args.description)
        elif args.command == "done":
            cmd_done(spine, args.task_id)
        elif args.command == "fail":
            cmd_fail(spine, args.task_id)
        elif args.command == "delete":
            cmd_delete(spine, args.task_id, expected_version=args.expected_version)
        elif args.command == "artifact":
            cmd_artifact(spine, args.task_id, args.kind, args.ref)
        elif args.command == "set-state":
            cmd_set_state(spine, args.task_id, args.state, expected_version=args.expected_version)
        elif args.command == "list":
            cmd_list(spine, args.state)
        elif args.command == "show":
            cmd_show(spine, args.task_id)
        elif args.command == "update":
            patch = {}
            if hasattr(args, "title"):
                patch["title"] = args.title
            if hasattr(args, "description"):
                patch["description"] = args.description
            cmd_update(spine, args.task_id, patch, expected_version=args.expected_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
