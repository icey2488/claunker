"""spine/drive_backup.py — Drive-durable backup for the Claunker Spine.

DORMANT until CLAUNKER_SPINE_SA_KEY env var points to an existing SA key file.
When dormant: one INFO log line at startup, all public methods are no-ops.

Architecture:
  Active blob  : claunker_spine_v1.json.gz  (single mutable object, always current)
  Daily snapshot: claunker_spine_v1_YYYY-MM-DD.json.gz (immutable, 7-day retention)
  Scope        : https://www.googleapis.com/auth/drive.file

Split-brain guard: zero active blobs -> create; one -> update in-place; two+ -> HALT.

Failure escalation (fail-open, never silent):
  5 consecutive push failures OR dirty-age > 15 min -> mint ONE Escalation against
  SYSTEM_BACKUP card (auto-provisioned on first activation).
  Next verified successful push -> resolve the escalation (MI-2 single write).
  Single-open invariant: never more than one open backup escalation at a time.

Restore (gated, manual):
  Call restore_from_drive(db_path) when --restore-from-drive flag is set and the
  local ledger is absent. Structural validation (root keys, tasks >= 1, gzip
  integrity) runs before load(). Auto-restore does not exist.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_FOLDER_NAME = "claunker-spine-backups"
ACTIVE_BLOB_NAME = "claunker_spine_v1.json.gz"
SNAPSHOT_PREFIX = "claunker_spine_v1_"
SNAPSHOT_SUFFIX = ".json.gz"

# Root keys that MUST be present and non-null in every dump envelope.
MANDATORY_KEYS = ("tasks", "schema_version", "seq")

META_TABLE = "backup_meta"

DEBOUNCE_SECS = 30
CONSECUTIVE_FAIL_THRESHOLD = 5
DIRTY_AGE_THRESHOLD_SECS = 15 * 60  # 15 minutes
SNAPSHOT_RETENTION_DAYS = 7

SYSTEM_PROJECT_ID = "claunker-system-backup"
SYSTEM_BACKUP_TASK_TITLE = "SYSTEM_BACKUP"


# ── helpers ───────────────────────────────────────────────────────────────────

def _default_key_path(db_path: str) -> str:
    """SA key default: beside the spine's .env.spine-token (repo root)."""
    spine_dir = os.path.dirname(os.path.abspath(db_path))
    repo_root = os.path.dirname(spine_dir)
    return os.path.join(repo_root, "claunker-spine-sa-key.json")


def _meta_create_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {META_TABLE} "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()


def _meta_get(db_path: str, key: str) -> Optional[str]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"SELECT value FROM {META_TABLE} WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else None


def _meta_set(db_path: str, key: str, value: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()


def _meta_set_many(db_path: str, pairs: List[Tuple[str, str]]) -> None:
    with sqlite3.connect(db_path) as conn:
        for key, value in pairs:
            conn.execute(
                f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()


def _snapshot_bytes(db_path: str) -> bytes:
    """Read a consistent snapshot of the four entity tables in ONE SQLite read
    transaction (BEGIN DEFERRED), gzip the result. Zero torn reads."""
    from .storage import TABLES, SCHEMA_VERSION

    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN DEFERRED")
        out: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "seq": 0}
        for table in TABLES:
            rows = conn.execute(f"SELECT data FROM {table}").fetchall()
            out[table] = [json.loads(r[0]) for r in rows]

    raw = json.dumps(out, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw)


def _preflight(blob: bytes) -> Optional[Dict[str, Any]]:
    """Validate a gzipped dump blob before upload.

    Returns {status, bytes, cards, sha256} on success, or None on failure/skip
    (failures are logged; an empty task ledger is a SKIP, not an error).
    """
    try:
        raw = gzip.decompress(blob)
        obj = json.loads(raw)
    except Exception as exc:
        log.error("drive_backup: pre_flight FAIL — cannot decompress/parse: %s", exc)
        return None

    for key in MANDATORY_KEYS:
        if key not in obj or obj[key] is None:
            log.error("drive_backup: pre_flight FAIL — missing mandatory key %r", key)
            return None

    tasks = obj.get("tasks", [])
    if len(tasks) < 1:
        log.info("drive_backup: pre_flight SKIP — empty task ledger, nothing to push")
        return None

    sha = hashlib.sha256(raw).hexdigest()
    result = {
        "status": "pre_flight_ok",
        "bytes": len(blob),
        "cards": len(tasks),
        "sha256": sha,
    }
    log.info("drive_backup: %s", json.dumps(result))
    return result


def _validate_restore_blob(blob: bytes) -> Tuple[bytes, Dict[str, Any]]:
    """Validate a downloaded restore blob. Returns (raw_bytes, parsed_obj) or raises SystemExit."""
    try:
        raw = gzip.decompress(blob)
        obj = json.loads(raw)
    except Exception as exc:
        raise SystemExit(f"FATAL: restore blob corrupt (cannot decompress/parse): {exc}")

    for key in MANDATORY_KEYS:
        if key not in obj or obj[key] is None:
            raise SystemExit(f"FATAL: restore blob missing mandatory key {key!r}")

    tasks = obj.get("tasks", [])
    if len(tasks) < 1:
        raise SystemExit("FATAL: restore blob has empty task ledger (tasks >= 1 required)")

    return raw, obj


# ── Drive helpers ─────────────────────────────────────────────────────────────

def _build_drive_service(key_path: str):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=[DRIVE_SCOPE]
    )
    return build("drive", "v3", credentials=creds)


