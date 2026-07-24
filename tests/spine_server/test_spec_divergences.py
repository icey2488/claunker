"""ENFORCEMENT for the Kanbantt MCP Spec Divergence Register.

The register (``claunker-ops/docs/SPEC-DIVERGENCES.md``) lists every place the reference
spine diverges from the canonical spec. A markdown file is a graveyard for good intentions:
if a divergence isn't pinned by a test asserting the gap EXISTS, the register is a confession,
not a control. This file is that control — the project's own epistemology (proofs-by-absence,
red-on-violation) applied to its own documentation.

THE LOAD-BEARING PROPERTY: each test below asserts that a CURRENT divergence still holds. When
someone later CLOSES a divergence (models ``priority`` natively, stamps ``updated_at``, lands the
gate path, …) its test goes RED — forcing them to update the register in the same change. The
documentation is self-invalidating: it cannot silently drift out of date, because drift breaks CI.

CROSS-REFERENCE: every test names its register entry id (``Register #N``); the register entries
name their test back (both directions). Entries RESOLVED by the v0.8.0 card-body work have their
assertions INVERTED here — they now assert the CORRECT behavior, so re-opening the bug goes red too.

Register entry coverage (12 entries):
  * #1  acceptance_criteria extension ............ asserted (round-trips)
  * #2  priority/checklist/attachments ........... INVERTED — RESOLVED v0.8.0 (now round-trips)
  * #3  created_by render gate .................... NOT spine-testable — board-enforced (see note)
  * #4  wire-mint identity re-stamp ............... asserted (gap)
  * #5  updated_at / updated_by always null ....... asserted (gap)
  * #6  card_list updated_since ignored ........... asserted (gap)
  * #7  gate_status hardcoded COMMITTED ........... asserted (gap)
  * #8  tier as a tag, not a native field ......... asserted (gap)
  * #9  Card carries no project_id ................ asserted (gap)
  * #10 acceptance_criteria "" default when absent  asserted (gap)
  * #11 description projects null when unset ...... INVERTED — RESOLVED v0.8.0 (real body, not "")
  * #12 unmodeled foreign keys preserved .......... INVERTED — RESOLVED v0.8.0 (round-trips)
⇒ 11 entries carry an executable assertion; 1 (#3) is declared board-enforced, not faked here.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import Spine, Store  # noqa: E402
from spine_server.cards import list_cards  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import CARD_CREATE_ACTOR, build_server  # noqa: E402
from tests.spine_server._util import BIG, cleanup, make_temp_db, seed  # noqa: E402


def _config(path, **overrides):
    return ServerConfig(token="test-token", db_path=path, enable_dns_rebinding_protection=False, **overrides)


def _seed_project(path):
    spine = Spine(Store(path))
    try:
        return spine.create_project("p").id
    finally:
        spine.store.close()


async def _call(server, name, arguments):
    async with connect(server) as client:
        await client.initialize()
        result = await client.call_tool(name, arguments)
        return result.isError, result.structuredContent


def _create(path, card):
    return anyio.run(
        _call, build_server(_config(path)), "card_create",
        {"card": card, "project_id": _seed_project(path)},
    )


# ── #1 · acceptance_criteria is a modeled Claunker EXTENSION (INTENTIONAL & PERMANENT) ──
def test_divergence_1_acceptance_criteria_is_modeled_and_round_trips():
    """Register #1. The spine models ``acceptance_criteria`` first-class (a named extension with
    a real judge/framing consumer) rather than treating it as an opaque unknown field. Goes RED
    if the spine ever stops storing/echoing it."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "acceptance_criteria": "done when the suite is green"})
        assert is_error is False
        assert sc["card"]["acceptance_criteria"] == "done when the suite is green"
    finally:
        cleanup(directory)


