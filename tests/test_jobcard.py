"""Tests for jobcard.py CLI — board-hygiene arc (2026-07-04).

Run:
    uv run --with pytest --python 3.11 python -m pytest tests/test_jobcard.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jobcard import main
from spine import Spine, State, Store


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point CLAUNKER_SPINE_DB at a fresh temp file for each test."""
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("CLAUNKER_SPINE_DB", db)
    return db


# ── create --state ─────────────────────────────────────────────────────────────

def test_create_default_state_is_dispatched(tmp_db, capsys):
    main(["create", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.state == State.DISPATCHED


def test_create_state_created(tmp_db, capsys):
    main(["create", "--state", "created", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.state == State.CREATED


# ── set-state ──────────────────────────────────────────────────────────────────

def test_set_state_moves_card(tmp_db, capsys):
    main(["create", "x"])
    task_id = capsys.readouterr().out.strip()

    main(["set-state", task_id, "tiered"])

    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.state == State.TIERED


def test_set_state_rejects_off_enum_state(tmp_db):
    with pytest.raises(SystemExit) as exc_info:
        main(["set-state", "fake-id", "nonexistent"])
    assert exc_info.value.code == 2  # argparse rejects invalid choice


def test_set_state_unknown_card_fails_loudly(tmp_db):
    with pytest.raises(SystemExit, match="jobcard:"):
        main(["set-state", "00000000-0000-0000-0000-000000000000", "created"])


# ── create --project ───────────────────────────────────────────────────────────

def test_project_resolves_by_id(tmp_db, capsys):
    with Store(tmp_db) as store:
        proj = Spine(store).create_project("My Project")

    main(["create", "--project", proj.id, "task"])
    task_id = capsys.readouterr().out.strip()

    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.project_id == proj.id


def test_project_resolves_by_name(tmp_db, capsys):
    with Store(tmp_db) as store:
        proj = Spine(store).create_project("Named Project")

    main(["create", "--project", "Named Project", "task"])
    task_id = capsys.readouterr().out.strip()

    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.project_id == proj.id


def test_project_unknown_errors(tmp_db):
    with pytest.raises(SystemExit, match="unknown project"):
        main(["create", "--project", "GhostProject", "task"])


# ── create --actor round-trip ──────────────────────────────────────────────────

def test_actor_agent_round_trip(tmp_db, capsys):
    main(["create", "--actor", "claude-code", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.created_by == {"type": "agent", "id": "claude-code"}


def test_actor_human_round_trip(tmp_db, capsys):
    main(["create", "--actor", "icey2488", "--actor-type", "human", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.created_by == {"type": "human", "id": "icey2488"}


def test_no_actor_gives_null_created_by(tmp_db, capsys):
    main(["create", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.created_by is None


# ── create --model/--effort/--job-id (dispatch provenance) ────────────────────

def test_provenance_flags_round_trip(tmp_db, capsys):
    main(["create", "--actor", "claude-code", "--model", "claude-sonnet-5",
          "--effort", "medium", "--job-id", "job-abc-123", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.created_by == {
        "type": "agent", "id": "claude-code",
        "model": "claude-sonnet-5", "effort": "medium", "job_id": "job-abc-123",
    }


def test_provenance_flags_omitted_produce_todays_behavior(tmp_db, capsys):
    """Omitted --model/--effort/--job-id → absent means absent, no empty/null keys."""
    main(["create", "--actor", "claude-code", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.created_by == {"type": "agent", "id": "claude-code"}
    assert "model" not in task.created_by
    assert "effort" not in task.created_by
    assert "job_id" not in task.created_by


def test_provenance_flags_partial_only_sets_given_keys(tmp_db, capsys):
    main(["create", "--actor", "claude-code", "--model", "claude-opus-4-8", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.created_by == {"type": "agent", "id": "claude-code", "model": "claude-opus-4-8"}


def test_provenance_flags_ignored_without_actor(tmp_db, capsys):
    """No --actor → created_by stays null, so provenance flags have nothing to ride on."""
    main(["create", "--model", "claude-sonnet-5", "--effort", "high", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.created_by is None


# ── create --description (narrative body, spec v0.8.0) ─────────────────────────

def test_description_flag_round_trips(tmp_db, capsys):
    main(["create", "--description", "Fix the widget latency regression.", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.description == "Fix the widget latency regression."


def test_description_omitted_is_null_not_empty(tmp_db, capsys):
    main(["create", "my task"])
    task_id = capsys.readouterr().out.strip()
    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.description is None


# ── artifact subcommand ────────────────────────────────────────────────────────

def test_artifact_git_hash_ref_accepted(tmp_db, capsys):
    main(["create", "task"])
    task_id = capsys.readouterr().out.strip()

    main(["artifact", task_id, "--kind", "delivery", "--ref", "81d33c2a4b5e6f7890abcdef1234567890abcdef"])
    artifact_id = capsys.readouterr().out.strip()
    assert artifact_id  # got an id back

    with Store(tmp_db) as store:
        a = store.artifacts.get(artifact_id)
    assert a.task_id == task_id
    assert a.kind == "delivery"
    assert a.ref == "81d33c2a4b5e6f7890abcdef1234567890abcdef"


def test_artifact_unix_local_path_rejected_non_durable_ref(tmp_db, capsys):
    main(["create", "task"])
    task_id = capsys.readouterr().out.strip()
    capsys.readouterr()

    with pytest.raises(SystemExit, match="non_durable_ref"):
        main(["artifact", task_id, "--kind", "file", "--ref", "/workspace/output.py"])


def test_artifact_windows_local_path_rejected_non_durable_ref(tmp_db, capsys):
    main(["create", "task"])
    task_id = capsys.readouterr().out.strip()
    capsys.readouterr()

    with pytest.raises(SystemExit, match="non_durable_ref"):
        main(["artifact", task_id, "--kind", "file", "--ref", "C:\\output\\result.txt"])


def test_artifact_on_tombstoned_card_rejected_zombie_append(tmp_db, capsys):
    main(["create", "task"])
    task_id = capsys.readouterr().out.strip()
    # Soft-delete (tombstone) via Spine so the row is retained but dead — MI-1 zombie case.
    with Store(tmp_db) as store:
        Spine(store).soft_delete_task(task_id)

    with pytest.raises(SystemExit, match="tombstoned"):
        main(["artifact", task_id, "--kind", "delivery", "--ref", "abc123def456abc123def456abc123def456abc1"])


def test_artifact_unknown_card_rejected_not_found(tmp_db):
    with pytest.raises(SystemExit, match="does not exist"):
        main(["artifact", "00000000-0000-0000-0000-000000000000", "--kind", "diff",
              "--ref", "abc123def456abc123def456abc123def456abc1"])


# ── delete (governed soft-delete, card b467851e) ───────────────────────────────

def test_delete_without_expected_version_fails_at_parse(tmp_db, capsys):
    """No --expected-version must fail at argparse (exit 2), before cmd_delete
    ever runs — the CLI must never let a bare task_id slide through as an
    implicit None (spine.py's fail-open opt-out is for internal/test callers
    only, never the wire-facing CLI)."""
    main(["create", "x"])
    task_id = capsys.readouterr().out.strip()

    with pytest.raises(SystemExit) as exc_info:
        main(["delete", task_id])
    assert exc_info.value.code == 2


def test_delete_wrong_version_rejected_row_untouched(tmp_db, capsys):
    main(["create", "x"])
    task_id = capsys.readouterr().out.strip()

    with pytest.raises(SystemExit, match="jobcard delete:"):
        main(["delete", task_id, "--expected-version", "0:stale"])

    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task is not None
    assert task.deleted_at is None


def test_delete_correct_version_tombstones_recoverably(tmp_db, capsys):
    main(["create", "x"])
    task_id = capsys.readouterr().out.strip()

    with Store(tmp_db) as store:
        version = store.tasks.get(task_id).version

    main(["delete", task_id, "--expected-version", version])
    capsys.readouterr()

    with Store(tmp_db) as store:
        # Excluded from live views...
        assert task_id not in {t.id for t in store.tasks.list_live()}
        # ...but the row itself is retained (recoverable), tombstoned not gone —
        # the raw hard_delete behavior (row vanishes entirely) is gone.
        task = store.tasks.get(task_id)
        assert task is not None
        assert task.deleted_at is not None
        assert task_id in {t.id for t in store.tasks.list_all()}


def test_delete_unknown_id_fails_loudly(tmp_db):
    with pytest.raises(SystemExit, match="jobcard:"):
        main(["delete", "00000000-0000-0000-0000-000000000000",
              "--expected-version", "0:whatever"])


# ── show (id resolution: exact + unambiguous prefix) ───────────────────────────

def test_show_exact_uuid_still_works(tmp_db, capsys):
    main(["create", "my task"])
    task_id = capsys.readouterr().out.strip()

    main(["show", task_id])
    out = capsys.readouterr().out
    assert f"id: {task_id}" in out


def test_show_unique_prefix_resolves(tmp_db, capsys):
    main(["create", "my task"])
    task_id = capsys.readouterr().out.strip()

    main(["show", task_id[:8]])
    out = capsys.readouterr().out
    assert f"id: {task_id}" in out


def test_show_ambiguous_prefix_rejected_and_names_candidates(tmp_db, capsys):
    # Mint two tasks whose ids share a common prefix, via task_id= on create_task.
    with Store(tmp_db) as store:
        spine = Spine(store)
        project = spine.create_project("Dispatch Log")
        a = spine.create_task(project.id, "task a",
                               task_id="abcd1111-0000-0000-0000-000000000001")
        b = spine.create_task(project.id, "task b",
                               task_id="abcd2222-0000-0000-0000-000000000002")

    with pytest.raises(SystemExit) as exc_info:
        main(["show", "abcd"])
    msg = str(exc_info.value)
    assert "ambiguous" in msg
    assert a.id in msg
    assert b.id in msg


def test_show_unknown_prefix_rejected_distinct_from_ambiguous(tmp_db):
    with pytest.raises(SystemExit) as exc_info:
        main(["show", "deadbeef"])
    msg = str(exc_info.value)
    assert "no card matches" in msg
    assert "ambiguous" not in msg


# ── update (concurrency + null-forwarding) ─────────────────────────────────────

def test_update_stale_version_rejected_and_state_unchanged(tmp_db, capsys):
    """A stale --expected-version is rejected, and BOTH title and description are
    left exactly as they were — asserted against the PERSISTED row, not the CLI's
    exit code. The same edit with the CORRECT version then succeeds, proving the
    rejection above was the concurrency guard and not some other failure."""
    main(["create", "--description", "orig desc", "orig title"])
    task_id = capsys.readouterr().out.strip()

    with Store(tmp_db) as store:
        good_version = store.tasks.get(task_id).version
    stale_version = "0:stale"

    with pytest.raises(SystemExit):
        main(["update", task_id, "--title", "new title",
              "--description", "new desc", "--expected-version", stale_version])

    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.title == "orig title"
    assert task.description == "orig desc"

    main(["update", task_id, "--title", "new title",
          "--description", "new desc", "--expected-version", good_version])
    capsys.readouterr()

    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.title == "new title"
    assert task.description == "new desc"


def test_update_title_only_leaves_description_unchanged(tmp_db, capsys):
    """Supplying only --title must NOT null out --description. This is red against
    a naive patch built as {"title": args.title, "description": args.description}
    (argparse fills the omitted --description with None, which update_task's RFC
    7386 key-presence contract reads as an explicit clear)."""
    main(["create", "--description", "keep me", "orig title"])
    task_id = capsys.readouterr().out.strip()

    with Store(tmp_db) as store:
        version = store.tasks.get(task_id).version

    main(["update", task_id, "--title", "new title", "--expected-version", version])
    capsys.readouterr()

    with Store(tmp_db) as store:
        task = store.tasks.get(task_id)
    assert task.title == "new title"
    assert task.description == "keep me"