def _find_or_create_folder(drive) -> str:
    results = (
        drive.files()
        .list(
            q=(
                f"name='{DRIVE_FOLDER_NAME}' and "
                "mimeType='application/vnd.google-apps.folder' and trashed=false"
            ),
            fields="files(id)",
        )
        .execute()
    )
    folders = results.get("files", [])
    if folders:
        return folders[0]["id"]
    meta = {
        "name": DRIVE_FOLDER_NAME,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = drive.files().create(body=meta, fields="id").execute()
    return folder["id"]


def _find_active_blob_id(drive, folder_id: str) -> Optional[str]:
    """Find the active blob by name. Raises RuntimeError on split-brain (2+ copies)."""
    results = (
        drive.files()
        .list(
            q=(
                f"name='{ACTIVE_BLOB_NAME}' and "
                f"'{folder_id}' in parents and trashed=false"
            ),
            fields="files(id)",
        )
        .execute()
    )
    files = results.get("files", [])
    if len(files) >= 2:
        raise RuntimeError(
            f"drive_backup: SPLIT-BRAIN HALT — found {len(files)} copies of "
            f"{ACTIVE_BLOB_NAME!r} in {DRIVE_FOLDER_NAME!r}; "
            "manual deduplication required before backup can resume"
        )
    return files[0]["id"] if files else None


def _upload_blob(drive, folder_id: str, name: str, blob: bytes, existing_id: Optional[str] = None) -> str:
    from googleapiclient.http import MediaInMemoryUpload

    media = MediaInMemoryUpload(blob, mimetype="application/gzip", resumable=False)
    if existing_id:
        result = (
            drive.files()
            .update(fileId=existing_id, media_body=media, fields="id")
            .execute()
        )
    else:
        file_meta = {"name": name, "parents": [folder_id]}
        result = (
            drive.files()
            .create(body=file_meta, media_body=media, fields="id")
            .execute()
        )
    return result["id"]


def _download_blob(drive, file_id: str) -> bytes:
    import io
    from googleapiclient.http import MediaIoBaseDownload

    request = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


def _prune_old_snapshots(drive, folder_id: str) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_RETENTION_DAYS)).strftime("%Y-%m-%d")
    results = (
        drive.files()
        .list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name)",
        )
        .execute()
    )
    for f in results.get("files", []):
        name = f["name"]
        if not (name.startswith(SNAPSHOT_PREFIX) and name.endswith(SNAPSHOT_SUFFIX) and name != ACTIVE_BLOB_NAME):
            continue
        date_part = name[len(SNAPSHOT_PREFIX): -len(SNAPSHOT_SUFFIX)]
        if len(date_part) == 10 and date_part < cutoff:
            try:
                drive.files().delete(fileId=f["id"]).execute()
                log.info("drive_backup: pruned old snapshot %s", name)
            except Exception as exc:
                log.warning("drive_backup: failed to prune %s: %s", name, exc)


# ── restore (standalone, gated) ────────────────────────────────────────────────

