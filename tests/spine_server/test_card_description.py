"""The narrative ``description`` body + PRESERVE-AND-ROUND-TRIP of unmodeled foreign
keys on the card-write wire (spec v0.8.0).

Two contract fixes proven here at the wire boundary (same in-memory-client harness as
test_card_provenance):

  * ``description`` — the spec-conformant, agent-agnostic Markdown BODY. It is now a real
    modeled field: it persists and round-trips on create, is MUTABLE on update (set,
    change, and clear-via-null), absent means null (never a coerced ""), and an over-limit
    body is rejected LOUDLY naming the char limit (never silently truncated). This ends the
    old "projection emits constant ''" silent drop.
  * UNMODELED FOREIGN KEYS — a key the spine has no first-class Card field for is PRESERVED
    into ``Task.metadata`` and echoed on read, never flattened away (spec §Schema
    Versioning: unknown fields round-trip). card_update merges foreign keys (RFC 7386 —
    null removes). Over-limit foreign payloads reject loudly. Governance/extension keys are
    NOT client-preservable (a client cannot smuggle gate_status through metadata).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from spine import Spine, Store  # noqa: E402
from spine.entity import (  # noqa: E402
    MAX_DESCRIPTION_LEN,
    MAX_METADATA_BYTES,
    MAX_METADATA_DEPTH,
    MAX_METADATA_KEYS,
    MAX_METADATA_VALUE_LEN,
    Task,
)
from spine.projection import PROTECTED_CARD_KEYS, to_card  # noqa: E402
from spine_server.config import ServerConfig  # noqa: E402
from spine_server.server import build_server  # noqa: E402
from tests.spine_server._util import cleanup, make_temp_db  # noqa: E402


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


def _task_on_disk(path, task_id):
    with Store(path) as store:
        return store.tasks.get(task_id)


def _version_of(path, task_id):
    return _task_on_disk(path, task_id).version


def _create(path, card):
    return anyio.run(
        _call, build_server(_config(path)), "card_create",
        {"card": card, "project_id": _seed_project(path)},
    )


# ── description: create persistence + projection ──────────────────────────────
def test_card_create_description_persists_and_round_trips():
    directory, path = make_temp_db()
    try:
        body = "# Goal\n\nMake the widget **fast**.\n\n- step one\n- step two"
        is_error, sc = _create(path, {"title": "t", "description": body})
        assert is_error is False
        # Reaches the projected Card...
        assert sc["card"]["description"] == body
        # ...and is persisted on the Task entity.
        assert _task_on_disk(path, sc["card"]["id"]).description == body
    finally:
        cleanup(directory)


def test_card_create_absent_description_is_null_not_empty_string():
    """Omitted description → null (absent), NOT a coerced "". The absent/present
    distinction survives so a foreign client can tell "no body" from "empty body"."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t"})
        assert is_error is False
        assert sc["card"]["description"] is None
        assert _task_on_disk(path, sc["card"]["id"]).description is None
    finally:
        cleanup(directory)


def test_card_create_over_limit_description_rejects_loudly_naming_limit():
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "description": "x" * (MAX_DESCRIPTION_LEN + 1)})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert str(MAX_DESCRIPTION_LEN) in sc["message"] and "description" in sc["message"]
    finally:
        cleanup(directory)


def test_card_create_non_string_description_is_validation_failed():
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "description": {"not": "a string"}})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "description" in sc["message"]
    finally:
        cleanup(directory)


def test_card_create_max_length_description_is_accepted():
    """The cap bounds abuse, not a legitimately long body: exactly MAX is fine."""
    directory, path = make_temp_db()
    try:
        body = "y" * MAX_DESCRIPTION_LEN
        is_error, sc = _create(path, {"title": "t", "description": body})
        assert is_error is False
        assert _task_on_disk(path, sc["card"]["id"]).description == body
    finally:
        cleanup(directory)


# ── description: mutability on update (set / change / clear) ───────────────────
def test_card_update_description_is_mutable_and_clearable():
    directory, path = make_temp_db()
    try:
        _, created = _create(path, {"title": "t", "description": "first"})
        cid = created["card"]["id"]

        # CHANGE the body.
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"description": "second"}, "expected_version": _version_of(path, cid)},
        )
        assert is_error is False
        assert sc["card"]["description"] == "second"
        assert _task_on_disk(path, cid).description == "second"

        # CLEAR the body (present-null → key-presence clear, like effort/impact/due).
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"description": None}, "expected_version": _version_of(path, cid)},
        )
        assert is_error is False
        assert sc["card"]["description"] is None
        assert _task_on_disk(path, cid).description is None
    finally:
        cleanup(directory)


