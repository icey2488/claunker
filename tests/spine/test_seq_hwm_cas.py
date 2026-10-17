"""Regression + concurrency tests for the persisted seq high-water mark and the
write-time compare-and-swap (fix/spine-seq-hwm-cas, 2026-10-16).

These pin the fix for the ABA hazard proved by the read-only repro job
``claunker.spine-aba-verify-20261016-syjhcgcm``: every writer opens a FRESH
``Store`` per call, the in-memory seq used to reset to 0 on each open, and the
content hash carried no per-write field, so a content revert A→B→A re-minted the
IDENTICAL token and a stale write carrying the earlier token was accepted.

Two layers are exercised here:
  * the PERSISTED seq HWM (``meta.seq_hwm``) makes seq monotonic across opens and
    across processes, so a reverted content never reproduces an earlier token; and
  * the write-time CAS (``UPDATE ... WHERE json_extract(data,'$.version')=expect``)
    rejects a lost update between two writers that read the same version.

Run (pytest):
    .venv/Scripts/python -m pytest tests/spine/test_seq_hwm_cas.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

# Make ``spine`` importable when run directly (mirrors test_spine_data_core.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from spine import ConflictError, Spine, Store  # noqa: E402
from spine.storage import META_TABLE, SEQ_HWM_KEY, _SEQ_MAX  # noqa: E402

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _assert_raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {fn} to raise {exc.__name__}")


def _seq(token: str) -> int:
    """The seq prefix of a ``{seq}:{hash}`` token (producer-side parse only)."""
    return int(token.split(":", 1)[0])


def _hash(token: str) -> str:
    return token.split(":", 1)[1]


def _seed_task(db_path, *, state="tiered"):
    """One project + one task in a fresh file-backed DB; returns the task id."""
    with Store(str(db_path)) as store:
        sp = Spine(store)
        sp.create_project("P", project_id="p")
        sp.create_task("p", "T", task_id="t", state=state,
                       created_at="2026-10-16T00:00:00+00:00")
    return "t"


# ── 1. ABA regression: fresh Store per request, A→B→A ─────────────────────────
def test_aba_revert_mints_distinct_token_and_rejects_stale_write(tmp_path):
    """The exact A→B→A scenario, each mutation through its OWN fresh Store (as the
    server does with ``with Store(config.db_path)``). The content hash for state A
    is identical at step 1 and step 3 — but the persisted HWM makes the FULL tokens
    differ, and a stale writer still holding T1 is rejected."""
    db = tmp_path / "aba.db"
    _seed_task(db)

    def current():
        with Store(str(db)) as s:
            return Spine(s).get_task("t").version

    # A → B → A, each in a fresh Store/connection.
    with Store(str(db)) as s:
        t1 = Spine(s).set_state("t", "tiered", expected_version=current()).version
    with Store(str(db)) as s:
        _t2 = Spine(s).set_state("t", "dispatched", expected_version=current()).version
    with Store(str(db)) as s:
        t3 = Spine(s).set_state("t", "tiered", expected_version=current()).version

    # Genuine ABA: state A's content hash is identical at step 1 and step 3 ...
    assert _hash(t1) == _hash(t3), "precondition: step 1 and 3 are the SAME content"
    # ... yet the tokens differ, because the persisted seq strictly advanced.
    assert t1 != t3
    assert _seq(t3) > _seq(t1)

    # A stale writer that still holds T1 is rejected (current token is now T3).
    with Store(str(db)) as s:
        _assert_raises(
            lambda: Spine(s).set_state("t", "judged", expected_version=t1),
            ConflictError,
        )


# ── 2. CAS: concurrent writers racing on one entity with one expected_version ──
def test_concurrent_same_expected_version_exactly_one_wins(tmp_path):
    """Two threads, each with its own Store/connection, race ``set_state`` with the
    SAME ``expected_version``. BEGIN IMMEDIATE serializes them; the loser's CAS finds
    the token already moved and raises ConflictError. Exactly one write survives."""
    db = tmp_path / "race.db"
    _seed_task(db)
    with Store(str(db)) as s:
        expected = Spine(s).get_task("t").version

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def worker(name, target_state):
        with Store(str(db)) as s:
            sp = Spine(s)
            barrier.wait()  # maximise the overlap
            try:
                results[name] = sp.set_state("t", target_state, expected_version=expected)
            except ConflictError as exc:
                results[name] = exc

    threads = [
        threading.Thread(target=worker, args=("a", "dispatched")),
        threading.Thread(target=worker, args=("b", "judged")),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    wins = [r for r in results.values() if not isinstance(r, ConflictError)]
    losses = [r for r in results.values() if isinstance(r, ConflictError)]
    assert len(wins) == 1, f"expected exactly one winner, got {results}"
    assert len(losses) == 1, f"expected exactly one ConflictError, got {results}"

    # The DB reflects the winner's single write; no lost update, no double-apply.
    with Store(str(db)) as s:
        final = Spine(s).get_task("t")
    assert final.version == wins[0].version
    assert final.state in ("dispatched", "judged")


# ── 3. Seeding ────────────────────────────────────────────────────────────────
def test_seed_from_legacy_tokens_sets_hwm_to_max_and_next_mint_is_max_plus_one(tmp_path):
    """A DB whose tokens all carry low seqs (like production, 97% at seq=1) seeds the
    HWM to the MAX existing seq, so the next mint is max+1 — never a repeat of an
    existing seq."""
    db = tmp_path / "legacy.db"
    _seed_task(db)
    # Advance a couple of times so the max existing seq is > 1, then DROP the meta
    # table to simulate a DB that predates the persisted HWM entirely.
    with Store(str(db)) as s:
        sp = Spine(s)
        sp.set_state("t", "dispatched", expected_version=sp.get_task("t").version)
        last = sp.set_state("t", "judged", expected_version=sp.get_task("t").version)
    max_existing = _seq(last.version)

    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute(f"DROP TABLE {META_TABLE}")
    conn.commit()
    conn.close()

    # Re-open: __init__ recreates meta and RE-SEEDS from the max token prefix.
    with Store(str(db)) as s:
        assert s.seq == max_existing  # last-used seq restored, not reset to 0
        row = s._conn.execute(
            f"SELECT value FROM {META_TABLE} WHERE key = ?", (SEQ_HWM_KEY,)
        ).fetchone()
        assert int(row[0]) == max_existing
        nxt = Spine(s).set_state("t", "delivered", expected_version=s.tasks.get("t").version)
    assert _seq(nxt.version) == max_existing + 1


def test_missing_meta_table_migrates_without_error(tmp_path):
    """Opening a DB with no meta table (legacy) must migrate silently — create the
    table, seed it, and serve writes."""
    db = tmp_path / "nometa.db"
    _seed_task(db)
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute(f"DROP TABLE {META_TABLE}")
    conn.commit()
    conn.close()
    # No exception, and the counter is usable.
    with Store(str(db)) as s:
        before = s.tasks.get("t").version
        after = Spine(s).set_state("t", "dispatched", expected_version=before)
    assert _seq(after.version) > _seq(before)


def test_huge_token_prefix_is_capped_at_seq_max(tmp_path):
    """A token with an absurd seq prefix seeds the HWM no higher than the SQLite
    INTEGER ceiling (so the value stays representable)."""
    db = tmp_path / "huge.db"
    _seed_task(db)
    import sqlite3
    conn = sqlite3.connect(str(db))
    # Plant a task blob with a wildly out-of-range seq prefix, then drop meta so the
    # next open re-seeds from the stored tokens.
    row = conn.execute("SELECT data FROM tasks WHERE id = 't'").fetchone()
    blob = json.loads(row[0])
    blob["version"] = f"{10 ** 40}:deadbeefdeadbeef"  # far beyond 2**63-1
    conn.execute("UPDATE tasks SET data = ? WHERE id = 't'", (json.dumps(blob),))
    conn.execute(f"DROP TABLE {META_TABLE}")
    conn.commit()
    conn.close()

    with Store(str(db)) as s:
        assert s.seq == _SEQ_MAX  # capped, not the 10**40 prefix
        row = s._conn.execute(
            f"SELECT value FROM {META_TABLE} WHERE key = ?", (SEQ_HWM_KEY,)
        ).fetchone()
        assert int(row[0]) == _SEQ_MAX


# ── 4. load() backstop ────────────────────────────────────────────────────────
def test_load_backstop_advances_persisted_hwm(tmp_path):
    """``Store.load`` recomputes seq = max(entity seq) and must ALSO push the
    persisted HWM forward, so a restore/merge carrying tokens minted higher than our
    counter can never let the next mint collide with a token already present."""
    db = tmp_path / "load.db"
    _seed_task(db)
    with Store(str(db)) as s:
        # A dumped envelope whose token prefix is far above the current HWM.
        blob = s.dump()
        blob["tasks"][0]["version"] = "9000:abcdef0123456789"
        s.load(blob)
        assert s.seq == 9000
        row = s._conn.execute(
            f"SELECT value FROM {META_TABLE} WHERE key = ?", (SEQ_HWM_KEY,)
        ).fetchone()
        assert int(row[0]) == 9000
        # The next mint continues above the loaded high-water mark.
        nxt = Spine(s).set_state("t", "dispatched", expected_version=s.tasks.get("t").version)
    assert _seq(nxt.version) == 9001


def test_load_does_not_regress_hwm_below_persisted(tmp_path):
    """A load carrying LOWER tokens must never drag the persisted HWM backwards."""
    db = tmp_path / "noregress.db"
    _seed_task(db)
    with Store(str(db)) as s:
        for _ in range(3):
            Spine(s).set_state("t", "dispatched",
                               expected_version=s.tasks.get("t").version)
            Spine(s).set_state("t", "tiered",
                               expected_version=s.tasks.get("t").version)
        high = s.seq
        low_blob = s.dump()
        low_blob["tasks"][0]["version"] = "1:aaaaaaaaaaaaaaaa"
        s.load(low_blob)
        assert s.seq == high  # unchanged — never regressed
        row = s._conn.execute(
            f"SELECT value FROM {META_TABLE} WHERE key = ?", (SEQ_HWM_KEY,)
        ).fetchone()
        assert int(row[0]) == high


# ── 5. Cross-process monotonicity + stale rejection ───────────────────────────
_WORKER = r"""
import json, os, sys
sys.path.insert(0, os.environ["SPINE_REPO_ROOT"])
from spine import ConflictError, Spine, Store

