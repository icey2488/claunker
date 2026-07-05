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
