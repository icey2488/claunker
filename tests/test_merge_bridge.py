"""Tests for spine/merge_bridge.py — the Python->Node subprocess bridge to the
vendored sync-merge.js core.

Unit tests mock/patch node resolution and subprocess; the integration test
shells to a REAL node + the vendored, unedited sync-merge.js and is skipped
cleanly (not failed) if node is unavailable in the test environment.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spine.merge_bridge import MergeBridgeError, MERGE_RUNNER_PATH, run_merge


HAS_NODE = shutil.which("node") is not None


# ── fail-closed: node absent ──────────────────────────────────────────────────

def test_run_merge_hard_fails_when_node_path_is_none():
    """No resolvable node path -> MergeBridgeError, not a silent skip."""
    with pytest.raises(MergeBridgeError, match="node executable not found"):
        run_merge({"tasks": []}, {"tasks": []}, node_path="")


def test_run_merge_hard_fails_when_node_path_is_bogus():
    """A node_path override that does not exist -> a clear MergeBridgeError, not
    a silent skip and not an uncaught OSError."""
    with pytest.raises(MergeBridgeError, match="failed to launch node"):
        run_merge({"tasks": []}, {"tasks": []}, node_path=os.path.join(os.path.dirname(__file__), "no-such-node-binary"))


def test_run_merge_hard_fails_on_timeout(monkeypatch):
    """A node call that exceeds the timeout raises MergeBridgeError, not a
    hung/silent process."""
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="node", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(MergeBridgeError, match="timed out"):
        run_merge({"tasks": []}, {"tasks": []}, node_path="node")


def test_run_merge_hard_fails_on_missing_runner(monkeypatch):
    """If the vendored runner script is missing, fail closed with a clear error."""
    monkeypatch.setattr("spine.merge_bridge.MERGE_RUNNER_PATH", "/no/such/runner.js")
    with pytest.raises(MergeBridgeError, match="merge runner not found"):
        run_merge({"tasks": []}, {"tasks": []}, node_path="node")


# ── real node + vendored sync-merge.js integration ────────────────────────────

@pytest.mark.skipif(not HAS_NODE, reason="node is not available in this test environment")
def test_run_merge_real_node_convergence():
    """Merge two genuinely divergent blobs through the REAL vendored core and
    verify: a base + conflict copy pair, both edits preserved, and the
    conflict is correctly surfaced in the returned conflict report."""
    a = {
        "schema_version": 1,
        "seq": 1,
        "tasks": [{"id": "X", "version": "1:aaaa", "title": "edit-A", "deleted_at": None}],
    }
    b = {
        "schema_version": 1,
        "seq": 1,
        "tasks": [{"id": "X", "version": "2:bbbb", "title": "edit-B", "deleted_at": None}],
    }

    merged, conflicts = run_merge(a, b)

    assert len(merged["tasks"]) == 2
    titles = sorted(t["title"] for t in merged["tasks"])
    assert titles == ["edit-A", "edit-B"], "no data lost across the merge"

    assert len(conflicts) == 1
    assert conflicts[0]["collection"] == "tasks"
    assert conflicts[0]["of"] == "X"
    assert conflicts[0]["id"].startswith("X.conflict.")


@pytest.mark.skipif(not HAS_NODE, reason="node is not available in this test environment")
def test_run_merge_real_node_is_commutative():
    """merge(a, b) and merge(b, a) converge to the same set of tasks (ids +
    titles) — the CRDT property the vendored core proves in its own suite."""
    a = {"schema_version": 1, "seq": 1, "tasks": [{"id": "X", "version": "1:aaaa", "title": "AA", "deleted_at": None}]}
    b = {"schema_version": 1, "seq": 1, "tasks": [{"id": "X", "version": "2:bbbb", "title": "BB", "deleted_at": None}]}

    m_ab, _ = run_merge(a, b)
    m_ba, _ = run_merge(b, a)

    key = lambda blob: sorted((t["id"], t["title"]) for t in blob["tasks"])
    assert key(m_ab) == key(m_ba)


@pytest.mark.skipif(not HAS_NODE, reason="node is not available in this test environment")
def test_run_merge_real_node_no_conflict_when_identical():
    """Merging a blob with itself produces no conflict copies (idempotency)."""
    a = {"schema_version": 1, "seq": 1, "tasks": [{"id": "X", "version": "1:aaaa", "title": "same", "deleted_at": None}]}

    merged, conflicts = run_merge(a, a)

    assert len(merged["tasks"]) == 1
    assert conflicts == []


def test_merge_runner_path_exists():
    """The vendored runner this bridge shells out to actually exists on disk."""
    assert os.path.exists(MERGE_RUNNER_PATH)
