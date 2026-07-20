"""The four ``card_*`` operator-write tools — the FREE / ungoverned write path,
now CONFORMED to the kanbantt-mcp-spec card-write contract (§Tool Contract,
§Concurrency).

Drives each tool through the SDK's in-memory client (same harness as
test_escalation_resolve) against a file-backed spine, and asserts the ratified
stance + the spec's optimistic-concurrency model:

  * card_create speaks the spec shape { card: CardInput, project_id } and persists a
    new Task, returning its projected Card. HUMAN INTAKE defaults: column 'created',
    UNTIERED (tier only when the input carries one — a "tier:N" tag or the
    card_update-style tolerance). Idempotent on a duplicate id (live OR tombstone —
    the existing card returns as success, before the project_id requirement so a
    retry never trips targeting). project_id is REQUIRED (validation_failed naming
    project_list when absent; unknown/tombstoned project → not_found — no phantom
    projects, no silent default). Authority-owned CardInput fields are ignored;
    created_by is stamped from the credential ({type: human, id: operator}).
  * card_update takes the spec shape { id, patch, expected_version, force? } and edits
    ONLY the modeled mutable fields in the patch (title / acceptance_criteria / effort /
    impact / tier); patch.tier rides as the "tier:N" tag-id string the projection emits
    and round-trips on the FREE initial classification (untiered → N). A SET tier is
    WRITE-ONCE — it changes only via the governed card_retier path (see
    test_card_retier). effort/impact are plain ungoverned string fields with the exact
    same "only present keys apply" treatment as acceptance_criteria — no validation, no
    write-once semantics.
  * card_move takes { id, column_id, order, expected_version, force? } — column_id IS
    the target state, and it is FREE (moves across NON-adjacent states with no
    transition check) at the supplied LexoRank order.
  * card_delete takes { id, expected_version } (NO force) and is a SOFT delete: it
    returns the TOMBSTONE card, the card disappears from card_list/the board, but the
    ROW + its data persist on disk (auditable, recoverable).

  * OPTIMISTIC CONCURRENCY: a mismatched expected_version → a `conflict` error whose
    meta.current is the freshly-read current card; force (update/move only) overrides
    the check; a write to a tombstone → `conflict` (meta.current = the tombstone),
    even under force; card_delete of an already-tombstoned card → `conflict`.

Async calls go through ``anyio.run`` inside sync tests, so no async plugin is needed.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import Spine, Store  # noqa: E402
from spine.entity import State  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import build_server  # noqa: E402
from tests.spine_server._util import cleanup, make_temp_db  # noqa: E402

# A token that never matches a real one (real tokens are "{seq}:{hash}" with seq>=1).
STALE = "0:stale"


def _config(path, **overrides):
    return ServerConfig(token="test-token", db_path=path, enable_dns_rebinding_protection=False, **overrides)


def _seed_task(path, *, title="t", state=State.CREATED, tier=None):
    """Seed project + one task; return (project_id, task_id)."""
    spine = Spine(Store(path))
    try:
        proj = spine.create_project("p")
        task = spine.create_task(proj.id, title, state=state, tier=tier)
        return proj.id, task.id
    finally:
        spine.store.close()


async def _call(server, name, arguments):
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool(name, arguments)
        return result.isError, result.structuredContent


async def _cards(server):
    """The current card_list snapshot (live cards only)."""
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool("card_list", {})
        return result.structuredContent["cards"]


def _task_on_disk(path, task_id):
    """Read a task straight from a freshly-opened store (proves a committed write)."""
    with Store(path) as store:
        return store.tasks.get(task_id)


def _version_of(path, task_id):
    """The task's current opaque version token (read fresh) — the expected_version a
    conforming client would echo back from its last-seen card."""
    return _task_on_disk(path, task_id).version


def _tombstone(path, task_id):
    """Soft-delete a task directly via the facade (setup for the immutability cases).
    Uses the internal None-version path, which skips the optimistic check."""
    spine = Spine(Store(path))
    try:
        spine.soft_delete_task(task_id)
    finally:
        spine.store.close()


# ── card_create — spec shape { card: CardInput, project_id } (the conformance pass) ──
def test_card_create_persists_and_returns_projected_card():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)  # gives us a real project to create into
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "new card", "column_id": "tiered", "tags": ["tier:3"]},
             "project_id": project_id},
        )
        assert is_error is False
        card = sc["card"]
        # The projected Card: column IS the state, tier rides as its tag,
        # gate_status COMMITTED, no badge.
        assert card["title"] == "new card"
        assert card["column_id"] == "tiered"
        assert card["tags"] == ["tier:3"]
        assert card["gate_status"] == "COMMITTED"
        assert card["badge"] is None
        # Committed to disk (acceptance_criteria is stored though not in the Card lens).
        stored = _task_on_disk(path, card["id"])
        assert stored is not None and stored.state == "tiered" and stored.tier == 3
        assert stored.project_id == project_id
    finally:
        cleanup(directory)


def test_card_create_defaults_are_human_intake_created_and_untiered():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "intake"}, "project_id": project_id},
        )
        assert is_error is False
        # HUMAN INTAKE: the card enters 'created', UNTIERED (no tier:N tag), exactly
        # like a hand-written card — the retired int tier=1 default pre-classified.
        assert sc["card"]["column_id"] == "created"
        assert sc["card"]["tags"] == []
        stored = _task_on_disk(path, sc["card"]["id"])
        assert stored.tier is None
        assert stored.acceptance_criteria == ""            # default ""
        # order is server-appended when the client mints none.
        assert isinstance(stored.order, str) and stored.order
        # created_by is derived from the credential (the operator token), never null.
        assert stored.created_by == {"type": "human", "id": "operator"}
    finally:
        cleanup(directory)


def test_card_create_honors_client_minted_id_and_order():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"id": "client-uuid-1", "title": "positioned", "order": "zz"},
             "project_id": project_id},
        )
        assert is_error is False
        assert sc["card"]["id"] == "client-uuid-1"     # servers MUST accept client ids
        assert sc["card"]["order"] == "zz"             # client-minted LexoRank honored
        assert _task_on_disk(path, "client-uuid-1").order == "zz"
    finally:
        cleanup(directory)


def test_card_create_duplicate_id_returns_existing_card_as_success():
    directory, path = make_temp_db()
    try:
        project_id, task_id = _seed_task(path, title="original")
        # A retry replaying an id the spine knows returns the EXISTING card — success,
        # no write, the different payload ignored. Runs before the project_id
        # requirement (an untargeted retry of a landed create must not trip it), so
        # this call deliberately omits project_id.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"id": task_id, "title": "totally different"}},
        )
        assert is_error is False
        assert sc["card"]["id"] == task_id
        assert sc["card"]["title"] == "original"                 # nothing overwritten
        assert _task_on_disk(path, task_id).title == "original"
        assert _version_of(path, task_id) == sc["card"]["version"]  # no version mint
    finally:
        cleanup(directory)


def test_card_create_duplicate_tombstoned_id_returns_the_tombstone():
    directory, path = make_temp_db()
    try:
        project_id, task_id = _seed_task(path, title="gone")
        _tombstone(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"id": task_id, "title": "resurrect?"}, "project_id": project_id},
        )
        # Spec §Create: "including tombstoned" — the existing card comes back as
        # success, and it IS the tombstone (deleted_at set): create never resurrects.
        assert is_error is False
        assert sc["card"]["id"] == task_id
        assert sc["card"]["deleted_at"] is not None
        assert _task_on_disk(path, task_id).title == "gone"
    finally:
        cleanup(directory)


def test_card_create_empty_title_is_validation_failed():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "   "}, "project_id": project_id},  # whitespace-only is empty
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


def test_card_create_bad_tier_is_validation_failed():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)
        server = build_server(_config(path))
        # Out-of-range via the card_update-style tier tolerance…
        is_error, sc = anyio.run(
            _call, server, "card_create",
            {"card": {"title": "t", "tier": 7}, "project_id": project_id},
        )
        assert is_error is True and sc["code"] == "validation_failed"
        # …out-of-range via the canonical tag form…
        is_error, sc = anyio.run(
            _call, server, "card_create",
            {"card": {"title": "t", "tags": ["tier:0"]}, "project_id": project_id},
        )
        assert is_error is True and sc["code"] == "validation_failed"
        # …and a malformed tier tag (not parseable to a tier int).
        is_error, sc = anyio.run(
            _call, server, "card_create",
            {"card": {"title": "t", "tags": ["tier:abc"]}, "project_id": project_id},
        )
        assert is_error is True and sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


def test_card_create_missing_project_id_is_validation_failed_naming_the_read():
    directory, path = make_temp_db()
    try:
        _seed_task(path)  # projects exist; the caller just failed to target one
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {"title": "untargeted"}},
        )
        # LOUD, no default-project fallback: the error names the enumeration read.
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "project_list" in sc["message"]
    finally:
        cleanup(directory)


def test_card_create_unknown_or_tombstoned_project_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path)  # a real project exists, but we target a ghost id
        server = build_server(_config(path))
        is_error, sc = anyio.run(
            _call, server, "card_create",
            {"card": {"title": "orphan"}, "project_id": "ghost"},
        )
        assert is_error is True and sc["code"] == "not_found"
        # A soft-deleted project is not a live create target either.
        spine = Spine(Store(path))
        try:
            dead = spine.create_project("retired")
            dead.deleted_at = "2026-01-01T00:00:00+00:00"
            spine.store.projects.put(dead)
            dead_id = dead.id
        finally:
            spine.store.close()
        is_error, sc = anyio.run(
            _call, server, "card_create",
            {"card": {"title": "orphan"}, "project_id": dead_id},
        )
        assert is_error is True and sc["code"] == "not_found"
    finally:
        cleanup(directory)


def test_card_create_ignores_authority_owned_fields():
    directory, path = make_temp_db()
    try:
        project_id, _ = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_create",
            {"card": {
                "title": "forged",
                "version": "999:forged",
                "created_at": "1999-01-01T00:00:00+00:00",
                "created_by": {"type": "agent", "id": "impostor"},
                "deleted_at": "1999-01-01T00:00:00+00:00",
            }, "project_id": project_id},
        )
        # Spec §Create: authority-owned fields supplied by a client MUST be ignored,
        # not errored. The stamp comes from the store/credential, never the payload.
        assert is_error is False
        stored = _task_on_disk(path, sc["card"]["id"])
        assert stored.version != "999:forged"
        assert stored.created_by == {"type": "human", "id": "operator"}
        assert stored.deleted_at is None
        assert stored.created_at != "1999-01-01T00:00:00+00:00"
    finally:
        cleanup(directory)


def test_card_create_passes_due_and_depends_on_through_facade_validation():
    directory, path = make_temp_db()
    try:
        project_id, other_id = _seed_task(path)
        server = build_server(_config(path))
        is_error, sc = anyio.run(
            _call, server, "card_create",
            {"card": {"title": "dated", "due": "2026-08-01T00:00:00+00:00",
                      "depends_on": [other_id]},
             "project_id": project_id},
        )
        assert is_error is False
        assert sc["card"]["due"] == "2026-08-01T00:00:00+00:00"
        assert sc["card"]["depends_on"] == [other_id]
        # A malformed due is the facade's ValueError → validation_failed.
        is_error, sc = anyio.run(
            _call, server, "card_create",
            {"card": {"title": "dated", "due": "not-a-date"}, "project_id": project_id},
        )
        assert is_error is True and sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


# ── card_update — spec shape { id, patch, expected_version, force? } ──────────────
def test_card_update_changes_only_provided_fields():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="before", tier=1)
        ev = _version_of(path, task_id)
        # Patch ONLY title + acceptance_criteria; tier omitted → unchanged.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "after", "acceptance_criteria": "must compile"},
             "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["title"] == "after"
        assert sc["card"]["tags"] == ["tier:1"]            # tier left untouched
        stored = _task_on_disk(path, task_id)
        assert stored.title == "after" and stored.tier == 1
        assert stored.acceptance_criteria == "must compile"
        assert stored.version != ev                        # a mutation minted a new token
    finally:
        cleanup(directory)


def test_card_update_tier_round_trips_as_tag_id_string():
    directory, path = make_temp_db()
    try:
        # Tier is WRITE-ONCE as of spec v0.3.0: card_update may set an UNTIERED card's
        # INITIAL tier (the free first classification) but not CHANGE a set one — that is
        # the governed card_retier path (see test_card_retier). So the "tier:N" tag-id
        # string round-trip is exercised here on the surviving free path: untiered → N.
        _, task_id = _seed_task(path, tier=None)
        ev = _version_of(path, task_id)
        # patch.tier is the "tier:N" tag-id string the projection emits — the EXACT
        # representation the card carries in `tags`; it must round-trip (string→int→string).
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"tier": "tier:4"}, "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["tags"] == ["tier:4"]            # projection re-emits the same string
        assert _task_on_disk(path, task_id).tier == 4      # mapped to the internal int
    finally:
        cleanup(directory)


def test_card_update_empty_patch_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
    finally:
        cleanup(directory)


def test_card_update_bad_tier_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        ev = _version_of(path, task_id)
        # A well-formed "tier:N" string but out of the 1..4 range.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"tier": "tier:9"}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, task_id).tier == 2  # no partial write
    finally:
        cleanup(directory)


def test_card_update_malformed_tier_string_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, tier=2)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"tier": "nonsense"}, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, task_id).tier == 2  # no write
    finally:
        cleanup(directory)


def test_card_update_unknown_id_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": "ghost", "patch": {"title": "x"}, "expected_version": STALE},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
    finally:
        cleanup(directory)


def test_card_update_empty_title_is_validation_failed_no_partial_write():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="before", tier=2)
        ev = _version_of(path, task_id)
        # An explicit empty / whitespace-only title is rejected on update just as on
        # create — and the co-submitted valid tier must NOT be written (atomic reject).
        for blank in ("", "   "):
            is_error, sc = anyio.run(
                _call, build_server(_config(path)), "card_update",
                {"id": task_id, "patch": {"title": blank, "tier": "tier:4"},
                 "expected_version": ev},
            )
            assert is_error is True
            assert sc["code"] == "validation_failed"
            stored = _task_on_disk(path, task_id)
            assert stored.title == "before"   # title untouched on disk
            assert stored.tier == 2           # no partial write of the valid field
    finally:
        cleanup(directory)


def test_card_update_version_mismatch_is_conflict_with_current_meta():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="before", tier=1)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "after"}, "expected_version": STALE},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        # meta.current is the freshly-read current card — immediate ground truth.
        current = sc["meta"]["current"]
        assert current["id"] == task_id
        assert current["title"] == "before"        # unchanged
        assert current["deleted_at"] is None        # still live
        assert _task_on_disk(path, task_id).title == "before"  # no write happened
    finally:
        cleanup(directory)


def test_card_update_force_overrides_version_check():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="before")
        # force: true deliberately crushes the (stale) version check.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "after"}, "expected_version": STALE, "force": True},
        )
        assert is_error is False
        assert sc["card"]["title"] == "after"
        assert _task_on_disk(path, task_id).title == "after"
    finally:
        cleanup(directory)


def test_card_update_on_tombstone_is_conflict_even_with_force():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="doomed")
        _tombstone(path, task_id)
        # Tombstones are immutable — force MUST NOT resurrect them.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "zombie"}, "expected_version": STALE, "force": True},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        current = sc["meta"]["current"]
        assert current["id"] == task_id
        assert current["deleted_at"] is not None     # the tombstone rides in meta.current
        assert _task_on_disk(path, task_id).title == "doomed"  # untouched
    finally:
        cleanup(directory)


# ── card_update — effort/impact (plain ungoverned fields, spec §Field rules) ──────
def test_card_update_effort_and_impact_set_and_returned():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"effort": "high", "impact": "med"},
             "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["effort"] == "high"
        assert sc["card"]["impact"] == "med"
        stored = _task_on_disk(path, task_id)
        assert stored.effort == "high" and stored.impact == "med"
    finally:
        cleanup(directory)


def test_card_update_effort_and_impact_round_trip_on_fresh_read():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        ev = _version_of(path, task_id)
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"effort": "low", "impact": "high"},
             "expected_version": ev},
        )
        # A brand-new server/client connection, reading fresh off disk — not the
        # immediate call_tool response — proves persistence, not just an echo.
        cards = anyio.run(_cards, build_server(_config(path)))
        card = next(c for c in cards if c["id"] == task_id)
        assert card["effort"] == "low"
        assert card["impact"] == "high"
    finally:
        cleanup(directory)


def test_card_update_omitting_effort_impact_leaves_existing_values_untouched():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="before")
        ev = _version_of(path, task_id)
        anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"effort": "high", "impact": "low"},
             "expected_version": ev},
        )
        ev2 = _version_of(path, task_id)
        # A second patch touching only title — effort/impact absent from the patch
        # entirely, must survive unchanged (same "only present keys apply" rule as
        # acceptance_criteria).
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "after"}, "expected_version": ev2},
        )
        assert is_error is False
        assert sc["card"]["effort"] == "high"
        assert sc["card"]["impact"] == "low"
        stored = _task_on_disk(path, task_id)
        assert stored.title == "after"
        assert stored.effort == "high" and stored.impact == "low"
    finally:
        cleanup(directory)


def test_card_update_effort_impact_default_to_null_when_never_set():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        cards = anyio.run(_cards, build_server(_config(path)))
        card = next(c for c in cards if c["id"] == task_id)
        assert card["effort"] is None
        assert card["impact"] is None
    finally:
        cleanup(directory)


def test_card_update_effort_composes_with_title_in_same_patch():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="before")
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": task_id, "patch": {"title": "after", "effort": "med"},
             "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["title"] == "after"
        assert sc["card"]["effort"] == "med"
        stored = _task_on_disk(path, task_id)
        assert stored.title == "after" and stored.effort == "med"
    finally:
        cleanup(directory)


# ── card_move — spec shape { id, column_id, order, expected_version, force? } ─────
def test_card_move_is_free_across_non_adjacent_states_and_applies_order():
    directory, path = make_temp_db()
    try:
        # Seed in 'created'; jump straight to 'delivered' (skipping tiered/dispatched/
        # judged) — a NON-adjacent move that the free path must allow.
        _, task_id = _seed_task(path, state=State.CREATED)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"id": task_id, "column_id": "delivered", "order": "zzz", "expected_version": ev},
        )
        assert is_error is False
        assert sc["card"]["column_id"] == "delivered"   # the column moved, no gating
        assert sc["card"]["order"] == "zzz"             # the LexoRank order was applied
        assert _task_on_disk(path, task_id).state == "delivered"
    finally:
        cleanup(directory)


def test_card_move_bad_column_is_validation_failed():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, state=State.CREATED)
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"id": task_id, "column_id": "escalated", "order": "m", "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"        # not one of the six states
        assert _task_on_disk(path, task_id).state == "created"  # unchanged
    finally:
        cleanup(directory)


def test_card_move_unknown_id_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"id": "ghost", "column_id": "tiered", "order": "m", "expected_version": STALE},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
    finally:
        cleanup(directory)


def test_card_move_version_mismatch_is_conflict():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, state=State.CREATED)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"id": task_id, "column_id": "tiered", "order": "m", "expected_version": STALE},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        assert sc["meta"]["current"]["id"] == task_id
        assert sc["meta"]["current"]["column_id"] == "created"   # unchanged ground truth
        assert _task_on_disk(path, task_id).state == "created"   # no move happened
    finally:
        cleanup(directory)


def test_card_move_force_overrides_version_check():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, state=State.CREATED)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"id": task_id, "column_id": "judged", "order": "m", "expected_version": STALE,
             "force": True},
        )
        assert is_error is False
        assert sc["card"]["column_id"] == "judged"
        assert _task_on_disk(path, task_id).state == "judged"
    finally:
        cleanup(directory)


def test_card_move_on_tombstone_is_conflict():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, state=State.CREATED)
        _tombstone(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_move",
            {"id": task_id, "column_id": "tiered", "order": "m", "expected_version": STALE,
             "force": True},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        assert sc["meta"]["current"]["deleted_at"] is not None   # immutable tombstone
    finally:
        cleanup(directory)


# ── card_delete (SOFT delete) — spec shape { id, expected_version }, NO force ─────
def test_card_delete_returns_tombstone_card_and_retains_the_row_on_disk():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path, title="doomed")
        # Present on the board before deletion.
        assert task_id in {c["id"] for c in anyio.run(_cards, build_server(_config(path)))}

        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_delete",
            {"id": task_id, "expected_version": ev},
        )
        assert is_error is False
        # Returns the TOMBSTONE CARD (spec: card_delete → { card } (the tombstone)).
        card = sc["card"]
        assert card["id"] == task_id
        assert card["deleted_at"] is not None       # the tombstone marker
        assert card["title"] == "doomed"            # data retained in the card

        # Gone from the board / card_list snapshot…
        assert task_id not in {c["id"] for c in anyio.run(_cards, build_server(_config(path)))}
        # …but the ROW + its data PERSIST on disk as a tombstone (auditable/recoverable).
        stored = _task_on_disk(path, task_id)
        assert stored is not None
        assert stored.title == "doomed"            # data retained, not scrubbed
        assert stored.deleted_at is not None       # tombstoned, not hard-deleted
    finally:
        cleanup(directory)


def test_card_delete_unknown_id_is_not_found():
    directory, path = make_temp_db()
    try:
        _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_delete",
            {"id": "ghost", "expected_version": STALE},
        )
        assert is_error is True
        assert sc["code"] == "not_found"
    finally:
        cleanup(directory)


def test_card_delete_version_mismatch_is_conflict():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_delete",
            {"id": task_id, "expected_version": STALE},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        assert sc["meta"]["current"]["id"] == task_id
        assert sc["meta"]["current"]["deleted_at"] is None       # still live
        assert _task_on_disk(path, task_id).deleted_at is None    # not deleted
    finally:
        cleanup(directory)


def test_card_delete_on_tombstone_is_conflict():
    directory, path = make_temp_db()
    try:
        _, task_id = _seed_task(path)
        _tombstone(path, task_id)
        # Even with the CORRECT current version, re-deleting a tombstone is a conflict:
        # tombstones are immutable, and delete has no force.
        ev = _version_of(path, task_id)
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_delete",
            {"id": task_id, "expected_version": ev},
        )
        assert is_error is True
        assert sc["code"] == "conflict"
        assert sc["meta"]["current"]["deleted_at"] is not None
    finally:
        cleanup(directory)
