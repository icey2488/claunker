"""spine/drive_common.py — shared Google Drive plumbing for the Spine's two
Drive channels: ``drive_backup.py`` (immutable-snapshot backup, untouched
behavior) and ``drive_sync.py`` (live cross-device merge). Both are DORMANT
until a service-account key is configured; this module holds only the
generic auth/folder/blob mechanics neither channel should duplicate.

Extracted from ``drive_backup.py`` verbatim in behavior — every function here
is the same code that used to live in that module, generalized only where a
folder/blob NAME needed to become a parameter so ``drive_sync.py`` can reuse it
against its own folder (``claunker-spine-sync/``) without touching
``drive_backup.py``'s folder (``claunker-spine-backups/``) or its behavior.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def build_drive_service(key_path: str):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=[DRIVE_SCOPE]
    )
    return build("drive", "v3", credentials=creds)


def find_or_create_folder(drive, folder_name: str) -> str:
    """Find-or-create a Drive folder by name, at 'My Drive' root.

    CAVEAT (SA-ownership): a folder created by a service account is OWNED by
    that service account, not by a human Drive user. Unless the folder is
    shared to (or the SA key is scoped under) the intended human account, only
    the SA credential itself can see/manage it from the Drive UI. See
    docs/spine-drive-sync.md for the sharing step that must accompany minting
    a fresh SA key.
    """
    results = (
        drive.files()
        .list(
            q=(
                f"name='{folder_name}' and "
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
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = drive.files().create(body=meta, fields="id").execute()
    return folder["id"]


def find_blob_id(drive, folder_id: str, blob_name: str) -> Optional[str]:
    """Find a blob by exact name within a folder. Raises RuntimeError on
    split-brain (2+ copies) — the caller supplies the HALT wording (each
    channel names its own blob/folder in the message)."""
    results = (
        drive.files()
        .list(
            q=(
                f"name='{blob_name}' and "
                f"'{folder_id}' in parents and trashed=false"
            ),
            fields="files(id)",
        )
        .execute()
    )
    files = results.get("files", [])
    if len(files) >= 2:
        raise RuntimeError(
            f"SPLIT-BRAIN HALT — found {len(files)} copies of {blob_name!r} "
            f"in folder {folder_id!r}; manual deduplication required before "
            "sync/backup can resume"
        )
    return files[0]["id"] if files else None


def upload_blob(drive, folder_id: str, name: str, blob: bytes, existing_id: Optional[str] = None) -> str:
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


def download_blob(drive, file_id: str) -> bytes:
    import io
    from googleapiclient.http import MediaIoBaseDownload

    request = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


def prune_old_snapshots(
    drive,
    folder_id: str,
    *,
    active_blob_name: str,
    snapshot_prefix: str,
    snapshot_suffix: str,
    retention_days: int,
    log=None,
) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
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
        if not (name.startswith(snapshot_prefix) and name.endswith(snapshot_suffix) and name != active_blob_name):
            continue
        date_part = name[len(snapshot_prefix): -len(snapshot_suffix)]
        if len(date_part) == 10 and date_part < cutoff:
            try:
                drive.files().delete(fileId=f["id"]).execute()
                if log is not None:
                    log.info("drive: pruned old snapshot %s", name)
            except Exception as exc:
                if log is not None:
                    log.warning("drive: failed to prune %s: %s", name, exc)
