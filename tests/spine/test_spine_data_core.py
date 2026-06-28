"""Tests for the Claunker Spine data core (locked v1 architecture).

Covers the new model's required surface — per-entity store round-trip across all
four tables, soft-delete omission (list_live + projection), opaque equality-only
versions, LexoRank ordering, the two Mutation Invariants (MI-1 no late children on
a tombstoned task; MI-2 resolve-escalation as a single-field write), the one-to-one
state→column projection, the unresolved-escalation→badge rule (and its absence for
resolved/tombstoned escalations), and the dump/load blob round-trip — adapting the
surface the old event-sourced suite covered.

Run (pytest):
    uv run --with pytest --python 3.11 python -m pytest tests/spine/ -q
Run (no pytest — pure-python fallback):
    uv run --python 3.11 python tests/spine/test_spine_data_core.py
"""

import os
import sys
import tempfile

# Make ``spine`` importable when this file is run directly (python tests/spine/x.py
# puts THIS dir on sys.path, not the repo root). Harmless under pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from spine import (  # noqa: E402
    ARTIFACT_KINDS,
    SCHEMA_VERSION,
    STATES,
    Artifact,
    ArtifactKind,
    Escalation,
    GATE_STATUS_COMMITTED,
    MAX_RANK_LENGTH,
    Project,
    Spine,
    State,
    Store,
    Task,
    append_rank,
    make_version,
    project,
    rank_between,
    rebalance,
    to_card,
)

# Fixed timestamps so created_at-derived content (and version hashes) are stable.
T = [f"2026-06-28T0{i}:00:00+00:00" for i in range(9)]

_CONTROL_DIFF = {"control_id": "net.egress", "old_value": "deny", "new_value": "allow", "reduces_control": True}


def _card_for(spine, task_id):
    """The single projected card for ``task_id`` (raises if absent/omitted)."""
    return next(c for c in spine.cards() if c["id"] == task_id)


def _assert_raises(fn, exc=Exception):
    raised = False
    try:
        fn()
    except exc:
        raised = True
    assert raised, f"expected {fn} to raise {exc.__name__}"


# ── per-entity store round-trip across all four tables ─────────────────────────
def test_per_entity_store_round_trip_all_four_tables():
    store = Store()
    p = Project(id="p1", name="Proj", created_at=T[0])
    t = Task(id="t1", project_id="p1", title="task", state=State.TIERED, tier=2,
             acceptance_criteria=["compiles", "tests pass"], order="i", created_at=T[1])
    a = Artifact(id="a1", task_id="t1", kind=ArtifactKind.DIFF, ref="patch://1", created_at=T[2])
    e = Escalation(id="e1", task_id="t1", reason="needs human", control_diff=_CONTROL_DIFF, created_at=T[3])

    store.projects.put(p)
    store.tasks.put(t)
    store.artifacts.put(a)
    store.escalations.put(e)

    # Each blob round-trips byte-for-byte through (id, data) — version stamped by put.
    assert store.projects.get("p1").to_dict() == p.to_dict()
    assert store.tasks.get("t1").to_dict() == t.to_dict()
    assert store.artifacts.get("a1").to_dict() == a.to_dict()
    assert store.escalations.get("e1").to_dict() == e.to_dict()

    # Semantic fields survived the JSON round-trip (incl. nested control_diff).
    assert store.tasks.get("t1").acceptance_criteria == ["compiles", "tests pass"]
    assert store.escalations.get("e1").control_diff == _CONTROL_DIFF

    # list_all sees each table's single row; a miss is None.
    for sub in (store.projects, store.tasks, store.artifacts, store.escalations):
        assert len(sub.list_all()) == 1
    assert store.projects.get("nope") is None


# ── soft-delete: omitted from list_live AND projection, retained in list_all ───
def test_soft_delete_omitted_from_list_live_and_projection():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])

    assert len(spine.store.tasks.list_live()) == 1
    assert len(spine.cards()) == 1

    spine.soft_delete_task(t.id)

    assert spine.store.tasks.list_live() == []          # gone from the live view
    assert len(spine.store.tasks.list_all()) == 1       # tombstone retained
    assert spine.store.tasks.list_all()[0].deleted_at is not None
    assert spine.cards() == []                          # and omitted from the board
    assert to_card(spine.store.tasks.get(t.id)) is None  # the lens omits a tombstone


