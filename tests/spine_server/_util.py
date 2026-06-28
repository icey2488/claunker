"""Shared helpers for the spine_server tests: temp file-backed spine dbs + seeding.

Mirrors the spine suite's self-contained style (sys.path shim so the file also runs
directly). Not a test module (no ``test_`` prefix) — pytest won't collect it.
"""

import os
import shutil
import sys
import tempfile

# Make ``spine`` / ``spine_server`` importable when a test is run directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from spine import Spine, Store  # noqa: E402
from spine.entity import State  # noqa: E402

# A generous ceiling for tests that should never trip payload_too_large.
BIG = 64 * 1024 * 1024


def make_temp_db():
    """A fresh temp dir + the spine.db path inside it. Returns (dir, db_path)."""
    directory = tempfile.mkdtemp(prefix="spine_server_test_")
    return directory, os.path.join(directory, "spine.db")


def cleanup(directory):
    """Remove the temp dir (ignore residual WAL lock errors on Windows)."""
    shutil.rmtree(directory, ignore_errors=True)


def seed(path, specs):
    """Seed a file-backed spine and close it (so a fresh reader sees committed
    state). ``specs`` is an iterable of ``{title, state?, tier?, deleted?}``.
    Returns the created task ids in order."""
    spine = Spine(Store(path))
    try:
        project = spine.create_project("p")
        ids = []
        for spec in specs:
            task = spine.create_task(
                project.id,
                spec["title"],
                state=spec.get("state", State.CREATED),
                tier=spec.get("tier"),
            )
            if spec.get("deleted"):
                spine.soft_delete_task(task.id)
            ids.append(task.id)
        return ids
    finally:
        spine.store.close()


def assert_raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected call to raise {exc.__name__}")