def test_card_update_over_limit_description_rejects_and_leaves_body_untouched():
    directory, path = make_temp_db()
    try:
        _, created = _create(path, {"title": "t", "description": "keep me"})
        cid = created["card"]["id"]
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"description": "z" * (MAX_DESCRIPTION_LEN + 1)},
             "expected_version": _version_of(path, cid)},
        )
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert _task_on_disk(path, cid).description == "keep me"  # rejected write changed nothing
    finally:
        cleanup(directory)


# ── preserve-and-round-trip: unmodeled foreign keys ───────────────────────────
def test_card_create_unknown_key_survives_and_is_echoed():
    """The headline contract: a foreign key the spine does not model survives create and is
    echoed on read — never silently discarded (forward-compat with a newer client)."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "x_from_v0_9": {"nested": [1, 2]}})
        assert is_error is False
        assert sc["card"]["x_from_v0_9"] == {"nested": [1, 2]}
        assert _task_on_disk(path, sc["card"]["id"]).metadata == {"x_from_v0_9": {"nested": [1, 2]}}
    finally:
        cleanup(directory)


def test_card_update_merges_and_removes_foreign_keys():
    directory, path = make_temp_db()
    try:
        _, created = _create(path, {"title": "t", "foo": "1", "bar": "2"})
        cid = created["card"]["id"]
        # Merge a new foreign key + change one; a null REMOVES a key (RFC 7386).
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"foo": "1-updated", "baz": "3", "bar": None},
             "expected_version": _version_of(path, cid)},
        )
        assert is_error is False
        assert sc["card"]["foo"] == "1-updated" and sc["card"]["baz"] == "3"
        assert "bar" not in sc["card"]
        assert _task_on_disk(path, cid).metadata == {"foo": "1-updated", "baz": "3"}
    finally:
        cleanup(directory)


def test_card_create_over_limit_foreign_metadata_rejects_loudly():
    directory, path = make_temp_db()
    try:
        # Over the total-bytes backstop while UNDER key-count and per-value caps — proving
        # the byte TOTAL is the binding guard (not merely the per-value cap, which no single
        # value can exceed anyway). 20 keys × 2000 chars ≈ 40 KB > the 32 KiB total.
        bulky = {f"k{i}": "v" * 2000 for i in range(20)}
        is_error, sc = _create(path, {"title": "t", **bulky})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "metadata serialized size exceeds cap" in sc["message"]
        assert 20 <= MAX_METADATA_KEYS and 2000 <= MAX_METADATA_VALUE_LEN  # under both other caps
        assert 20 * 2000 > MAX_METADATA_BYTES                              # yet over the total
    finally:
        cleanup(directory)


def test_card_create_too_many_foreign_keys_rejects_loudly():
    directory, path = make_temp_db()
    try:
        too_many = {f"k{i}": "v" for i in range(MAX_METADATA_KEYS + 1)}
        is_error, sc = _create(path, {"title": "t", **too_many})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "metadata carries too many keys" in sc["message"]
    finally:
        cleanup(directory)


def test_client_cannot_smuggle_extension_field_via_metadata():
    """A governance/extension key (gate_status, badge) is part of the known Card surface,
    so it is NOT preserved as foreign metadata: the projection re-emits the SERVER's value.
    A client cannot overwrite it — the metadata boundary excludes CARD_FIELD_KEYS."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "gate_status": "HACKED", "badge": {"x": 1}})
        assert is_error is False
        assert sc["card"]["gate_status"] == "COMMITTED"   # server value, not the client's
        assert sc["card"]["badge"] is None                # no escalation → server's null
        assert _task_on_disk(path, sc["card"]["id"]).metadata == {}  # neither was preserved
    finally:
        cleanup(directory)


def test_unmodeled_known_card_fields_round_trip():
    """RESOLVED v0.8.0 (was ``..._keep_documented_defaults``): priority/checklist/attachments
    are spec-DEFINED Card fields the spine does not model as first-class columns. The old scope
    DROPPED a client's value for them (projecting a documented default) while PRESERVING a random
    foreign key — punishing spec compliance, rewarding deviation. They now route through the SAME
    preservation path (``Task.metadata``) as an unknown foreign key and round-trip on read; only
    when the client sends none does ``to_card`` emit the documented default. See
    test_spec_divergences.py::test_priority_checklist_attachments_round_trip for the register cross-ref."""
    directory, path = make_temp_db()
    try:
        checklist = [{"text": "a", "done": False}, {"text": "b", "done": True}]
        attachments = [{"id": "att-1", "ref": "s3://bucket/x"}]
        is_error, sc = _create(path, {
            "title": "t", "priority": "high", "checklist": checklist, "attachments": attachments,
        })
        assert is_error is False
        assert sc["card"]["priority"] == "high"        # the client's value, no longer dropped to "med"
        assert sc["card"]["checklist"] == checklist
        assert sc["card"]["attachments"] == attachments
        # stored in metadata (the preservation path), NOT as a first-class Task column
        assert _task_on_disk(path, sc["card"]["id"]).metadata == {
            "priority": "high", "checklist": checklist, "attachments": attachments,
        }
    finally:
        cleanup(directory)