def restore_from_drive(db_path: str) -> None:
    """Download the active blob from Drive, validate it, and load() into the spine.

    Raises SystemExit on any failure. Called by the server main() when
    --restore-from-drive is specified and the local ledger is absent.
    """
    key_path = os.environ.get("CLAUNKER_SPINE_SA_KEY") or _default_key_path(db_path)
    if not os.path.exists(key_path):
        raise SystemExit(
            f"FATAL: --restore-from-drive: SA key not found at {key_path} "
            "(set CLAUNKER_SPINE_SA_KEY or place key beside the project root)"
        )

    try:
        drive = _build_drive_service(key_path)
    except Exception as exc:
        raise SystemExit(f"FATAL: --restore-from-drive: could not init Drive client: {exc}")

    results = (
        drive.files()
        .list(
            q=(
                f"name='{DRIVE_FOLDER_NAME}' and "
                "mimeType='application/vnd.google-apps.folder' and trashed=false"
            ),
            fields="files(id)",
        )
        .execute()
    )
    folders = results.get("files", [])
    if not folders:
        raise SystemExit(
            f"FATAL: --restore-from-drive: backup folder {DRIVE_FOLDER_NAME!r} not found in Drive"
        )
    folder_id = folders[0]["id"]

    blob_id = _find_active_blob_id(drive, folder_id)
    if blob_id is None:
        raise SystemExit(
            f"FATAL: --restore-from-drive: active blob {ACTIVE_BLOB_NAME!r} not found in backup folder"
        )

    log.info("drive_backup: restore downloading active blob (%s)...", blob_id)
    try:
        blob = _download_blob(drive, blob_id)
    except Exception as exc:
        raise SystemExit(f"FATAL: --restore-from-drive: download failed: {exc}")

    raw, obj = _validate_restore_blob(blob)
    sha = hashlib.sha256(raw).hexdigest()
    log.info("drive_backup: restore validated (sha=%s, cards=%d)", sha, len(obj["tasks"]))

    from .storage import Store
    with Store(db_path) as store:
        store.load(obj)

    # Prime the meta table so startup_flush sees the restore as a clean push.
    _meta_create_table(db_path)
    _meta_set_many(db_path, [
        ("last_push_sha", sha),
        ("last_push_ts", datetime.now(timezone.utc).isoformat()),
    ])
    log.info("drive_backup: restore complete")


# ── main backup class ─────────────────────────────────────────────────────────