# ── #2 · priority/checklist/attachments — RESOLVED v0.8.0 (INVERTED assertion) ──────────
def test_divergence_2_priority_checklist_attachments_round_trip():
    """Register #2 — RESOLVED v0.8.0 (was KNOWN GAP: documented-default drop). These spec-defined-
    but-unmodeled Card fields now route through the preservation path and round-trip. INVERTED: it
    asserts the CORRECT (resolved) behavior, so re-introducing the silent-default drop goes RED.
    (Fuller round-trip / update / clobber coverage: test_card_description.py.)"""
    directory, path = make_temp_db()
    try:
        checklist = [{"text": "a", "done": False}]
        attachments = [{"id": "att-1", "ref": "s3://b/x"}]
        is_error, sc = _create(path, {
            "title": "t", "priority": "high", "checklist": checklist, "attachments": attachments,
        })
        assert is_error is False
        assert sc["card"]["priority"] == "high"          # NOT dropped to the "med" default
        assert sc["card"]["checklist"] == checklist
        assert sc["card"]["attachments"] == attachments
    finally:
        cleanup(directory)


# ── #3 · created_by render gate — NOT spine-testable (declared board-enforced) ──────────
# Register #3 (INTENTIONAL & PERMANENT) is a BOARD RENDERING rule: dispatch provenance renders
# ONLY when ``created_by.type === "agent"`` (``readProvenance``/``hasProvenance`` in the board).
# It is a pure presentation gate with no spine-observable behavior, so per the register's own
# "say so rather than fake a test" clause it is NOT asserted here. It is enforced board-side in
# kanbantt-app (the provenance-chip unit tests); the spine-side PRECONDITION that makes it matter
# — a wire mint yields a ``type:"human"`` identity — IS asserted, as divergence #4 below.


# ── #4 · a wire card_create cannot mint an agent-typed (renderable) stamp (KNOWN GAP) ───
def test_divergence_4_wire_mint_restamps_identity_from_credential():
    """Register #4. Identity is authority-owned: a wire ``card_create`` re-stamps ``type``/``id``
    from the single operator credential (``CARD_CREATE_ACTOR`` = human/operator), IGNORING a
    client-claimed agent identity — so a wire mint cannot produce an agent-typed (renderable)
    stamp today. Descriptive provenance (``model``) still merges. Goes RED when per-agent
    credentials land and a wire mint can carry ``type:"agent"``."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {
            "title": "t",
            "created_by": {"type": "agent", "id": "sneaky-agent", "model": "claude-x"},
        })
        assert is_error is False
        cb = sc["card"]["created_by"]
        assert cb["type"] == CARD_CREATE_ACTOR["type"] == "human"   # re-stamped, not the claim
        assert cb["id"] == CARD_CREATE_ACTOR["id"] == "operator"
        assert cb["model"] == "claude-x"                            # provenance merged, not authority
    finally:
        cleanup(directory)


# ── #5 · updated_at / updated_by are always null (KNOWN GAP) ────────────────────────────
def test_divergence_5_updated_at_and_updated_by_are_always_null():
    """Register #5. v1 tracks no per-entity update stamp, so every projected Card carries
    ``updated_at``/``updated_by`` = null unconditionally. Goes RED when an update stamp lands."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t"})
        assert is_error is False
        assert sc["card"]["updated_at"] is None
        assert sc["card"]["updated_by"] is None
    finally:
        cleanup(directory)


# ── #6 · card_list updated_since is accepted but ignored (KNOWN GAP) ─────────────────────
def test_divergence_6_updated_since_is_accepted_but_returns_full_snapshot():
    """Register #6. ``updated_since`` is a documented seam: v1 ALWAYS returns a full authoritative
    snapshot, never a delta. A far-future ``updated_since`` still returns every live card. Goes RED
    when real delta narrowing lands."""
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}, {"title": "b"}, {"title": "c"}])
        full = list_cards(path, max_bytes=BIG)
        delta = list_cards(path, updated_since="2099-01-01T00:00:00+00:00", max_bytes=BIG)
        assert len(full["cards"]) == 3
        assert {c["id"] for c in delta["cards"]} == {c["id"] for c in full["cards"]}  # ignored
    finally:
        cleanup(directory)


