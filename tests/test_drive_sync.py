"""Tests for spine/drive_sync.py — the Drive pull-merge-push cycle (card
3ae179b1). Drive API mocked throughout; the merge step uses an INJECTABLE
callable (merge_fn) mirroring drive_backup.py's drive_service injection
pattern, so these tests never shell to node.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spine import Spine, Store
from spine.drive_sync import DRIVE_FOLDER_NAME, SYNC_BLOB_NAME, DriveSync, _preflight
from spine.merge_bridge import MergeBridgeError


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db_file(tmp_path):
    p = tmp_path / "spine.db"
    with Store(str(p)) as store:
        sp = Spine(store)
        sp.create_project("Test", project_id="proj-1")
        sp.create_task("proj-1", "Task One", tier=2)
    return str(p)


def _list_side_effect_factory(active_files):
    def list_side_effect(**kwargs):
        q = kwargs.get("q", "")
        m = MagicMock()
        if "application/vnd.google-apps.folder" in q:
            m.execute.return_value = {"files": [{"id": "folder-id-1"}]}
        elif SYNC_BLOB_NAME in q:
            m.execute.return_value = {"files": active_files}
        else:
            m.execute.return_value = {"files": []}
        return m
    return list_side_effect


@pytest.fixture
def mock_drive():
    drive = MagicMock()
    create_result = MagicMock()
    create_result.execute.return_value = {"id": "file-id-new"}
    drive.files.return_value.create.return_value = create_result
    update_result = MagicMock()
    update_result.execute.return_value = {"id": "file-id-existing"}
    drive.files.return_value.update.return_value = update_result
    return drive


def _noop_merge(local, remote):
    return local, []


# ── dormant mode ───────────────────────────────────────────────────────────────

def test_dormant_mode_when_key_absent(tmp_path):
    db_path = str(tmp_path / "spine.db")
    with patch.dict(os.environ, {"CLAUNKER_SPINE_SA_KEY": str(tmp_path / "nonexistent-key.json")}):
        ds = DriveSync(db_path)
    assert ds.dormant
    # No-ops, never raise, never touch Drive.
    ds.mark_dirty()
    ds.startup_flush()
    ds.shutdown_flush()


# ── bootstrap (no remote blob yet) ─────────────────────────────────────────────

def test_bootstrap_seeds_from_local_when_remote_absent(db_file, mock_drive):
    mock_drive.files.return_value.list.side_effect = _list_side_effect_factory([])
    ds = DriveSync(db_file, drive_service=mock_drive, merge_fn=_noop_merge)
    assert not ds.dormant

    ok = ds.run_cycle()
    assert ok
    # Bootstrap uploads (create), never updates an existing file.
    assert mock_drive.files.return_value.create.called
    assert not mock_drive.files.return_value.update.called


def test_folder_uses_dedicated_sync_folder_name(db_file, mock_drive):
    """The sync channel's folder is claunker-spine-sync/, distinct from
    drive_backup.py's claunker-spine-backups/."""
    assert DRIVE_FOLDER_NAME == "claunker-spine-sync"
    mock_drive.files.return_value.list.side_effect = _list_side_effect_factory([])
    ds = DriveSync(db_file, drive_service=mock_drive, merge_fn=_noop_merge)
    assert ds._folder_id == "folder-id-1"


# ── pull-merge-push cycle with an existing remote blob ────────────────────────

def _make_remote_blob(task_id="X", version="9:zzzz", title="remote-edit"):
    obj = {
        "schema_version": 1,
        "seq": 9,
        "projects": [],
        "tasks": [{"id": task_id, "project_id": "proj-1", "title": title, "version": version, "deleted_at": None}],
        "artifacts": [],
        "escalations": [],
    }
    raw = json.dumps(obj).encode("utf-8")
    return gzip.compress(raw)


