"""spine/merge_bridge.py — Python bridge to the vendored Node merge core.

Invokes ``spine/vendor/merge_runner.js`` (which calls the vendored, verbatim
``sync-merge.js``) as a subprocess: two JSON blobs go in on stdin, a merged blob
plus a conflict report come back on stdout. Node is never treated as optional
silent infrastructure — if it cannot be found or the call fails, this module
raises loudly (fail-closed: no node = no sync, never a silent skip).

Node path resolution happens ONCE, at import time (module-level constant), via
``where`` on Windows / ``which`` elsewhere, overridable with the
``CLAUNKER_NODE_PATH`` env var (set it to the absolute node executable path when
PATH is not populated the same way for a dispatched job as for an interactive
shell). If neither resolves, ``NODE_PATH`` is ``None`` and every call to
``run_merge`` raises ``MergeBridgeError`` immediately, with a clear message —
never silently skipping the sync cycle.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

MERGE_RUNNER_PATH = os.path.join(os.path.dirname(__file__), "vendor", "merge_runner.js")
DEFAULT_TIMEOUT_SECS = 30


class MergeBridgeError(RuntimeError):
    """Raised on any failure to invoke or complete the Node merge core."""


def _resolve_node_path() -> Optional[str]:
    """Resolve the absolute node executable path once, at import time.

    ``CLAUNKER_NODE_PATH`` (if set) wins outright — this is the escape hatch for
    a dispatched job whose PATH does not carry the same entries as an
    interactive shell. Otherwise fall back to ``shutil.which("node")``, which
    uses ``where`` on Windows / ``which`` on POSIX.
    """
    override = os.environ.get("CLAUNKER_NODE_PATH")
    if override:
        return override
    return shutil.which("node")


# Resolved once at import/config time, per the dispatched-job PATH lesson: never
# re-resolve per call (a dispatched job's PATH can be sparser than an
# interactive shell's, and re-resolving mid-run risks flip-flopping between a
# found and not-found node depending on when in the process lifecycle we ask).
NODE_PATH: Optional[str] = _resolve_node_path()


def run_merge(
    blob_a: Dict[str, Any],
    blob_b: Dict[str, Any],
    *,
    node_path: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Merge two blobs via the vendored sync-merge.js core, over a node subprocess.

    Returns ``(merged_blob, conflicts)``. Raises ``MergeBridgeError`` if node is
    absent, the subprocess times out, exits non-zero, or returns malformed JSON —
    fail-closed, never a silent skip.

    ``node_path`` overrides the module-level ``NODE_PATH`` resolution for a
    single call (the injectable-callable seam tests use instead of mocking
    ``subprocess`` directly; see ``spine/drive_sync.py``'s ``merge_fn`` param for
    the higher-level injection point).
    """
    resolved = node_path if node_path is not None else NODE_PATH
    if not resolved:
        raise MergeBridgeError(
            "spine.merge_bridge: node executable not found (checked CLAUNKER_NODE_PATH "
            "env var and PATH via `where`/`which`). Sync cannot run without node — "
            "set CLAUNKER_NODE_PATH to the absolute node executable path."
        )
    if not os.path.exists(MERGE_RUNNER_PATH):
        raise MergeBridgeError(f"spine.merge_bridge: merge runner not found at {MERGE_RUNNER_PATH}")

    payload = json.dumps({"a": blob_a, "b": blob_b})

    try:
        proc = subprocess.run(
            [resolved, MERGE_RUNNER_PATH],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MergeBridgeError(f"spine.merge_bridge: node merge timed out after {timeout}s") from exc
    except OSError as exc:
        raise MergeBridgeError(f"spine.merge_bridge: failed to launch node ({resolved}): {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stdout.strip() or proc.stderr.strip() or f"exit code {proc.returncode}"
        raise MergeBridgeError(f"spine.merge_bridge: merge_runner.js failed: {detail}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MergeBridgeError(
            f"spine.merge_bridge: merge_runner.js returned non-JSON stdout: {proc.stdout[:500]!r}"
        ) from exc

    if "error" in result:
        raise MergeBridgeError(f"spine.merge_bridge: merge_runner.js reported an error: {result['error']}")
    if "merged" not in result:
        raise MergeBridgeError(f"spine.merge_bridge: merge_runner.js result missing 'merged': {result!r}")

    return result["merged"], result.get("conflicts", [])


# Type alias for the injectable merge callable used by DriveSync (see
# spine/drive_sync.py) — same signature as run_merge, so a test double can be
# substituted with zero call-site changes (the drive_backup.py mocking pattern).
MergeCallable = Callable[[Dict[str, Any], Dict[str, Any]], Tuple[Dict[str, Any], List[Dict[str, Any]]]]