# ── #7 · gate_status is hardcoded "COMMITTED" (KNOWN GAP) ────────────────────────────────
def test_divergence_7_gate_status_is_hardcoded_committed():
    """Register #7. Every projected Card carries ``gate_status: "COMMITTED"`` unconditionally (the
    write/gate path is a later slice). Goes RED when a real gate state drives it."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t"})
        assert is_error is False
        assert sc["card"]["gate_status"] == "COMMITTED"
    finally:
        cleanup(directory)


# ── #8 · tier lives as a "tier:N" tag, not a native Card field (INTENTIONAL & PERMANENT) ─
def test_divergence_8_tier_projects_as_a_tag_not_a_native_field():
    """Register #8. Tier is projected into the ``tags`` array as ``tier:N``; the Card has NO native
    ``tier`` field. Goes RED if a native ``tier`` Card field is ever added."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "tier": 2})
        assert is_error is False
        assert "tier:2" in sc["card"]["tags"]
        assert "tier" not in sc["card"]      # no native Card field
    finally:
        cleanup(directory)


# ── #9 · the projected Card carries no project_id (INTENTIONAL & PERMANENT) ──────────────
def test_divergence_9_projected_card_has_no_project_id():
    """Register #9. ``project_id`` is a create-time targeting sibling of CardInput, NOT a Card
    field — the projected Card is project-agnostic. Goes RED if ``project_id`` is ever projected."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t"})
        assert is_error is False
        assert "project_id" not in sc["card"]
    finally:
        cleanup(directory)


def test_divergence_9_listed_cards_have_no_project_id():
    """Register #9 (list-endpoint mirror). ``project_id`` must not leak onto any card projected
    by ``card_list`` either — not just the create-time echo. Goes RED if ``project_id`` is ever
    projected onto a listed card."""
    directory, path = make_temp_db()
    try:
        seed(path, [{"title": "a"}, {"title": "b"}])
        listed = list_cards(path, max_bytes=BIG)
        assert len(listed["cards"]) == 2
        for card in listed["cards"]:
            assert "project_id" not in card
    finally:
        cleanup(directory)


# ── #10 · card_create stores acceptance_criteria "" when absent (KNOWN GAP, minor) ───────
def test_divergence_10_absent_acceptance_criteria_coerces_to_empty_string():
    """Register #10. A wire ``card_create`` with no ``acceptance_criteria`` coerces it to ``""``
    (not ``null``) — a pre-existing quirk left untouched by the v0.8.0 scope (which fixed the same
    class of bug for ``description``). Goes RED when it is read verbatim (absent → null)."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t"})
        assert is_error is False
        assert sc["card"]["acceptance_criteria"] == ""   # NOT None — the documented quirk
    finally:
        cleanup(directory)


# ── #11 · description projects the real body / null when unset — RESOLVED (INVERTED) ─────
def test_divergence_11_description_is_the_real_body_not_a_constant_empty_string():
    """Register #11 — RESOLVED v0.8.0 (was a constant-`""` silent drop). INVERTED: absent →
    ``null`` (the absent/present distinction survives; NOT coerced to ``""``), and a supplied body
    is echoed verbatim. Re-introducing the constant-`""` drop goes RED."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t"})
        assert is_error is False
        assert sc["card"]["description"] is None          # null when unset, NOT ""
        is_error, sc = _create(path, {"title": "t2", "description": "# Real body"})
        assert is_error is False
        assert sc["card"]["description"] == "# Real body"  # the real value, not a constant
    finally:
        cleanup(directory)


# ── #12 · unmodeled foreign keys are preserved and round-tripped — RESOLVED (INVERTED) ──
def test_divergence_12_unmodeled_foreign_keys_are_preserved():
    """Register #12 — RESOLVED v0.8.0 (were flattened away). INVERTED: a genuinely-foreign key
    survives create + read (preserve-and-round-trip). Re-introducing the flatten-away drop goes
    RED."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "x_custom_priority": {"weight": 7}})
        assert is_error is False
        assert sc["card"]["x_custom_priority"] == {"weight": 7}
    finally:
        cleanup(directory)