def test_cycle_calls_merge_fn_and_loads_result(db_file, mock_drive):
    mock_drive.files.return_value.list.side_effect = _list_side_effect_factory([{"id": "existing-blob-id"}])

    remote_blob = _make_remote_blob()
    with patch("spine.drive_sync._dc.download_blob", return_value=remote_blob):
        merge_calls = []

        def spy_merge(local, remote):
            merge_calls.append((local, remote))
            merged = dict(local)
            merged["tasks"] = local.get("tasks", []) + remote.get("tasks", [])
            return merged, [{"collection": "tasks", "id": "conflict-1", "of": "X", "source_version": "9:zzzz"}]

        ds = DriveSync(db_file, drive_service=mock_drive, merge_fn=spy_merge)
        ok = ds.run_cycle()

    assert ok
    assert len(merge_calls) == 1
    local_arg, remote_arg = merge_calls[0]
    assert any(t["id"] for t in local_arg["tasks"])  # local dump was passed
    assert remote_arg["tasks"][0]["id"] == "X"

    # Merged result was loaded back into the local store.
    with Store(db_file) as store:
        ids = {t.id for t in store.tasks.list_all()}
    assert "X" in ids

    # Push used update() against the existing blob id (not create()).
    assert mock_drive.files.return_value.update.called


def test_cycle_push_uses_gzip_and_sha256(db_file, mock_drive):
    mock_drive.files.return_value.list.side_effect = _list_side_effect_factory([])
    ds = DriveSync(db_file, drive_service=mock_drive, merge_fn=_noop_merge)
    ok = ds.run_cycle()
    assert ok

    # The uploaded media is a valid, preflight-passing gzip blob.
    create_call = mock_drive.files.return_value.create
    assert create_call.called


def test_split_brain_halts(db_file, mock_drive):
    mock_drive.files.return_value.list.side_effect = _list_side_effect_factory(
        [{"id": "a"}, {"id": "b"}]
    )
    ds = DriveSync(db_file, drive_service=mock_drive, merge_fn=_noop_merge)

    with pytest.raises(RuntimeError, match="SPLIT-BRAIN"):
        ds.run_cycle()

    assert not mock_drive.files.return_value.create.called
    assert not mock_drive.files.return_value.update.called


# ── node-absent hard fail (fail-closed, never a silent skip) ─────────────────

def test_cycle_hard_fails_when_node_absent(db_file, mock_drive):
    """With a real remote blob present (so the merge path — not bootstrap — is
    taken) and node unresolvable, the cycle raises MergeBridgeError rather than
    silently skipping the sync."""
    mock_drive.files.return_value.list.side_effect = _list_side_effect_factory([{"id": "existing-blob-id"}])
    remote_blob = _make_remote_blob()

    with patch("spine.drive_sync._dc.download_blob", return_value=remote_blob):
        # Use the REAL run_merge (default merge_fn) with node resolution forced absent.
        ds = DriveSync(db_file, drive_service=mock_drive)
        with patch("spine.merge_bridge.NODE_PATH", None):
            with pytest.raises(MergeBridgeError, match="node executable not found"):
                ds.run_cycle()

    assert not mock_drive.files.return_value.update.called


# ── preflight ──────────────────────────────────────────────────────────────────

def test_preflight_rejects_missing_mandatory_key():
    obj = {"schema_version": 1, "seq": 0, "tasks": [], "artifacts": [], "escalations": []}  # no 'projects'
    blob = gzip.compress(json.dumps(obj).encode("utf-8"))
    assert _preflight(blob) is None


def test_preflight_ok_with_valid_blob():
    obj = {"schema_version": 1, "seq": 0, "projects": [], "tasks": [], "artifacts": [], "escalations": []}
    blob = gzip.compress(json.dumps(obj).encode("utf-8"))
    result = _preflight(blob)
    assert result is not None
    assert result["status"] == "pre_flight_ok"
    assert len(result["sha256"]) == 64


def test_preflight_rejects_corrupt_gzip():
    assert _preflight(b"not gzip data") is None


# ── mark_dirty / debounce ──────────────────────────────────────────────────────

def test_mark_dirty_schedules_debounce_timer(db_file, mock_drive):
    mock_drive.files.return_value.list.side_effect = _list_side_effect_factory([])
    ds = DriveSync(db_file, drive_service=mock_drive, merge_fn=_noop_merge)
    ds.mark_dirty()
    assert ds._dirty
    assert ds._debounce_timer is not None
    ds._debounce_timer.cancel()  # don't let the real cycle fire in the test


def test_shutdown_flush_is_noop_when_clean(db_file, mock_drive):
    mock_drive.files.return_value.list.side_effect = _list_side_effect_factory([])
    ds = DriveSync(db_file, drive_service=mock_drive, merge_fn=_noop_merge)
    ds.shutdown_flush()  # _dirty is False -> no cycle, no Drive calls
    assert not mock_drive.files.return_value.create.called
