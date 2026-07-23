"""Tests for the Claunker Spine data core (locked v1 architecture).

Covers the new model's required surface — per-entity store round-trip across all
four tables, soft-delete omission (list_live + projection), opaque equality-only
versions, LexoRank ordering, the two Mutation Invariants (MI-1 no late children on
a tombstoned task; MI-2 resolve-escalation as a single put recording the operator
decision), the resolve validation floors + the operator-only actor invariant, the
one-to-one state→column projection, the THREE-state escalation→badge rule
(unresolved / denied / none), and the dump/load blob round-trip — adapting the
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
    SpineError,
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


# ── migration-free: a legacy Task blob with no deleted_at key loads live ───────
def test_legacy_task_blob_without_deleted_at_loads_live_and_visible():
    """A Task blob written before ``deleted_at`` existed carries no such key. The
    dataclass default fills it in (``from_dict`` ignores unknown keys, defaults the
    missing one), so the task loads with ``deleted_at=None`` — LIVE and on the board.
    No migration / backfill is needed when the soft-delete field is introduced."""
    store = Store()
    legacy = {  # exactly the pre-deleted_at blob shape (note: no "deleted_at" key)
        "id": "t1", "project_id": "p", "title": "legacy", "state": "created",
        "tier": None, "acceptance_criteria": None, "order": "i",
        "created_at": T[0], "version": "1:deadbeef",
    }
    assert "deleted_at" not in legacy
    store.load({"tasks": [legacy]})                      # raw INSERT, blob unchanged

    loaded = store.tasks.get("t1")
    assert loaded.deleted_at is None                     # defaulted, not absent
    assert len(store.tasks.list_live()) == 1             # present in the live view
    assert [c["id"] for c in project(store.tasks.list_all(), [])] == ["t1"]  # visible on the board


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


def test_due_less_task_projects_due_null_never_fabricated():
    """A Task minted with no stored due field projects due=null.

    Red-contrast: if the projection mistakenly used task.created_at as due, it
    would emit a non-null ISO string (T[0]) — this test would fail, catching the
    regression. The never-fabricate rule: only a genuinely stored due value may
    appear; with no stored due, null is the only correct output."""
    spine = Spine()
    p = spine.create_project("p")
    task = spine.create_task(p.id, "no-due task", created_at=T[0])
    card = _card_for(spine, task.id)
    # The task does have a created_at (red-contrast: a fabricating projection
    # would leak T[0] here instead of null).
    assert task.created_at == T[0]
    assert card["due"] is None  # never fabricated — null, not T[0] or any other date


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


def test_acceptance_criteria_echoes_into_the_card_lens():
    spine = Spine()
    p = spine.create_project("p")
    # Unset → the key is present (the lens always carries it) and projects as None.
    bare = spine.create_task(p.id, "bare", created_at=T[0])
    bare_card = _card_for(spine, bare.id)
    assert "acceptance_criteria" in bare_card
    assert bare_card["acceptance_criteria"] is None
    # A set value round-trips through the projection unchanged.
    criteria = ["compiles", "tests pass"]
    withac = spine.create_task(p.id, "withac", acceptance_criteria=criteria, created_at=T[1])
    assert _card_for(spine, withac.id)["acceptance_criteria"] == criteria


# ── escalation → badge (orthogonal to the state column) ────────────────────────
def test_unresolved_escalation_badges_card_and_keeps_it_in_state_column():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", state=State.DISPATCHED, created_at=T[0])

    e = spine.create_escalation(t.id, "weakens egress control", control_diff=_CONTROL_DIFF, created_at=T[1])
    card = _card_for(spine, t.id)
    # Badge present, in the unresolved state, carrying the approval-queue fields…
    assert card["badge"] is not None
    assert card["badge"]["status"] == "unresolved"
    assert card["badge"]["id"] == e.id
    assert card["badge"]["reason"] == "weakens egress control"
    assert card["badge"]["control_diff"]["reduces_control"] is True
    # …and the card STAYS in its state column (escalation is not a column).
    assert card["column_id"] == "dispatched"

    # APPROVING the escalation clears the badge (an approved change is committed;
    # the card's column is untouched).
    spine.resolve_escalation(e.id, resolution="approve", resolution_rationale="approved after review", actor="operator")
    cleared = _card_for(spine, t.id)
    assert cleared["badge"] is None
    assert cleared["column_id"] == "dispatched"


def test_approved_and_tombstoned_escalations_yield_no_badge():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", state=State.JUDGED, created_at=T[0])

    # An APPROVED escalation → no badge (the approved change is committed). (A DENIED
    # one would badge as 'denied' — that case is covered by the three-state test.)
    resolved = spine.create_escalation(t.id, "r", created_at=T[1])
    spine.resolve_escalation(resolved.id, resolution="approve", resolution_rationale="approved, looks fine", actor="operator", resolved_at=T[2])
    assert _card_for(spine, t.id)["badge"] is None

    # A live unresolved one badges it (status unresolved)…
    live = spine.create_escalation(t.id, "live", created_at=T[3])
    badge = _card_for(spine, t.id)["badge"]
    assert badge["id"] == live.id and badge["status"] == "unresolved"
    # …and tombstoning that escalation clears the badge too (only the approved one
    # remains, which yields no badge).
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


# ── MI-2: resolving an escalation is a single put, no paired state transition ──
def test_mi2_resolve_escalation_is_a_single_put_with_no_state_transition():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    e = spine.create_escalation(t.id, "why", control_diff=_CONTROL_DIFF, created_at=T[1])
    assert e.resolved_at is None
    before = e.to_dict()

    resolved = spine.resolve_escalation(
        e.id, resolution="deny", resolution_rationale="rejecting the egress weaken", actor="operator", resolved_at=T[5],
    )

    # MI-2 is ONE put that records the decision: exactly the resolution triad +
    # resolved_at + the version stamp changed — nothing else on the escalation.
    assert resolved.resolved_at == T[5]
    assert (resolved.resolution, resolved.actor) == ("deny", "operator")
    after = resolved.to_dict()
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"resolved_at", "resolution", "resolution_rationale", "actor", "version"}, changed

    # And NO paired Task.state transition (escalated is not a state).
    assert spine.get_task(t.id).state == State.CREATED


# ── resolve_escalation: decision recorded; rationale floor + actor invariant ────
def test_resolve_escalation_records_decision_rationale_and_actor():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])

    appr = spine.create_escalation(t.id, "loosen egress", created_at=T[1])
    spine.resolve_escalation(appr.id, resolution="approve", resolution_rationale="reviewed and accepted", actor="operator", resolved_at=T[2])
    got = spine.get_escalation(appr.id)
    assert (got.resolution, got.resolution_rationale, got.actor) == ("approve", "reviewed and accepted", "operator")
    assert got.resolved_at == T[2]

    deny = spine.create_escalation(t.id, "loosen egress again", created_at=T[3])
    spine.resolve_escalation(deny.id, resolution="deny", resolution_rationale="rejecting: weakens the guardrail", actor="operator")
    got2 = spine.get_escalation(deny.id)
    assert got2.resolution == "deny" and got2.resolved_at is not None  # resolved_at defaulted to now


def test_resolve_escalation_rejects_unknown_resolution():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    e = spine.create_escalation(t.id, "why", created_at=T[1])
    # Only 'approve'/'deny' are decisions; anything else is validation_failed (ValueError).
    _assert_raises(lambda: spine.resolve_escalation(e.id, resolution="maybe", resolution_rationale="a long enough rationale", actor="operator"), ValueError)
    assert spine.get_escalation(e.id).resolved_at is None  # no partial write


def test_resolve_escalation_enforces_rationale_floor():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    e = spine.create_escalation(t.id, "why", created_at=T[1])
    # Under 10 non-whitespace chars → ValueError (a semantic floor, not just non-empty)…
    _assert_raises(lambda: spine.resolve_escalation(e.id, resolution="approve", resolution_rationale="too short", actor="operator"), ValueError)
    # …and whitespace does not count toward the floor (.strip()).
    _assert_raises(lambda: spine.resolve_escalation(e.id, resolution="approve", resolution_rationale="          ", actor="operator"), ValueError)
    assert spine.get_escalation(e.id).resolved_at is None


def test_resolve_escalation_enforces_operator_actor_invariant():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    e = spine.create_escalation(t.id, "why", created_at=T[1])
    # A non-operator actor is a HARD abort (PermissionError → unauthorized), distinct
    # from the argument ValueErrors — even with an otherwise-valid resolution+rationale.
    _assert_raises(lambda: spine.resolve_escalation(e.id, resolution="approve", resolution_rationale="valid rationale here", actor="agent"), PermissionError)
    assert spine.get_escalation(e.id).resolved_at is None  # nothing written


def test_resolve_escalation_unknown_id_raises_keyerror():
    spine = Spine()
    _assert_raises(
        lambda: spine.resolve_escalation("ghost", resolution="approve", resolution_rationale="valid rationale here", actor="operator"),
        KeyError,
    )


# ── projection: the THREE-state escalation badge (unresolved > denied > none) ───
def test_projection_badge_is_three_state_unresolved_denied_none():
    spine = Spine()
    p = spine.create_project("p")

    # (1) a live unresolved escalation → status 'unresolved'.
    t1 = spine.create_task(p.id, "unresolved", created_at=T[0])
    spine.create_escalation(t1.id, "pending", control_diff=_CONTROL_DIFF, created_at=T[1])
    assert _card_for(spine, t1.id)["badge"]["status"] == "unresolved"

    # (2) most-recent resolution is a DENY → status 'denied', carrying the rationale receipt.
    t2 = spine.create_task(p.id, "denied", created_at=T[0])
    d = spine.create_escalation(t2.id, "weakens egress", control_diff=_CONTROL_DIFF, created_at=T[1])
    spine.resolve_escalation(d.id, resolution="deny", resolution_rationale="rejecting: ghost-worker overreach", actor="operator", resolved_at=T[2])
    badge2 = _card_for(spine, t2.id)["badge"]
    assert badge2["status"] == "denied"
    assert badge2["id"] == d.id
    assert badge2["resolution_rationale"] == "rejecting: ghost-worker overreach"

    # (3) most-recent resolution is an APPROVE → NO badge.
    t3 = spine.create_task(p.id, "approved", created_at=T[0])
    a = spine.create_escalation(t3.id, "fine", created_at=T[1])
    spine.resolve_escalation(a.id, resolution="approve", resolution_rationale="approved, committed", actor="operator", resolved_at=T[2])
    assert _card_for(spine, t3.id)["badge"] is None

    # precedence: a NEW unresolved escalation outranks an earlier denial on the same card.
    t4 = spine.create_task(p.id, "denied-then-reescalated", created_at=T[0])
    d4 = spine.create_escalation(t4.id, "first", control_diff=_CONTROL_DIFF, created_at=T[1])
    spine.resolve_escalation(d4.id, resolution="deny", resolution_rationale="denied the first attempt", actor="operator", resolved_at=T[2])
    spine.create_escalation(t4.id, "second", created_at=T[3])  # a fresh unresolved one
    assert _card_for(spine, t4.id)["badge"]["status"] == "unresolved"


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


# ── Task.created_by: nullable actor, validation, projection passthrough ────────

def test_task_created_by_defaults_to_null():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    assert t.created_by is None


def test_task_created_by_valid_agent():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0], created_by={"type": "agent", "id": "claude-code"})
    assert t.created_by == {"type": "agent", "id": "claude-code"}
    assert spine.get_task(t.id).created_by == {"type": "agent", "id": "claude-code"}


def test_task_created_by_valid_human():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0], created_by={"type": "human", "id": "icey2488"})
    assert t.created_by == {"type": "human", "id": "icey2488"}


def test_task_created_by_malformed_rejected():
    # bad type string
    _assert_raises(lambda: Task(id="t", project_id="p", title="x", created_by={"type": "robot", "id": "r1"}), SpineError)
    # missing id
    _assert_raises(lambda: Task(id="t", project_id="p", title="x", created_by={"type": "agent"}), SpineError)
    # empty id
    _assert_raises(lambda: Task(id="t", project_id="p", title="x", created_by={"type": "agent", "id": ""}), SpineError)
    # not a dict
    _assert_raises(lambda: Task(id="t", project_id="p", title="x", created_by="claude-code"), SpineError)


def test_task_created_by_null_is_fine():
    # null default is explicitly fine — no SpineError on None
    t = Task(id="t", project_id="p", title="x", created_by=None)
    assert t.created_by is None


def test_projection_passthrough_created_by_agent():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0], created_by={"type": "agent", "id": "claude-code"})
    card = _card_for(spine, t.id)
    assert card["created_by"] == {"type": "agent", "id": "claude-code"}


def test_projection_null_created_by_for_legacy_null():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    card = _card_for(spine, t.id)
    assert card["created_by"] is None


def test_legacy_blob_without_created_by_loads_untouched():
    """A blob written before created_by existed carries no such key. from_dict ignores
    unknown keys and defaults the missing one, so the task loads with created_by=None."""
    store = Store()
    legacy = {
        "id": "t1", "project_id": "p", "title": "legacy", "state": "created",
        "tier": None, "acceptance_criteria": None, "order": "i",
        "created_at": T[0], "version": "1:deadbeef", "deleted_at": None, "archived_at": None,
    }
    assert "created_by" not in legacy
    store.load({"tasks": [legacy]})
    loaded = store.tasks.get("t1")
    assert loaded.created_by is None
    assert len(store.tasks.list_live()) == 1


# ── created_by dispatch provenance: model/effort/job_id inside created_by ──────
# Provenance rides INSIDE created_by (never a top-level effort/model — the Task's own
# effort/impact are the Matrix work-size axes and MUST NOT collide). Additive-optional:
# a human/plain card carries none; the keys are validated as strings; unknown foreign
# keys are tolerated; the whole stamp projects through the lens verbatim.

def test_task_created_by_agent_with_provenance_persists_and_projects():
    spine = Spine()
    p = spine.create_project("p")
    prov = {"type": "agent", "id": "claude-code", "model": "claude-sonnet-5",
            "effort": "medium", "job_id": "job-42"}
    t = spine.create_task(p.id, "x", created_at=T[0], created_by=prov)
    assert t.created_by == prov
    assert spine.get_task(t.id).created_by == prov      # survives the store round-trip
    assert _card_for(spine, t.id)["created_by"] == prov  # projects verbatim


def test_task_created_by_provenance_non_string_rejected():
    # model/effort/job_id are shape-checked as strings WHEN PRESENT (write-admission).
    _assert_raises(lambda: Task(id="t", project_id="p", title="x",
                                created_by={"type": "agent", "id": "a", "model": 5}), SpineError)
    _assert_raises(lambda: Task(id="t", project_id="p", title="x",
                                created_by={"type": "agent", "id": "a", "effort": ["high"]}), SpineError)
    _assert_raises(lambda: Task(id="t", project_id="p", title="x",
                                created_by={"type": "agent", "id": "a", "job_id": 7}), SpineError)


def test_task_created_by_unknown_string_keys_tolerated():
    """MCP interop: a created_by minted by a FOREIGN server may carry KEYS we do not
    model. Unknown keys with STRING values must NOT break our write or read path —
    additive-only, preserved verbatim through store and lens."""
    spine = Spine()
    p = spine.create_project("p")
    foreign = {"type": "agent", "id": "some-other-agent", "model": "their-model",
               "vendor_trace": "span-abc", "cost_note": "3c"}
    t = spine.create_task(p.id, "x", created_at=T[0], created_by=foreign)
    assert t.created_by == foreign
    assert _card_for(spine, t.id)["created_by"] == foreign  # unknown STRING keys survive the lens


def test_task_created_by_unknown_nonstring_value_rejected():
    """Unknown-KEY tolerance does NOT extend to non-string VALUES: a nested object or
    array (or number) under a foreign key is rejected, closing the nesting/depth hole
    (previously only the modeled model/effort/job_id keys were type-checked, so a nested
    unknown value was silently admitted and then immutable forever)."""
    _assert_raises(lambda: Task(id="t", project_id="p", title="x",
                                created_by={"type": "agent", "id": "a", "vendor_trace": {"span": "abc"}}),
                   SpineError)
    _assert_raises(lambda: Task(id="t", project_id="p", title="x",
                                created_by={"type": "agent", "id": "a", "cost_cents": 3}),
                   SpineError)


def test_created_by_admission_caps_enforced_at_create_task():
    """The size caps guard create_task itself (the CLI / local-trust path), not only the
    wire tool: too-many keys, an oversized value, and an oversized total all raise at
    mint. A realistic payload passes. Proves BOTH write paths are bounded."""
    from spine.entity import (MAX_CREATED_BY_BYTES, MAX_PROVENANCE_KEYS,
                              MAX_PROVENANCE_VALUE_LEN)
    spine = Spine()
    p = spine.create_project("p")
    base = {"type": "agent", "id": "a"}
    # too many keys
    _assert_raises(lambda: spine.create_task(
        p.id, "x", created_by={**base, **{f"k{i}": "v" for i in range(MAX_PROVENANCE_KEYS + 1)}}), SpineError)
    # oversized single value
    _assert_raises(lambda: spine.create_task(
        p.id, "x", created_by={**base, "model": "m" * (MAX_PROVENANCE_VALUE_LEN + 1)}), SpineError)
    # oversized total (each value ≤ cap, key count ≤ cap, but total over the byte cap)
    _assert_raises(lambda: spine.create_task(
        p.id, "x", created_by={**base, **{f"k{i}": "v" * 500 for i in range(10)}}), SpineError)
    # realistic mint passes comfortably
    ok = spine.create_task(p.id, "x", created_at=T[0],
                           created_by={**base, "model": "claude-sonnet-5", "effort": "high", "job_id": "job-1"})
    assert ok.created_by["model"] == "claude-sonnet-5"
    assert MAX_CREATED_BY_BYTES == 4096  # pin the documented contract number


# ── R6 durable-ref validation in create_artifact ──────────────────────────────

def test_r6_git_hash_ref_accepted():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    a = spine.create_artifact(t.id, ArtifactKind.DELIVERY, "81d33c2a4b5e6f7890abcdef1234567890abcdef")
    assert a.ref == "81d33c2a4b5e6f7890abcdef1234567890abcdef"


def test_r6_unix_local_path_rejected():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    _assert_raises(lambda: spine.create_artifact(t.id, ArtifactKind.FILE, "/workspace/out.py"), ValueError)


def test_r6_windows_local_path_rejected():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    _assert_raises(lambda: spine.create_artifact(t.id, ArtifactKind.FILE, "C:\\output\\result.txt"), ValueError)


def test_r6_tilde_path_rejected():
    spine = Spine()
    p = spine.create_project("p")
    t = spine.create_task(p.id, "x", created_at=T[0])
    _assert_raises(lambda: spine.create_artifact(t.id, ArtifactKind.FILE, "~/output.py"), ValueError)


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