class DriveBackup:
    """Drive backup manager for the Claunker Spine.

    Pass ``drive_service`` in tests to inject a mock; leave None in production
    (the SA key file is used). Dormant when the key file is absent or Drive
    initialization fails.
    """

    def __init__(self, db_path: str, *, drive_service=None) -> None:
        self._db_path = db_path
        self._dormant = False

        if drive_service is None:
            key_path = os.environ.get("CLAUNKER_SPINE_SA_KEY") or _default_key_path(db_path)
            if not os.path.exists(key_path):
                log.info("drive_backup: DORMANT (SA key not found at %s)", key_path)
                self._dormant = True
                return
            try:
                drive_service = _build_drive_service(key_path)
            except Exception as exc:
                log.error("drive_backup: DORMANT (failed to init Drive client: %s)", exc)
                self._dormant = True
                return

        self._drive = drive_service

        # Ensure meta table and load last-snapshot date.
        try:
            _meta_create_table(db_path)
        except Exception as exc:
            log.error("drive_backup: DORMANT (failed to create meta table: %s)", exc)
            self._dormant = True
            return

        # In-memory state.
        self._dirty = False
        self._dirty_since: Optional[float] = None
        self._consecutive_failures = 0
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._last_snapshot_date: Optional[str] = _meta_get(db_path, "last_snapshot_date")

        # Find-or-create the backup folder.
        try:
            self._folder_id = _find_or_create_folder(drive_service)
        except Exception as exc:
            log.error("drive_backup: DORMANT (failed to init backup folder: %s)", exc)
            self._dormant = True
            return

        # Auto-provision the SYSTEM_BACKUP card (first-activation only).
        self._ensure_system_backup_card()

        log.info("drive_backup: ACTIVE (folder=%s)", self._folder_id)

    # ── public interface ──────────────────────────────────────────────────────

    @property
    def dormant(self) -> bool:
        return self._dormant

    def mark_dirty(self) -> None:
        """Call after each successful mutation. Schedules a debounced push. No-op if dormant."""
        if self._dormant:
            return
        with self._lock:
            now = time.monotonic()
            if not self._dirty:
                self._dirty = True
                self._dirty_since = now
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            t = threading.Timer(DEBOUNCE_SECS, self._push_worker)
            t.daemon = True
            t.start()
            self._debounce_timer = t

    def startup_flush(self) -> None:
        """Pre-open push: compare current sha to last_push_sha; if dirty, push now. No-op if dormant."""
        if self._dormant:
            return
        try:
            blob = _snapshot_bytes(self._db_path)
        except Exception as exc:
            log.error("drive_backup: startup_flush snapshot failed: %s", exc)
            return

        pf = _preflight(blob)
        if pf is None:
            return

        sha = pf["sha256"]
        last_sha = _meta_get(self._db_path, "last_push_sha")
        if sha == last_sha:
            log.info("drive_backup: startup_flush clean (sha matches last push)")
            return

        log.info("drive_backup: startup_flush dirty detected — pushing immediately")
        self._do_push()

    def shutdown_flush(self) -> None:
        """Best-effort push on clean shutdown. No-op if dormant."""
        if self._dormant:
            return
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
            should_push = self._dirty
        if should_push:
            log.info("drive_backup: shutdown_flush — best-effort push")
            self._do_push()

    # ── internal push machinery ───────────────────────────────────────────────

    def _do_push(self) -> bool:
        """Execute one full push cycle. Returns True on success."""
        try:
            blob = _snapshot_bytes(self._db_path)
        except Exception as exc:
            log.error("drive_backup: snapshot read failed: %s", exc)
            return False

        pf = _preflight(blob)
        if pf is None:
            return False

        sha = pf["sha256"]
        last_sha = _meta_get(self._db_path, "last_push_sha")
        if sha == last_sha:
            log.info("drive_backup: push skipped — sha unchanged")
            with self._lock:
                self._dirty = False
                self._dirty_since = None
            return True

        # Upload active blob with split-brain guard.
        try:
            existing_id = _find_active_blob_id(self._drive, self._folder_id)
        except RuntimeError as exc:
            log.error("%s", exc)
            return False
        except Exception as exc:
            log.error("drive_backup: active-blob lookup failed: %s", exc)
            return False

        try:
            _upload_blob(self._drive, self._folder_id, ACTIVE_BLOB_NAME, blob, existing_id)
        except Exception as exc:
            log.error("drive_backup: upload failed: %s", exc)
            return False

        ts = datetime.now(timezone.utc).isoformat()
        _meta_set_many(self._db_path, [
            ("last_push_sha", sha),
            ("last_push_ts", ts),
        ])

        with self._lock:
            self._dirty = False
            self._dirty_since = None

        log.info(
            "drive_backup: push OK (sha=%s, bytes=%d, cards=%d)",
            sha, pf["bytes"], pf["cards"],
        )

        # Daily immutable snapshot.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_snapshot_date != today:
            self._take_daily_snapshot(blob, today, sha)

        return True

    def _take_daily_snapshot(self, blob: bytes, today: str, sha: str) -> None:
        snap_name = f"{SNAPSHOT_PREFIX}{today}{SNAPSHOT_SUFFIX}"
        try:
            snap_id = _upload_blob(self._drive, self._folder_id, snap_name, blob)
            self._last_snapshot_date = today
            _meta_set(self._db_path, "last_snapshot_date", today)
            log.info("drive_backup: daily snapshot uploaded (%s)", snap_name)

            # 24h read-back verification.
            try:
                downloaded = _download_blob(self._drive, snap_id)
                raw = gzip.decompress(downloaded)
                got_sha = hashlib.sha256(raw).hexdigest()
                if got_sha == sha:
                    log.info("drive_backup: snapshot verify_ok (date=%s, sha=%s)", today, sha)
                else:
                    log.error(
                        "drive_backup: snapshot verify FAIL (date=%s, expected=%s, got=%s)",
                        today, sha, got_sha,
                    )
            except Exception as exc:
                log.error("drive_backup: snapshot read-back failed: %s", exc)

            _prune_old_snapshots(self._drive, self._folder_id)
        except Exception as exc:
            log.error("drive_backup: daily snapshot failed: %s", exc)

    def _push_worker(self) -> None:
        """Background timer callback — runs in a daemon thread."""
        with self._lock:
            self._debounce_timer = None

        success = self._do_push()

        do_resolve = False
        do_escalate = False

        with self._lock:
            if success:
                self._consecutive_failures = 0
                do_resolve = True
            else:
                self._consecutive_failures += 1
                dirty_age = (
                    time.monotonic() - self._dirty_since
                    if self._dirty_since is not None
                    else 0.0
                )
                do_escalate = (
                    self._consecutive_failures >= CONSECUTIVE_FAIL_THRESHOLD
                    or (
                        self._dirty_since is not None
                        and dirty_age > DIRTY_AGE_THRESHOLD_SECS
                        and self._consecutive_failures > 0
                    )
                )

        if do_resolve:
            self._resolve_escalation_if_open()
        if do_escalate:
            self._maybe_mint_escalation()

    # ── escalation management ─────────────────────────────────────────────────

    def _ensure_system_backup_card(self) -> None:
        """Auto-provision the SYSTEM_BACKUP task on first backup activation (MI-1 respected)."""
        card_id = _meta_get(self._db_path, "system_backup_card_id")
        if card_id:
            with sqlite3.connect(self._db_path) as conn:
                try:
                    row = conn.execute("SELECT id FROM tasks WHERE id = ?", (card_id,)).fetchone()
                    if row:
                        return
                except Exception:
                    pass  # tasks table may not exist yet; will be created by Store

        from .spine import Spine
        from .storage import Store

        try:
            with Store(self._db_path) as store:
                sp = Spine(store)
                if sp.get_project(SYSTEM_PROJECT_ID) is None:
                    sp.create_project("SYSTEM", project_id=SYSTEM_PROJECT_ID)
                task = sp.create_task(
                    SYSTEM_PROJECT_ID,
                    SYSTEM_BACKUP_TASK_TITLE,
                    state="created",
                    tier=4,
                    acceptance_criteria="Automated backup health card — do not delete.",
                )
            _meta_set(self._db_path, "system_backup_card_id", task.id)
            log.info("drive_backup: provisioned SYSTEM_BACKUP card %s", task.id)
        except Exception as exc:
            log.error("drive_backup: failed to provision SYSTEM_BACKUP card: %s", exc)

    def _resolve_escalation_if_open(self) -> None:
        """Resolve the open backup escalation if one exists (MI-2 single write)."""
        esc_id = _meta_get(self._db_path, "open_escalation_id")
        if not esc_id:
            return
        from .spine import Spine
        from .storage import Store

        try:
            with Store(self._db_path) as store:
                Spine(store).resolve_escalation(
                    esc_id,
                    resolution="approve",
                    resolution_rationale=(
                        "Automated: backup resumed successfully after consecutive failures."
                    ),
                    actor="operator",
                )
            _meta_set(self._db_path, "open_escalation_id", "")
            log.info("drive_backup: resolved backup escalation %s", esc_id)
        except Exception as exc:
            log.error("drive_backup: failed to resolve escalation %s: %s", esc_id, exc)

    def _maybe_mint_escalation(self) -> None:
        """Mint ONE Escalation against SYSTEM_BACKUP card if none open (single-open invariant)."""
        existing = _meta_get(self._db_path, "open_escalation_id")
        if existing:
            return  # single-open invariant

        card_id = _meta_get(self._db_path, "system_backup_card_id")
        if not card_id:
            log.error("drive_backup: cannot mint escalation — SYSTEM_BACKUP card not provisioned")
            return

        from .spine import Spine
        from .storage import Store

        try:
            with Store(self._db_path) as store:
                esc = Spine(store).create_escalation(
                    card_id,
                    reason=(
                        f"Backup failure: {self._consecutive_failures} consecutive push failures "
                        f"or dirty-age > {DIRTY_AGE_THRESHOLD_SECS // 60} min. "
                        "Manual intervention may be required."
                    ),
                )
            _meta_set(self._db_path, "open_escalation_id", esc.id)
            log.error(
                "drive_backup: ESCALATION minted (id=%s, consecutive_failures=%d)",
                esc.id, self._consecutive_failures,
            )
        except Exception as exc:
            log.error("drive_backup: failed to mint escalation: %s", exc)