# ── ordering: creation order reflected in LexoRank order + projection order ────
def test_creation_order_reflected_in_lexorank_projection_order():
    spine = Spine()
    p = spine.create_project("p")
    a = spine.create_task(p.id, "a", created_at=T[0])
    b = spine.create_task(p.id, "b", created_at=T[1])
    c = spine.create_task(p.id, "c", created_at=T[2])

    assert a.order < b.order < c.order, (a.order, b.order, c.order)
    assert [card["title"] for card in spine.cards()] == ["a", "b", "c"]


def test_rank_between_and_append_order_correctly():
    first = append_rank("")
    second = append_rank(first)
    assert first < second
    mid = rank_between(first, second)
    assert first < mid < second
    # append-at-end past the top of the alphabet still grows correctly
    assert rank_between("z", "") > "z"


def test_rebalance_is_sorted_compact_and_total():
    ids = [f"id{i}" for i in range(25)]
    mapping = rebalance(ids)
    ranks = [r for _, r in mapping]
    assert [i for i, _ in mapping] == ids               # order preserved, total
    assert ranks == sorted(ranks)                       # strictly orderable
    assert len(set(ranks)) == len(ranks)                # no collisions
    assert all(len(r) <= MAX_RANK_LENGTH for r in ranks)


# ── version: opaque, equality-only, changes on every put ───────────────────────
def test_version_is_opaque_string_and_changes_on_mutation():
    store = Store()
    t = Task(id="t1", project_id="p", title="t", created_at=T[0])
    store.tasks.put(t)
    v1 = t.version
    assert isinstance(v1, str) and ":" in v1            # opaque {seq}:{hash} string
    assert store.tasks.get("t1").version == v1          # stored == returned (put consistency)

    t.title = "t2"
    store.tasks.put(t)
    assert t.version != v1                               # changes on mutation
    assert isinstance(t.version, str)


def test_put_mints_a_fresh_token_every_put():
    """Every put bumps the monotonic seq, so even re-putting identical content
    yields a new token (the equality-only contract: a put is a change event)."""
    store = Store()
    t = Task(id="t1", project_id="p", title="t", created_at=T[0])
    store.tasks.put(t)
    first = t.version
    store.tasks.put(t)                                   # same content, new seq
    assert t.version != first


def test_make_version_is_deterministic_and_seq_sensitive():
    content = {"id": "x", "title": "t"}
    assert make_version(1, content) == make_version(1, content)   # deterministic
    assert make_version(1, content) != make_version(2, content)   # seq prefix moves it
    assert make_version(1, content) != make_version(1, {"id": "x", "title": "u"})


# ── projection: gate_status, state→column, tier→tag, null attribution ──────────
def test_every_projected_card_is_gate_status_committed_with_null_attribution():
    spine = Spine()
    p = spine.create_project("p")
    spine.create_task(p.id, "a", created_at=T[0])
    spine.create_task(p.id, "b", created_at=T[1])
    cards = spine.cards()
    assert cards
    for card in cards:
        assert card["gate_status"] == GATE_STATUS_COMMITTED
        # v1 entities carry no actor/update metadata → these project as null.
        assert card["created_by"] is None
        assert card["updated_by"] is None
        assert card["updated_at"] is None


def test_state_maps_one_to_one_to_column():
    spine = Spine()
    p = spine.create_project("p")
    for st in STATES:
        spine.create_task(p.id, st, state=st, created_at=T[0])
    column_by_title = {c["title"]: c["column_id"] for c in spine.cards()}
    # All six states map to a column of the same id (no collapsing).
    for st in STATES:
        assert column_by_title[st] == st
    # The two cases the spec calls out explicitly.
    assert column_by_title["judged"] == "judged"
    assert column_by_title["failed"] == "failed"


def test_tier_projects_to_a_tag():
    spine = Spine()
    p = spine.create_project("p")
    untiered = spine.create_task(p.id, "untiered", created_at=T[0])
    tiered = spine.create_task(p.id, "tiered", tier=4, created_at=T[1])
    assert _card_for(spine, untiered.id)["tags"] == []
    assert _card_for(spine, tiered.id)["tags"] == ["tier:4"]


# ── escalation → badge (orthogonal to the state column) ────────────────────────
def test_unresolved_escalation_badges_card_and_keeps_it_in_state_column():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", state=State.DISPATCHED, created_at=T[0])

    e = spine.create_escalation(t.id, "weakens egress control", control_diff=_CONTROL_DIFF, created_at=T[1])
    card = _card_for(spine, t.id)
    # Badge present and carrying the approval-queue fields…
    assert card["badge"] is not None
    assert card["badge"]["id"] == e.id
    assert card["badge"]["reason"] == "weakens egress control"
    assert card["badge"]["control_diff"]["reduces_control"] is True
    # …and the card STAYS in its state column (escalation is not a column).
    assert card["column_id"] == "dispatched"

    # Resolving the escalation clears the badge (column unchanged).
    spine.resolve_escalation(e.id)
    cleared = _card_for(spine, t.id)
    assert cleared["badge"] is None
    assert cleared["column_id"] == "dispatched"


