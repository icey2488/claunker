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
