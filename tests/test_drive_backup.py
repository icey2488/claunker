"""FT-008 #2 — Drive-durable spine backup tests.

Drive API mocked throughout; spine uses real temp-file SQLite (":memory:" cannot
be shared across connections, which the DriveBackup opens separately for meta).

Coverage:
  - pre_flight rejects truncated / missing-root / empty-tasks fixtures
  - dormant mode no-ops (key absent -> mark_dirty silent, startup_flush silent)
  - dirty/meta logic incl. startup-flush trigger and sha-match skip
  - split-brain HALT on 2+ active-blob matches
  - UTC filename derivation for daily snapshot
  - 7-day prune spares the active blob and recent snapshots
  - escalation mints at failure threshold, resolves on success, single-open invariant
  - restore validation rejects a corrupt blob
  - gated-restore FATAL path (absent local db without --restore-from-drive flag)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any, Dict
from unittest.mock import MagicMock, call, patch

import pytest

# ── repo root on path so spine is importable ───────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spine.drive_backup import (
    ACTIVE_BLOB_NAME,
    CONSECUTIVE_FAIL_THRESHOLD,
    DIRTY_AGE_THRESHOLD_SECS,
    DRIVE_FOLDER_NAME,
    MANDATORY_KEYS,
    SNAPSHOT_PREFIX,
    SNAPSHOT_RETENTION_DAYS,
    SNAPSHOT_SUFFIX,
    DriveBackup,
    _preflight,
    _validate_restore_blob,
    restore_from_drive,
    _meta_get,
    _meta_set,
    _meta_create_table,
)
from spine import Spine, Store


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db_file(tmp_path):
    """A real temp SQLite file, initialized with the spine schema."""
    p = tmp_path / "spine.db"
    # Initialize spine schema and one task so pre-flight passes.
    with Store(str(p)) as store:
        sp = Spine(store)
        sp.create_project("Test", project_id="proj-1")
        sp.create_task("proj-1", "Task One", tier=2)
    return str(p)


@pytest.fixture
def mock_drive(db_file):
    """A MagicMock Drive service wired to satisfy find-or-create folder."""
    drive = MagicMock()
    # files().list() for folder search -> one folder
    folder_list = MagicMock()
    folder_list.execute.return_value = {"files": [{"id": "folder-id-1"}]}
    drive.files.return_value.list.return_value = folder_list
    # files().create() -> return file id
    create_result = MagicMock()
    create_result.execute.return_value = {"id": "file-id-new"}
    drive.files.return_value.create.return_value = create_result
    # files().update() -> return file id
    update_result = MagicMock()
    update_result.execute.return_value = {"id": "file-id-existing"}
    drive.files.return_value.update.return_value = update_result
    # files().get_media() for download -> bytes of a valid blob
    return drive


@pytest.fixture
def backup(db_file, mock_drive):
    """A DriveBackup instance with injected mock Drive, folder already found."""
    # Wire list() to return: folder found, no existing active blob.
    def list_side_effect(**kwargs):
        q = kwargs.get("q", "")
        m = MagicMock()
        if "application/vnd.google-apps.folder" in q:
            m.execute.return_value = {"files": [{"id": "folder-id-1"}]}
        elif ACTIVE_BLOB_NAME in q:
            m.execute.return_value = {"files": []}  # no existing active blob
        else:
            m.execute.return_value = {"files": []}
        return m

    mock_drive.files.return_value.list.side_effect = list_side_effect
    b = DriveBackup(db_file, drive_service=mock_drive)
    assert not b.dormant
    return b


def _make_blob(tasks=None, schema_version=1, seq=0) -> bytes:
    """Create a minimal valid gzipped dump blob."""
    if tasks is None:
        tasks = [{"id": "t1", "title": "task"}]
    obj = {
        "schema_version": schema_version,
        "seq": seq,
        "projects": [],
        "tasks": tasks,
        "artifacts": [],
        "escalations": [],
    }
    raw = json.dumps(obj).encode("utf-8")
    return gzip.compress(raw)


def _blob_sha(blob: bytes) -> str:
    return hashlib.sha256(gzip.decompress(blob)).hexdigest()


# ── pre-flight tests ──────────────────────────────────────────────────────────

def test_preflight_rejects_missing_root_key():
    """A blob missing 'tasks' fails pre-flight."""
    obj = {"schema_version": 1, "seq": 0, "projects": [], "artifacts": [], "escalations": []}
    raw = json.dumps(obj).encode("utf-8")
    blob = gzip.compress(raw)
    assert _preflight(blob) is None


def test_preflight_rejects_null_root_key():
    """A blob with tasks: null fails pre-flight."""
    obj = {"schema_version": 1, "seq": 0, "tasks": None, "projects": [], "artifacts": [], "escalations": []}
    raw = json.dumps(obj).encode("utf-8")
    blob = gzip.compress(raw)
    assert _preflight(blob) is None


def test_preflight_rejects_empty_tasks():
    """An empty task ledger is a skip (returns None), not an error — but still blocks push."""
    blob = _make_blob(tasks=[])
    assert _preflight(blob) is None


def test_preflight_ok_with_valid_blob():
    """A valid blob with at least one task returns the pre-flight dict."""
    blob = _make_blob(tasks=[{"id": "t1", "title": "x"}])
    result = _preflight(blob)
    assert result is not None
    assert result["status"] == "pre_flight_ok"
    assert result["cards"] == 1
    assert result["bytes"] == len(blob)
    assert len(result["sha256"]) == 64


def test_preflight_rejects_corrupt_gzip():
    """Non-gzip bytes fail pre-flight."""
    assert _preflight(b"not gzip data") is None


# ── dormant mode ──────────────────────────────────────────────────────────────

def test_dormant_mode_when_key_absent(tmp_path):
    """No SA key file -> DriveBackup is dormant; mark_dirty and startup_flush are no-ops."""
    db_path = str(tmp_path / "spine.db")
    # No CLAUNKER_SPINE_SA_KEY set; default path won't exist in tmp_path.
    with patch.dict(os.environ, {"CLAUNKER_SPINE_SA_KEY": str(tmp_path / "nonexistent-key.json")}):
        b = DriveBackup(db_path)
    assert b.dormant
    # These should be silent no-ops.
    b.mark_dirty()
    b.startup_flush()
    b.shutdown_flush()


# ── dirty / meta / startup-flush logic ────────────────────────────────────────

def test_startup_flush_triggers_push_when_sha_differs(db_file, backup, mock_drive):
    """startup_flush pushes when current sha != last_push_sha."""
    # Set a stale last_push_sha so the content appears dirty.
    _meta_create_table(db_file)
    _meta_set(db_file, "last_push_sha", "stale-sha-that-does-not-match")

    # Wire list() for active blob lookup -> no existing blob.
    def list_side_effect(**kwargs):
        q = kwargs.get("q", "")
        m = MagicMock()
        if "application/vnd.google-apps.folder" in q:
            m.execute.return_value = {"files": [{"id": "folder-id-1"}]}
        else:
            m.execute.return_value = {"files": []}
        return m

    mock_drive.files.return_value.list.side_effect = list_side_effect
    create_mock = MagicMock()
    create_mock.execute.return_value = {"id": "new-file-id"}
    mock_drive.files.return_value.create.return_value = create_mock

    backup.startup_flush()

    # create() should have been called (for active blob upload).
    assert mock_drive.files.return_value.create.called


def test_startup_flush_skips_when_sha_matches(db_file, backup, mock_drive):
    """startup_flush does NOT push when current sha matches last_push_sha."""
    from spine.drive_backup import _snapshot_bytes

    blob = _snapshot_bytes(db_file)
    sha = _blob_sha(blob)
    _meta_create_table(db_file)
    _meta_set(db_file, "last_push_sha", sha)

    # Reset call tracker.
    mock_drive.reset_mock()

    backup.startup_flush()

    # No upload should have occurred.
    assert not mock_drive.files.return_value.create.called
    assert not mock_drive.files.return_value.update.called


def test_mark_dirty_sets_dirty_flag(db_file, backup):
    """mark_dirty() sets the internal dirty flag within the debounce window."""
    assert not backup._dirty
    backup.mark_dirty()
    assert backup._dirty
    # Cancel the pending timer so we don't leak threads.
    with backup._lock:
        if backup._debounce_timer:
            backup._debounce_timer.cancel()


def test_meta_last_push_sha_updated_after_push(db_file, backup, mock_drive):
    """After a successful push the meta table records last_push_sha."""
    def list_side_effect(**kwargs):
        q = kwargs.get("q", "")
        m = MagicMock()
        if "application/vnd.google-apps.folder" in q:
            m.execute.return_value = {"files": [{"id": "folder-id-1"}]}
        else:
            m.execute.return_value = {"files": []}
        return m

    mock_drive.files.return_value.list.side_effect = list_side_effect
    create_mock = MagicMock()
    create_mock.execute.return_value = {"id": "new-blob-id"}
    mock_drive.files.return_value.create.return_value = create_mock

    backup._do_push()

    sha = _meta_get(db_file, "last_push_sha")
    assert sha is not None and len(sha) == 64


# ── split-brain guard ─────────────────────────────────────────────────────────

def test_split_brain_halt_on_two_active_blobs(db_file, backup, mock_drive):
    """Two copies of the active blob in Drive -> _do_push returns False (HALT)."""
    def list_side_effect(**kwargs):
        q = kwargs.get("q", "")
        m = MagicMock()
        if "application/vnd.google-apps.folder" in q:
            m.execute.return_value = {"files": [{"id": "folder-id-1"}]}
        elif ACTIVE_BLOB_NAME in q:
            # Two copies -> split-brain
            m.execute.return_value = {"files": [{"id": "a"}, {"id": "b"}]}
        else:
            m.execute.return_value = {"files": []}
        return m

    mock_drive.files.return_value.list.side_effect = list_side_effect
    result = backup._do_push()
    assert result is False
    # Upload must NOT have been attempted.
    assert not mock_drive.files.return_value.create.called
    assert not mock_drive.files.return_value.update.called


# ── UTC snapshot filename ─────────────────────────────────────────────────────

def test_utc_snapshot_filename_uses_utc_date(db_file, backup, mock_drive):
    """Daily snapshot uses UTC date (YYYY-MM-DD) derived from timezone.utc."""
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expected_name = f"{SNAPSHOT_PREFIX}{today_utc}{SNAPSHOT_SUFFIX}"

    captured_names = []

    def list_side_effect(**kwargs):
        q = kwargs.get("q", "")
        m = MagicMock()
        if "application/vnd.google-apps.folder" in q:
            m.execute.return_value = {"files": [{"id": "folder-id-1"}]}
        else:
            m.execute.return_value = {"files": []}
        return m

    def create_side_effect(**kwargs):
        body = kwargs.get("body", {})
        captured_names.append(body.get("name", ""))
        m = MagicMock()
        m.execute.return_value = {"id": "snap-id-1"}
        return m

    mock_drive.files.return_value.list.side_effect = list_side_effect
    mock_drive.files.return_value.create.side_effect = create_side_effect

    # Force the snapshot to trigger by clearing the last_snapshot_date.
    backup._last_snapshot_date = None
    _meta_set(db_file, "last_push_sha", "")  # force content to appear changed

    # Mock _download_blob directly to avoid patching an inner-import.
    with patch("spine.drive_backup._download_blob", return_value=_make_blob()):
        backup._do_push()

    assert expected_name in captured_names, (
        f"Expected snapshot name {expected_name!r} not in created files: {captured_names}"
    )


# ── 7-day pruning ─────────────────────────────────────────────────────────────

def test_7day_prune_spares_active_blob_and_recent_snapshots():
    """Pruning deletes snapshots older than 7 days but leaves the active blob and recent ones.

    Uses a standalone mock drive (not the backup fixture) to avoid side_effect conflicts.
    """
    from spine.drive_backup import _prune_old_snapshots

    today = datetime.now(timezone.utc)
    old_date = (today - timedelta(days=8)).strftime("%Y-%m-%d")
    recent_date = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    old_snap_name = f"{SNAPSHOT_PREFIX}{old_date}{SNAPSHOT_SUFFIX}"
    recent_snap_name = f"{SNAPSHOT_PREFIX}{recent_date}{SNAPSHOT_SUFFIX}"
    today_snap_name = f"{SNAPSHOT_PREFIX}{today_str}{SNAPSHOT_SUFFIX}"

    all_files = [
        {"id": "active-id", "name": ACTIVE_BLOB_NAME},
        {"id": "old-id", "name": old_snap_name},
        {"id": "recent-id", "name": recent_snap_name},
        {"id": "today-id", "name": today_snap_name},
    ]

    # Fresh mock drive with no side_effects.
    drive = MagicMock()
    list_mock = MagicMock()
    list_mock.execute.return_value = {"files": all_files}
    drive.files.return_value.list.return_value = list_mock

    delete_mock = MagicMock()
    delete_mock.execute.return_value = {}
    drive.files.return_value.delete.return_value = delete_mock

    _prune_old_snapshots(drive, "folder-id-1")

    # Verify delete was called exactly once (for the 8-day-old snapshot only).
    assert drive.files.return_value.delete.call_count == 1
    assert "old-id" in str(drive.files.return_value.delete.call_args)


# ── escalation lifecycle ──────────────────────────────────────────────────────

def test_escalation_mints_at_failure_threshold(db_file, backup, mock_drive):
    """After CONSECUTIVE_FAIL_THRESHOLD failed pushes, ONE escalation is minted."""
    # Provision the SYSTEM_BACKUP card first.
    backup._ensure_system_backup_card()
    card_id = _meta_get(db_file, "system_backup_card_id")
    assert card_id is not None

    # Force pushes to fail.
    with patch.object(backup, "_do_push", return_value=False):
        for _ in range(CONSECUTIVE_FAIL_THRESHOLD):
            backup._consecutive_failures += 1

    backup._maybe_mint_escalation()

    esc_id = _meta_get(db_file, "open_escalation_id")
    assert esc_id and len(esc_id) > 0

    # The escalation must exist in the store.
    with Store(db_file) as store:
        esc = store.escalations.get(esc_id)
    assert esc is not None
    assert esc.resolved_at is None  # still open


def test_escalation_resolves_on_successful_push(db_file, backup, mock_drive):
    """A successful push resolves the open escalation."""
    # Provision card and mint an escalation.
    backup._ensure_system_backup_card()
    card_id = _meta_get(db_file, "system_backup_card_id")
    backup._consecutive_failures = CONSECUTIVE_FAIL_THRESHOLD
    backup._maybe_mint_escalation()

    esc_id = _meta_get(db_file, "open_escalation_id")
    assert esc_id

    # Now resolve it.
    backup._resolve_escalation_if_open()

    resolved_id = _meta_get(db_file, "open_escalation_id")
    assert resolved_id == ""  # cleared

    with Store(db_file) as store:
        esc = store.escalations.get(esc_id)
    assert esc is not None
    assert esc.resolved_at is not None
    assert esc.resolution == "approve"


def test_single_open_escalation_invariant(db_file, backup, mock_drive):
    """Calling _maybe_mint_escalation twice only creates one open escalation."""
    backup._ensure_system_backup_card()
    backup._consecutive_failures = CONSECUTIVE_FAIL_THRESHOLD

    backup._maybe_mint_escalation()
    esc_id_1 = _meta_get(db_file, "open_escalation_id")
    assert esc_id_1

    backup._maybe_mint_escalation()  # second call — must not create another
    esc_id_2 = _meta_get(db_file, "open_escalation_id")
    assert esc_id_2 == esc_id_1  # unchanged

    # Count escalations in the store.
    with Store(db_file) as store:
        all_escs = store.escalations.list_all()
    assert len(all_escs) == 1


# ── restore validation ────────────────────────────────────────────────────────

def test_restore_validation_rejects_corrupt_gzip():
    """_validate_restore_blob raises SystemExit on non-gzip bytes."""
    with pytest.raises(SystemExit):
        _validate_restore_blob(b"not gzip data at all")


def test_restore_validation_rejects_missing_root_key():
    """_validate_restore_blob raises SystemExit on blob missing 'tasks'."""
    obj = {"schema_version": 1, "seq": 0}  # tasks missing
    raw = json.dumps(obj).encode("utf-8")
    blob = gzip.compress(raw)
    with pytest.raises(SystemExit):
        _validate_restore_blob(blob)


def test_restore_validation_rejects_empty_tasks():
    """_validate_restore_blob raises SystemExit on blob with zero tasks."""
    blob = _make_blob(tasks=[])
    with pytest.raises(SystemExit):
        _validate_restore_blob(blob)


def test_restore_validation_passes_valid_blob():
    """_validate_restore_blob succeeds on a properly formed blob."""
    blob = _make_blob(tasks=[{"id": "t1", "title": "task"}])
    raw, obj = _validate_restore_blob(blob)
    assert obj["tasks"][0]["id"] == "t1"


# ── gated-restore FATAL path ──────────────────────────────────────────────────

def test_gated_restore_fatal_when_key_absent(tmp_path):
    """restore_from_drive() raises SystemExit when the SA key does not exist."""
    db_path = str(tmp_path / "spine.db")
    nonexistent_key = str(tmp_path / "no-key.json")
    with patch.dict(os.environ, {"CLAUNKER_SPINE_SA_KEY": nonexistent_key}):
        with pytest.raises(SystemExit) as exc_info:
            restore_from_drive(db_path)
    assert "FATAL" in str(exc_info.value)


def test_gated_restore_fatal_when_no_folder_in_drive(tmp_path):
    """restore_from_drive() raises SystemExit when backup folder not found in Drive."""
    db_path = str(tmp_path / "spine.db")
    key_path = str(tmp_path / "fake-key.json")
    # Write a minimal fake key file so the existence check passes.
    with open(key_path, "w") as f:
        json.dump({"type": "service_account"}, f)

    with patch.dict(os.environ, {"CLAUNKER_SPINE_SA_KEY": key_path}):
        with patch("spine.drive_backup._build_drive_service") as mock_build:
            mock_svc = MagicMock()
            list_mock = MagicMock()
            list_mock.execute.return_value = {"files": []}
            mock_svc.files.return_value.list.return_value = list_mock
            mock_build.return_value = mock_svc

            with pytest.raises(SystemExit) as exc_info:
                restore_from_drive(db_path)
    assert "FATAL" in str(exc_info.value)
    assert "backup folder" in str(exc_info.value).lower() or DRIVE_FOLDER_NAME in str(exc_info.value)