def test_resolved_and_tombstoned_escalations_yield_no_badge():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", state=State.JUDGED, created_at=T[0])

    # A resolved escalation → no badge.
    resolved = spine.create_escalation(t.id, "r", created_at=T[1])
    spine.resolve_escalation(resolved.id, resolved_at=T[2])
    assert _card_for(spine, t.id)["badge"] is None

    # A live unresolved one badges it…
    live = spine.create_escalation(t.id, "live", created_at=T[3])
    assert _card_for(spine, t.id)["badge"]["id"] == live.id
    # …and tombstoning that escalation clears the badge too.
    spine.store.escalations.soft_delete(live.id)
    assert _card_for(spine, t.id)["badge"] is None


# ── MI-1: no late children on a tombstoned task (BOTH child kinds rejected) ────
def test_mi1_rejects_artifact_and_escalation_on_tombstoned_task():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])

    # While live, both child kinds are admitted.
    spine.create_artifact(t.id, ArtifactKind.FILE, "f://1")
    spine.create_escalation(t.id, "ok")

    spine.soft_delete_task(t.id)

    # Tombstoned → BOTH an Artifact and an Escalation are rejected (MI-1).
    _assert_raises(lambda: spine.create_artifact(t.id, ArtifactKind.DIFF, "f://2"), ValueError)
    _assert_raises(lambda: spine.create_escalation(t.id, "late"), ValueError)

    # Referential hygiene: an absent parent is rejected the same way.
    _assert_raises(lambda: spine.create_artifact("ghost", ArtifactKind.DIFF, "f://3"), ValueError)


# ── MI-2: resolving an escalation is a single-field write ───────────────────────
def test_mi2_resolve_escalation_is_single_field_write():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    e = spine.create_escalation(t.id, "why", control_diff=_CONTROL_DIFF, created_at=T[1])
    assert e.resolved_at is None
    before = e.to_dict()

    resolved = spine.resolve_escalation(e.id, resolved_at=T[5])

    # Only resolved_at (and the version stamp) changed — no other field touched.
    assert resolved.resolved_at == T[5]
    after = resolved.to_dict()
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"resolved_at", "version"}, changed

    # And NO paired Task.state transition (escalated is not a state).
    assert spine.get_task(t.id).state == State.CREATED


# ── dump / load: whole-blob sync seam round-trips losslessly ───────────────────
def test_dump_load_blob_round_trip():
    spine = Spine()
    p = spine.create_project("p", created_at=T[0])
    t = spine.create_task(p.id, "task", state=State.TIERED, tier=1, created_at=T[1])
    a = spine.create_artifact(t.id, ArtifactKind.VERDICT, "v://1", created_at=T[2])
    e = spine.create_escalation(t.id, "why", control_diff=_CONTROL_DIFF, created_at=T[3])

    blob = spine.store.dump()
    assert blob["schema_version"] == SCHEMA_VERSION
    assert blob["seq"] == spine.store.seq
    assert {len(blob[k]) for k in ("projects", "tasks", "artifacts", "escalations")} == {1}

    # Load into a fresh store: versions preserved (no re-stamp), seq restored.
    other = Store()
    other.load(blob)
    assert other.seq == spine.store.seq
    assert other.projects.get(p.id).to_dict() == spine.get_project(p.id).to_dict()
    assert other.tasks.get(t.id).to_dict() == spine.get_task(t.id).to_dict()
    assert other.artifacts.get(a.id).to_dict() == spine.get_artifact(a.id).to_dict()
    assert other.escalations.get(e.id).to_dict() == spine.get_escalation(e.id).to_dict()

    # The projection is identical from the reloaded store.
    assert project(other.tasks.list_all(), other.escalations.list_all()) == spine.cards()


# ── WAL: a file-backed store opens in WAL journal mode ─────────────────────────
def test_wal_mode_enabled_on_file_db():
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "spine.db")
    store = Store(path)
    try:
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
        os.rmdir(tmpdir)


# ── pure-python fallback runner (no pytest) ───────────────────────────────────
def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"{'RED' if failures else 'GREEN'}: {len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
