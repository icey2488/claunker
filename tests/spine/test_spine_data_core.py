"""Tests for the Claunker Spine data core (read-slice foundation).

Covers the converged design's required cases — round-trip (events → reduce →
project), soft-delete omission, ordering (append reflected in LexoRank order),
version opacity (string, stable for unchanged state, changes on mutation), and
gate_status == "COMMITTED" — plus the supporting controls (lifecycle→column,
tier→tag, escalated→badge, actor refs, tombstone immutability, and the two-phase
version-stamp consistency).

Run (pytest):
    uv run --with pytest --python 3.11 python -m pytest tests/spine/ -q
Run (no pytest — pure-python fallback):
    uv run --python 3.11 python tests/spine/test_spine_data_core.py
"""

import os
import sys

# Make ``spine`` importable when this file is run directly (python tests/spine/x.py
# puts THIS dir on sys.path, not the repo root). Harmless under pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from spine import (  # noqa: E402
    Actor,
    EventStore,
    EventType,
    Lifecycle,
    MAX_RANK_LENGTH,
    Spine,
    append_rank,
    lifecycle_to_column,
    make_version,
    project,
    rank_between,
    rebalance,
    reduce,
    reduce_all,
    to_card,
)

# Fixed timestamps so reductions (and thus version tokens) are deterministic.
T = [f"2026-06-28T0{i}:00:00+00:00" for i in range(9)]


# ── round-trip: append events → reduce → project → assert the card set ────────
def test_round_trip_events_reduce_project():
    store = EventStore()
    a, b, c = "task-A", "task-B", "task-C"

    # A: a full mutation history.
    store.append_event(a, EventType.CREATED, {"title": "build core", "order": "i",
                       "lifecycle_state": Lifecycle.CREATED}, Actor.OPERATOR, created_at=T[0])
    store.append_event(a, EventType.TITLE_CHANGED, {"title": "build the spine core"},
                       Actor.CLAUDE, created_at=T[1])
    store.append_event(a, EventType.COLUMN_CHANGED, {"lifecycle_state": Lifecycle.EXECUTING},
                       Actor.CLAUDE, created_at=T[2])
    store.append_event(a, EventType.TIER_ASSIGNED, {"tier": 2}, Actor.CLAUDE, created_at=T[3])
    store.append_event(a, EventType.ESCALATION_RAISED, {"ref": "esc-1"}, Actor.OLLAMA, created_at=T[4])

    # B: a plain queued task.
    store.append_event(b, EventType.CREATED, {"title": "second", "order": "r",
                       "lifecycle_state": Lifecycle.QUEUED}, Actor.OPERATOR, created_at=T[5])

    # C: created then soft-deleted (must be omitted from projection).
    store.append_event(c, EventType.CREATED, {"title": "third", "order": "v"},
                       Actor.OPERATOR, created_at=T[6])
    store.append_event(c, EventType.DELETED, {}, Actor.OPERATOR, created_at=T[7])

    entities = reduce_all(store.read_all_events())
    cards = project(list(entities.values()))
    by_id = {card["id"]: card for card in cards}

    # The soft-deleted C is absent; A and B remain.
    assert set(by_id) == {a, b}, by_id

    card_a = by_id[a]
    assert card_a["title"] == "build the spine core"          # last TITLE_CHANGED won
    assert card_a["column_id"] == "executing"                 # executing → its own column
    assert card_a["tags"] == ["tier:2"]                       # tier → a tag
    assert card_a["badge"] == "escalated"                     # escalated → a badge
    assert card_a["gate_status"] == "COMMITTED"
    assert card_a["order"] == "i"                             # order passes through
    assert card_a["version"] == entities[a].version           # version passes through
    assert card_a["created_by"] == {"type": "human", "id": "operator"}
    assert card_a["updated_by"] == {"type": "agent", "id": "ollama"}  # last actor

    card_b = by_id[b]
    assert card_b["column_id"] == "queued"                    # queued → its own column
    assert card_b["tags"] == []                               # no tier assigned
    assert card_b["badge"] is None                            # not escalated

    # Ordering: A ("i") sorts before B ("r") in the projected list.
    assert [card["id"] for card in cards] == [a, b]


# ── soft-delete omission ──────────────────────────────────────────────────────
def test_soft_delete_omitted_from_projection_but_present_in_reduce():
    store = EventStore()
    c = "c"
    store.append_event(c, EventType.CREATED, {"title": "x", "order": "i"}, Actor.OPERATOR, created_at=T[0])
    store.append_event(c, EventType.DELETED, {}, Actor.OPERATOR, created_at=T[1])

    entity = reduce(store.read_events(c))
    assert entity is not None
    assert entity.deleted_at == T[1]          # reduce still surfaces the tombstone
    assert to_card(entity) is None            # but the lens omits it
    assert project([entity]) == []


