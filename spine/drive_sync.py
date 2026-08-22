"""spine/drive_sync.py — Drive-durable cross-device MERGE sync for the Claunker
Spine (card 3ae179b1). A second, independent Drive channel alongside
``drive_backup.py``'s immutable-snapshot backup — NOT a replacement for it and
NOT sharing its folder or blob name:

    drive_backup.py : claunker-spine-backups/claunker_spine_v1.json.gz          (immutable snapshot channel, untouched)
    drive_sync.py   : claunker-spine-sync/claunker_spine_sync_v1.json.gz        (live mutable merge channel, this module)

DORMANT until ``CLAUNKER_SPINE_SA_KEY`` env var points to an existing SA key
file — the SAME env var ``drive_backup.py`` uses (same credential, different
folder/blob). When dormant: one INFO log line at startup, all public methods
are no-ops. Nothing here touches live Drive until Erick mints that key.

SA-OWNERSHIP CAVEAT: a folder the service account creates is owned by the SA,
not by a human Google account. Unless the sync folder is shared to (or created
under) Erick's account, only the SA credential can see/manage it from the
Drive UI. This must be handled as part of minting the fresh key — see
docs/spine-drive-sync.md.

Cycle:
  pull  — fetch claunker_spine_sync_v1.json.gz from claunker-spine-sync/.
          Absent -> bootstrap (nothing to merge against; local dump() becomes
          the seed and is pushed as-is).
  merge — local Store.dump() merged against the pulled remote blob via the
          Node bridge (spine/merge_bridge.py -> vendored sync-merge.js).
          The merge callable is INJECTABLE (``merge_fn``) for tests, mirroring
          drive_backup.py's ``drive_service`` injection pattern.
  load  — Store.load() the merged blob back into the local ledger. seq is
          recomputed from the merged entities on open (storage doc §5), never
          taken verbatim from the blob's stored seq field, so a merge can never
          regress the monotonic clock.
  push  — gzip + preflight (structural validation, same discipline as
          drive_backup.py's _preflight) + sha256, then upload.

Split-brain guard: 2+ blobs named ``SYNC_BLOB_NAME`` in the sync folder halts
the cycle and raises — the same pattern as drive_backup.py's active-blob guard
(spine/drive_common.py's ``find_blob_id`` raises RuntimeError on 2+ matches).

Debounce / lifecycle mirrors drive_backup.py exactly: ``mark_dirty()`` schedules
a debounced push, ``startup_flush()`` runs a full pull-merge-push cycle once at
process start, ``shutdown_flush()`` is a best-effort final push.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import drive_common as _dc
from .drive_backup import _default_key_path  # same SA key resolution as drive_backup
from .merge_bridge import MergeBridgeError, MergeCallable, run_merge

log = logging.getLogger(__name__)

DRIVE_FOLDER_NAME = "claunker-spine-sync"
SYNC_BLOB_NAME = "claunker_spine_sync_v1.json.gz"

# Root keys that MUST be present and non-null in a sync blob envelope (mirrors
# drive_backup.py's MANDATORY_KEYS discipline, over the spine's dump() shape).
MANDATORY_KEYS = ("schema_version", "seq", "projects", "tasks", "artifacts", "escalations")

DEBOUNCE_SECS = 30


def _preflight(blob: bytes) -> Optional[Dict[str, Any]]:
    """Validate a gzipped sync blob before upload. Returns
    {status, bytes, sha256} on success, None on failure (logged)."""
    try:
        raw = gzip.decompress(blob)
        obj = json.loads(raw)
    except Exception as exc:
        log.error("drive_sync: pre_flight FAIL — cannot decompress/parse: %s", exc)
        return None

    for key in MANDATORY_KEYS:
        if key not in obj or obj[key] is None:
            log.error("drive_sync: pre_flight FAIL — missing mandatory key %r", key)
            return None

    sha = hashlib.sha256(raw).hexdigest()
    result = {"status": "pre_flight_ok", "bytes": len(blob), "sha256": sha}
    log.info("drive_sync: %s", json.dumps(result))
    return result


class DriveSync:
    """Drive pull-merge-push sync manager for the Claunker Spine.

    Pass ``drive_service`` and/or ``merge_fn`` in tests to inject mocks; leave
    both None in production. Dormant when the SA key file is absent or Drive
    initialization fails — identical dormancy contract to ``DriveBackup``.
    """

    def __init__(
        self,
        db_path: str,
        *,
        drive_service=None,
        merge_fn: Optional[MergeCallable] = None,
    ) -> None:
        self._db_path = db_path
        self._dormant = False
        self._merge_fn: MergeCallable = merge_fn if merge_fn is not None else run_merge

        if drive_service is None:
            import os

            key_path = os.environ.get("CLAUNKER_SPINE_SA_KEY") or _default_key_path(db_path)
            if not os.path.exists(key_path):
                log.info("drive_sync: DORMANT (SA key not found at %s)", key_path)
                self._dormant = True
                return
            try:
                drive_service = _dc.build_drive_service(key_path)
            except Exception as exc:
                log.error("drive_sync: DORMANT (failed to init Drive client: %s)", exc)
                self._dormant = True
                return

        self._drive = drive_service
        self._dirty = False
        self._lock = threading.Lock()
        self._debounce_timer: Optional[threading.Timer] = None

        try:
            self._folder_id = _dc.find_or_create_folder(drive_service, DRIVE_FOLDER_NAME)
        except Exception as exc:
            log.error("drive_sync: DORMANT (failed to init sync folder: %s)", exc)
            self._dormant = True
            return

        log.info("drive_sync: ACTIVE (folder=%s)", self._folder_id)

    @property
    def dormant(self) -> bool:
        return self._dormant

    # ── public interface (mirrors DriveBackup's mark_dirty/startup_flush/shutdown_flush) ──

    def mark_dirty(self) -> None:
        """Call after each successful mutation. Schedules a debounced sync cycle.
        No-op if dormant."""
        if self._dormant:
            return
        with self._lock:
            self._dirty = True
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            t = threading.Timer(DEBOUNCE_SECS, self._cycle_worker)
            t.daemon = True
            t.start()
            self._debounce_timer = t

    def startup_flush(self) -> None:
        """Run one full pull-merge-push cycle at process start. No-op if dormant."""
        if self._dormant:
            return
        log.info("drive_sync: startup pull-merge-push cycle")
        self.run_cycle()

    def shutdown_flush(self) -> None:
        """Best-effort final cycle on clean shutdown. No-op if dormant."""
        if self._dormant:
            return
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
            should_run = self._dirty
        if should_run:
            log.info("drive_sync: shutdown_flush — best-effort cycle")
            self.run_cycle()

    # ── the pull-merge-load-push cycle ────────────────────────────────────────

    def run_cycle(self) -> bool:
        """Execute one full pull -> merge -> load -> push cycle. Returns True on
        success (including the no-op case where nothing changed). Raises
        RuntimeError on split-brain (2+ sync blobs) — a HALT, not a swallowed
        failure. Raises MergeBridgeError if node/the merge core is unavailable
        (fail-closed: no node = no sync, never a silent skip)."""
        from .storage import Store

        blob_id = _dc.find_blob_id(self._drive, self._folder_id, SYNC_BLOB_NAME)

        with Store(self._db_path) as store:
            local_blob = store.dump()

            if blob_id is None:
                # Bootstrap: nothing remote yet — local becomes the seed.
                log.info("drive_sync: bootstrap — no remote sync blob yet, seeding from local")
                merged = local_blob
                conflicts: List[Dict[str, Any]] = []
            else:
                remote_raw = _dc.download_blob(self._drive, blob_id)
                try:
                    remote_blob = json.loads(gzip.decompress(remote_raw))
                except Exception as exc:
                    raise RuntimeError(f"drive_sync: remote sync blob corrupt: {exc}") from exc

                merged, conflicts = self._merge_fn(local_blob, remote_blob)
                if conflicts:
                    log.info("drive_sync: merge produced %d conflict copy(ies)", len(conflicts))

            # store.load() recomputes seq = max(entity seq) + 1 from the merged
            # entities' own version tokens (storage doc §5) — the merged
            # blob's own top-level `seq` field (a plain sync-merge.js
            # pass-through, oblivious to the store's version-token scheme) is
            # never trusted verbatim.
            store.load(merged)

        raw = json.dumps(merged, separators=(",", ":")).encode("utf-8")
        push_blob = gzip.compress(raw)
        pf = _preflight(push_blob)
        if pf is None:
            log.error("drive_sync: push aborted — pre_flight failed")
            return False

        _dc.upload_blob(self._drive, self._folder_id, SYNC_BLOB_NAME, push_blob, blob_id)
        log.info("drive_sync: push OK (sha=%s, bytes=%d)", pf["sha256"], pf["bytes"])

        with self._lock:
            self._dirty = False

        return True

    def _cycle_worker(self) -> None:
        """Background debounce timer callback — runs in a daemon thread."""
        with self._lock:
            self._debounce_timer = None
        try:
            self.run_cycle()
        except Exception as exc:
            log.error("drive_sync: debounced cycle failed: %s", exc)