def test_unmodeled_known_card_fields_default_when_absent():
    """The other half of the round-trip: when the client sends none of the trio, ``to_card``
    still emits the documented default (``priority: "med"``, empty collections) and metadata
    stays empty — the default projection is unchanged for the absent case."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t"})
        assert is_error is False
        assert sc["card"]["priority"] == "med"
        assert sc["card"]["checklist"] == []
        assert sc["card"]["attachments"] == []
        assert _task_on_disk(path, sc["card"]["id"]).metadata == {}
    finally:
        cleanup(directory)


def test_unmodeled_known_card_fields_survive_update_round_trip():
    """Each of the trio survives a ``card_update`` round trip (RFC 7386 merge through metadata),
    exactly like a foreign key: set priority on create, then change it + add a checklist on
    update, and read both back."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "priority": "low"})
        assert is_error is False and sc["card"]["priority"] == "low"
        cid = sc["card"]["id"]
        is_error, sc = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"priority": "high", "checklist": [{"text": "ship", "done": False}]},
             "expected_version": _version_of(path, cid)},
        )
        assert is_error is False
        assert sc["card"]["priority"] == "high"
        assert sc["card"]["checklist"] == [{"text": "ship", "done": False}]
    finally:
        cleanup(directory)


def test_unmodeled_known_card_fields_cannot_clobber_a_modeled_field():
    """The clobber guard still holds AFTER the trio was subtracted from the protected set: a
    preserved key can override a known-but-unmodeled default (priority) but NEVER a modeled/
    authority/extension field. gate_status stays the server's COMMITTED even alongside a
    round-tripping priority."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "priority": "high", "gate_status": "HACKED"})
        assert is_error is False
        assert sc["card"]["priority"] == "high"          # unmodeled field overrides its default
        assert sc["card"]["gate_status"] == "COMMITTED"  # protected field stays server-owned
    finally:
        cleanup(directory)


def test_realistic_checklist_and_attachments_payload_passes_comfortably():
    """A realistic Card body — a few-dozen-item checklist + an attachments list — passes the
    RAISED metadata budget comfortably (the payload the caps were sized against). Proves the
    budget fits real collections, not just stray scalars."""
    directory, path = make_temp_db()
    try:
        checklist = [{"text": f"task item number {i} — do the thing", "done": i % 2 == 0}
                     for i in range(40)]
        attachments = [{"id": f"att-{i}", "ref": f"https://storage.example.com/blobs/{i:04d}"}
                       for i in range(15)]
        is_error, sc = _create(path, {
            "title": "t", "priority": "high", "checklist": checklist, "attachments": attachments,
        })
        assert is_error is False, sc
        assert len(sc["card"]["checklist"]) == 40 and len(sc["card"]["attachments"]) == 15
        assert sc["card"]["priority"] == "high"
    finally:
        cleanup(directory)


def test_metadata_budget_is_uncoupled_from_the_provenance_budget():
    """Finding 3: the metadata caps are DEFINED INDEPENDENTLY of the provenance caps (no longer
    aliased) and RAISED. This pins the uncoupling — the two budgets must NOT be equal — and
    proves the provenance caps did not move (they keep their pinned contract numbers)."""
    from spine.entity import (
        MAX_CREATED_BY_BYTES,
        MAX_METADATA_DEPTH,
        MAX_PROVENANCE_DEPTH,
        MAX_PROVENANCE_KEYS,
        MAX_PROVENANCE_VALUE_LEN,
    )
    # metadata is strictly larger on the size axes that matter for collections
    assert MAX_METADATA_BYTES == 32768 and MAX_METADATA_BYTES > MAX_CREATED_BY_BYTES
    assert MAX_METADATA_KEYS == 24 and MAX_METADATA_KEYS > MAX_PROVENANCE_KEYS
    assert MAX_METADATA_VALUE_LEN == 2048 and MAX_METADATA_VALUE_LEN > MAX_PROVENANCE_VALUE_LEN
    assert MAX_METADATA_DEPTH == 4 and MAX_METADATA_DEPTH > MAX_PROVENANCE_DEPTH
    # provenance caps UNMOVED (their pinned contract numbers)
    assert MAX_PROVENANCE_KEYS == 12 and MAX_PROVENANCE_VALUE_LEN == 512
    assert MAX_PROVENANCE_DEPTH == 3 and MAX_CREATED_BY_BYTES == 4096


# ── FIX 1 (round 2): the granular metadata caps are enforced RECURSIVELY, at every depth ──
# The old checks applied the per-string-length and per-object-key-count caps at the TOP LEVEL
# ONLY. Now that ``checklist``/``attachments`` route through metadata, nested strings/objects are
# the NORMAL case — an over-length string INSIDE an array, or an over-keyed NESTED object, sailed
# past the granular caps and hit only the 32 KiB byte backstop (a vague error, not the specific
# limit). These red-on-violation tests pin the recursive enforcement + the named locus.
def test_metadata_over_length_string_nested_in_an_array_rejects_naming_per_value_cap():
    """A 30 KB-class string nested inside a ``checklist`` item's ``text`` — NOT a top-level
    string value — must reject naming the PER-VALUE char cap (not the vague byte backstop),
    with its locus. Proves the value cap now reaches into arrays/objects."""
    directory, path = make_temp_db()
    try:
        oversized = "x" * (MAX_METADATA_VALUE_LEN + 1)  # ~2 KB — well under the 32 KiB byte total
        is_error, sc = _create(path, {
            "title": "t", "checklist": [{"text": oversized, "done": False}],
        })
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "string value too long" in sc["message"]           # the per-value cap, named
        assert "char max" in sc["message"]
        assert "metadata.checklist[0].text" in sc["message"]       # locus: act on THIS node
        # red-contrast: the message is NOT the byte backstop (which would be a vague "too big")
        assert "serialized size exceeds cap" not in sc["message"]
    finally:
        cleanup(directory)


def test_metadata_over_keyed_nested_object_rejects_naming_key_cap():
    """An object nested one level down, stuffed with more than ``MAX_METADATA_KEYS`` keys, must
    reject naming the PER-OBJECT key cap with its locus — proving the key-count cap is per-object
    at every depth, not top-level-only (the 500-keys-in-one-value hole is closed). The top-level
    metadata has ONE key here, so a top-level-only check would have missed this entirely."""
    directory, path = make_temp_db()
    try:
        wide_nested = {f"k{i}": "v" for i in range(MAX_METADATA_KEYS + 1)}
        is_error, sc = _create(path, {"title": "t", "vendor_blob": wide_nested})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "carries too many keys" in sc["message"]            # the key cap, named
        assert "metadata.vendor_blob" in sc["message"]             # locus: the nested object
        assert "serialized size exceeds cap" not in sc["message"]  # not the vague byte backstop
    finally:
        cleanup(directory)


def test_metadata_over_depth_nesting_rejects_naming_depth_and_locus():
    """Depth (4) is still enforced — and now names the locus. A container nested one past the
    cap rejects naming the depth limit and the path to the offending node."""
    directory, path = make_temp_db()
    try:
        # metadata(1) → a(2) → b(3) → c(4) → d(5): the dict at ``d`` sits one past the depth cap.
        over_deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
        is_error, sc = _create(path, {"title": "t", "deep": over_deep})
        assert is_error is True
        assert sc["code"] == "validation_failed"
        assert "nests deeper than the allowed limit" in sc["message"]
        assert MAX_METADATA_DEPTH == 4
    finally:
        cleanup(directory)


def test_realistic_checklist_and_attachments_pass_the_recursive_caps():
    """The recursive enforcement must NOT punish a realistic Card body: a ~40-item checklist +
    ~15-item attachments — nested strings and objects throughout — passes comfortably. This is
    the complement to the red tests above: the walk rejects abuse, not legitimate collections."""
    directory, path = make_temp_db()
    try:
        checklist = [{"text": f"acceptance item {i}: verify the {i}th behaviour end to end",
                      "done": i % 3 == 0} for i in range(40)]
        attachments = [{"id": f"att-{i:03d}", "ref": f"https://storage.example.com/blobs/{i:05d}"}
                       for i in range(15)]
        is_error, sc = _create(path, {
            "title": "t", "priority": "high", "checklist": checklist, "attachments": attachments,
        })
        assert is_error is False, sc
        assert len(sc["card"]["checklist"]) == 40 and len(sc["card"]["attachments"]) == 15
    finally:
        cleanup(directory)


# ── ITEM A (round 2): the MANDATORY version guard makes whole-value array replacement safe ──
# RFC 7386 replaces an array metadata value wholesale (checklist/attachments), so two clients
# editing the same checklist could clobber each other — EXCEPT that `expected_version` is
# REQUIRED on card_update (verified in code AND spec v0.8.0), so concurrent staleness surfaces as
# `conflict`, never a silent last-writer-wins.
def test_two_updates_sharing_one_expected_version_second_is_conflict_not_clobber():
    """Two clients holding the SAME expected_version each replace the checklist. The first lands
    and mints a new version; the second — still presenting the now-stale version — is rejected
    with `conflict` (meta.current = the freshly-written card), NOT a silent clobber. This is the
    concurrency guarantee that makes array-valued metadata edits safe between conforming clients."""
    directory, path = make_temp_db()
    try:
        is_error, sc = _create(path, {"title": "t", "checklist": [{"text": "a", "done": False}]})
        assert is_error is False
        cid = sc["card"]["id"]
        shared_ev = _version_of(path, cid)  # both "clients" read the same version token
        # Client 1 replaces the checklist wholesale — lands, mints a new version.
        is_error, sc1 = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"checklist": [{"text": "client-1", "done": True}]},
             "expected_version": shared_ev},
        )
        assert is_error is False
        assert sc1["card"]["checklist"] == [{"text": "client-1", "done": True}]
        # Client 2 replaces it too, presenting the SAME (now stale) expected_version → conflict.
        is_error, sc2 = anyio.run(
            _call, build_server(_config(path)), "card_update",
            {"id": cid, "patch": {"checklist": [{"text": "client-2", "done": False}]},
             "expected_version": shared_ev},
        )
        assert is_error is True
        assert sc2["code"] == "conflict"
        assert sc2["meta"]["current"]["id"] == cid
        # client 1's write STANDS; client 2 did not silently overwrite it.
        assert _task_on_disk(path, cid).metadata["checklist"] == [{"text": "client-1", "done": True}]
    finally:
        cleanup(directory)


# ── ITEM B (round 2): field-graduation guard + scrub ─────────────────────────────────────────
# When a field that round-tripped through metadata GRADUATES to a first-class modeled column, the
# projection overlay guard makes shadowing structurally impossible on READ (modeled value wins),
# and the write path SCRUBS the stale metadata twin on the next write of the entity.
def test_graduated_field_modeled_value_wins_on_read_over_a_stale_metadata_twin():
    """GUARD: the projection overlay never lets a preserved metadata key clobber a modeled/
    authority field (a key in ``PROTECTED_CARD_KEYS``). This is what makes graduation safe on
    READ — a stale metadata twin of a now-modeled field is SUPPRESSED and the modeled value wins.
    Simulated with ``description``/``title`` (real modeled, protected fields standing in for a
    just-graduated one); a genuinely-foreign key still round-trips."""
    task = Task(
        id="t1", project_id="p", title="real title", description="the real body",
        metadata={"description": "STALE SHADOW", "title": "SHADOW TITLE", "x_foreign": "kept"},
    )
    card = to_card(task)
    assert "description" in PROTECTED_CARD_KEYS and "title" in PROTECTED_CARD_KEYS
    assert card["description"] == "the real body"  # modeled value wins over the metadata twin
    assert card["title"] == "real title"           # ditto — shadow suppressed
    assert card["x_foreign"] == "kept"             # a genuinely-foreign key still round-trips


def test_graduation_scrub_drops_stale_metadata_twin_on_next_write():
    """SCRUB: a stale metadata twin of a now-modeled field is dropped on the NEXT WRITE of the
    entity — even an UNRELATED edit (title only, no metadata patch). Seeds pre-graduation residue
    (a protected-key twin sitting in stored metadata) directly, then edits the title; the twin is
    gone afterward while a genuinely-foreign key is untouched."""
    directory, path = make_temp_db()
    try:
        spine = Spine(Store(path))
        pid = spine.create_project("p").id
        t = spine.create_task(pid, "orig title")
        # Simulate PRE-GRADUATION residue: write a protected-key twin straight into stored
        # metadata (the wire boundary would never admit one — this is legacy residue).
        t.metadata = {"description": "STALE SHADOW", "keepme": "foreign"}
        spine.store.tasks.put(t)
        ev = spine.store.tasks.get(t.id).version
        # An unrelated edit (title only) still scrubs on write.
        spine.update_task(t.id, title="new title", expected_version=ev)
        stored = spine.store.tasks.get(t.id)
        assert "description" not in stored.metadata     # stale twin scrubbed on the next write
        assert stored.metadata == {"keepme": "foreign"}  # genuinely-foreign key untouched
        spine.store.close()
    finally:
        cleanup(directory)