# ── ordering: append order reflected in LexoRank order ────────────────────────
def test_creation_order_reflected_in_lexorank_order():
    spine = Spine()
    a = spine.create_task("a", Actor.OPERATOR)
    b = spine.create_task("b", Actor.OPERATOR)
    c = spine.create_task("c", Actor.OPERATOR)

    assert a.order < b.order < c.order, (a.order, b.order, c.order)
    # The projected board reflects creation order.
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


# ── version opacity: string, stable for unchanged state, changes on mutation ──
def test_version_is_opaque_string_stable_then_changes_on_mutation():
    store = EventStore()
    e = "e"
    store.append_event(e, EventType.CREATED, {"title": "t", "order": "i"}, Actor.OPERATOR, created_at=T[0])

    v1 = reduce(store.read_events(e)).version
    v1_again = reduce(store.read_events(e)).version
    assert isinstance(v1, str) and ":" in v1            # opaque {seq}:{hash} string
    assert v1 == v1_again                                # stable for unchanged state

    store.append_event(e, EventType.TITLE_CHANGED, {"title": "t2"}, Actor.CLAUDE, created_at=T[1])
    v2 = reduce(store.read_events(e)).version
    assert v2 != v1                                      # changes on mutation
    assert isinstance(v2, str)


def test_event_version_column_matches_reduced_version():
    """Two-phase stamp consistency: the version stamped on the row equals the
    reducer-derived token, and it moves on every mutation."""
    spine = Spine()
    created = spine.create_task("a", Actor.OPERATOR)
    events = spine.store.read_events(created.id)
    assert events[-1].version == created.version

    updated = spine.change_title(created.id, "a2")
    events2 = spine.store.read_events(created.id)
    assert events2[-1].version == updated.version
    assert events2[-1].version != events[-1].version


# ── gate_status == "COMMITTED" on every projected card ────────────────────────
def test_every_projected_card_is_gate_status_committed():
    spine = Spine()
    spine.create_task("a", Actor.OPERATOR)
    spine.create_task("b", Actor.CLAUDE)
    cards = spine.cards()
    assert cards
    assert all(card["gate_status"] == "COMMITTED" for card in cards)


# ── supporting controls ───────────────────────────────────────────────────────
def test_lifecycle_maps_one_to_one_to_own_column_with_passthrough():
    assert lifecycle_to_column(Lifecycle.CREATED) == "created"
    assert lifecycle_to_column(Lifecycle.QUEUED) == "queued"
    assert lifecycle_to_column(Lifecycle.EXECUTING) == "executing"
    assert lifecycle_to_column(Lifecycle.JUDGING) == "judging"
    assert lifecycle_to_column(Lifecycle.DELIVERED) == "delivered"
    assert lifecycle_to_column(Lifecycle.FAILED) == "failed"
    # Unknown lifecycle passes through (→ Kanbantt fallback tray), never dropped.
    assert lifecycle_to_column("some_custom_state") == "some_custom_state"


def test_escalation_raise_then_resolve_clears_badge():
    spine = Spine()
    e = spine.create_task("a", Actor.OPERATOR)
    spine.raise_escalation(e.id, "esc-9", Actor.CLAUDE)
    card = next(c for c in spine.cards() if c["id"] == e.id)
    assert card["badge"] == "escalated"

    resolved = spine.resolve_escalation(e.id, Actor.OPERATOR)
    assert resolved.escalated is False
    assert resolved.escalation_ref is None
    card2 = next(c for c in spine.cards() if c["id"] == e.id)
    assert card2["badge"] is None


def test_soft_deleted_entity_is_immutable():
    spine = Spine()
    e = spine.create_task("a", Actor.OPERATOR)
    spine.soft_delete(e.id)
    # The tombstone is immutable: any further mutation must raise.
    raised = False
    try:
        spine.change_title(e.id, "nope")
    except ValueError:
        raised = True
    assert raised, "expected a soft-deleted entity to reject mutation"


def test_event_before_created_raises():
    store = EventStore()
    store.append_event("z", EventType.TITLE_CHANGED, {"title": "t"}, Actor.CLAUDE, created_at=T[0])
    raised = False
    try:
        reduce(store.read_events("z"))
    except ValueError:
        raised = True
    assert raised, "expected reduce to reject an event stream not starting with CREATED"


def test_make_version_is_deterministic_and_seq_sensitive():
    content = {"id": "x", "title": "t"}
    assert make_version(1, content) == make_version(1, content)   # deterministic
    assert make_version(1, content) != make_version(2, content)   # seq prefix moves it
    assert make_version(1, content) != make_version(1, {"id": "x", "title": "u"})


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