db = sys.argv[1]
out = sys.argv[2]
iters = int(sys.argv[3])
stale_token = sys.argv[4]

minted = []
stale_rejected = 0
stale_total = 0
for i in range(iters):
    # A unique create bumps the shared HWM; contends with the other process.
    with Store(db) as s:
        tid = f"{os.getpid()}-{i}"
        t = Spine(s).create_task("p", "x", task_id=tid,
                                 created_at="2026-10-16T00:00:00+00:00")
        minted.append(t.version)
    # A deliberately-stale guarded write on the shared task; must be rejected.
    stale_total += 1
    with Store(db) as s:
        try:
            Spine(s).set_state("t", "dispatched", expected_version=stale_token)
        except ConflictError:
            stale_rejected += 1

with open(out, "w") as f:
    json.dump({"minted": minted, "stale_rejected": stale_rejected,
               "stale_total": stale_total}, f)
"""


def test_cross_process_tokens_strictly_increase_and_stale_rejected(tmp_path):
    """Two separate OS processes write to the same temp DB. Across BOTH processes the
    minted seqs are strictly increasing with no duplicates (the HWM read-modify-write
    is atomic across processes), and every interleaved stale-version write is
    rejected."""
    db = tmp_path / "xproc.db"
    _seed_task(db)
    # Advance the shared task once so a token captured BEFORE is genuinely stale.
    with Store(str(db)) as s:
        stale = s.tasks.get("t").version
        Spine(s).set_state("t", "dispatched", expected_version=stale)

    worker_py = tmp_path / "worker.py"
    worker_py.write_text(_WORKER, encoding="utf-8")
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    env = dict(os.environ, SPINE_REPO_ROOT=_REPO_ROOT)
    iters = 15

    procs = [
        subprocess.Popen([sys.executable, str(worker_py), str(db), str(out_a),
                          str(iters), stale], env=env),
        subprocess.Popen([sys.executable, str(worker_py), str(db), str(out_b),
                          str(iters), stale], env=env),
    ]
    rcs = [p.wait(timeout=120) for p in procs]
    assert rcs == [0, 0], f"worker(s) failed: {rcs}"

    a = json.loads(out_a.read_text())
    b = json.loads(out_b.read_text())

    seqs = sorted(_seq(v) for v in a["minted"] + b["minted"])
    assert len(seqs) == 2 * iters
    assert len(set(seqs)) == len(seqs), "duplicate seq minted across processes"
    assert seqs == sorted(set(seqs)), "seqs not strictly increasing / unique"

    # Every stale-version write, interleaved across both processes, was rejected.
    assert a["stale_rejected"] == a["stale_total"]
    assert b["stale_rejected"] == b["stale_total"]


if __name__ == "__main__":  # pure-python fallback (no pytest)
    import tempfile
    from pathlib import Path

    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            with tempfile.TemporaryDirectory() as d:
                fn(Path(d))
            print(f"ok  {name}")
    print("all passed")
