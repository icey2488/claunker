"""FastMCP wiring + the Starlette ASGI app.

Advertises ELEVEN tools — the read-only mirror pair (``board_get``, ``card_list``),
the project-targeting read (``project_list`` — the enumeration ``card_create``'s
targeting rides on), the one human-gated escalation control (``escalation_resolve``),
the FIVE operator card-write tools (``card_create``, ``card_update``, ``card_move``,
``card_delete``, ``card_retier``), and the governed archive pair (``card_archive``,
``card_unarchive``). With the card_* write path advertised the board is
no longer a read-only mirror: Kanbantt gates features on advertised tool NAMES, so this
surface lets Kanbantt's ``canWrite``, ``canRetier``, ``canArchive``, and
``canTargetProjects`` flip (``canRetier`` derives true iff ``card_retier`` is present;
``canArchive`` iff ``card_archive`` is; ``canTargetProjects`` iff ``project_list``
is). Escalations still render from the per-card badge that
rides in ``card_list`` (no ``escalation_list`` is advertised; see the
asymmetric-advertising note on ``escalation_resolve`` below).

Three write-path stances coexist here, DELIBERATELY asymmetric:

  * ``escalation_resolve`` is GOVERNED + HUMAN-GATED — a human control override. The
    actor is derived from the AUTHENTICATED CREDENTIAL, never the payload, and is
    re-asserted operator-only at the Spine (see the actor-invariant comment on the tool).
  * ``card_create`` / ``card_update`` / ``card_move`` / ``card_delete`` are UNGOVERNED
    operator edits — direct state/field puts with NO transition-legality check and NO
    actor field. The operator is the tier-4 human; these tools are the operator's hand,
    authenticated by the single Bearer token (the token IS the attribution — light
    transport-level only). ``card_delete`` is a SOFT delete (a recoverable tombstone;
    the row is retained, hidden from the board).
  * ``card_retier`` is GOVERNED but NOT human-gated — AUDITED. It changes an
    already-set tier (the field with a control gradient) only via an append-only
    ``tier_audit`` row written ATOMICALLY with the change, takes NO force, and records
    a (placeholder) actor; ``card_update`` enforces the matching WRITE-ONCE tier guard
    so a set tier cannot be changed off this audited path.
  * ``card_archive`` / ``card_unarchive`` follow ``card_retier``'s governed stance
    verbatim: audited (one append-only ``archive_audit`` row, atomic with the flag),
    NO force, placeholder actor. Two extra rules of their own: LOUD idempotency
    (archiving an already-archived card, or unarchiving a non-archived one, is
    validation_failed — healthy and broken must not emit the same signal), and the
    ESCALATION GATE (a card with an open escalation cannot be archived; unarchive is
    ungated). The governed *agent* write path is a separate v2 concern.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from spine import ConflictError, Spine, Store, project

# Drive backup singleton — DORMANT until SA key is present (set by main()).
_backup: Optional[Any] = None


def _set_backup(backup: Any) -> None:
    global _backup
    _backup = backup


def _notify_dirty() -> None:
    if _backup is not None:
        _backup.mark_dirty()

from .board import TIER_TAG_PREFIX, build_board
from .cards import PayloadTooLarge, list_cards, tombstone_card
from .config import STREAMABLE_HTTP_PATH, ServerConfig, from_env
from .http import BearerAuthMiddleware, CORSMiddleware
from .result import (
    CONFLICT,
    NOT_FOUND,
    PAYLOAD_TOO_LARGE,
    UNAUTHORIZED,
    VALIDATION_FAILED,
    domain_error_result,
    ok_result,
)

# serverInfo (arrives in the ``initialize`` handshake; Kanbantt shows it as
# "MCP: Claunker"). The version is this server's own version, aligned to the Kanbantt
# MCP spec version it implements (0.7.0 — dispatch provenance inside created_by, on top
# of v0.6.x project_list + CardInput-shaped card_create) and still distinct from the
# data schema version (1: provenance is additive-optional, no blob shape change).
SERVER_NAME = "Claunker"
SERVER_VERSION = "0.7.0"

# Actor stamped as ``created_by`` on every card_create write. Derived from the
# AUTHENTICATED CREDENTIAL, never the payload (the escalation_resolve actor stance
# applied to attribution): the server authenticates exactly one Bearer token — the
# operator's — so the creating actor is, by construction, the human operator. A
# ``created_by`` riding in CardInput is an authority-owned field and is IGNORED per
# spec §Create ("supplied by a client MUST be ignored, not errored"). At Stage 2
# (per-user credentials) derive the real identity here.
CARD_CREATE_ACTOR = {"type": "human", "id": "operator"}


def _created_by_with_provenance(client_created_by: Any) -> Dict[str, Any]:
    """Build the ``created_by`` stamp for a wire ``card_create``.

    Splits ``created_by`` into two halves with DIFFERENT trust models:

      * IDENTITY (``type`` + ``id``) is AUTHORITY-OWNED — always the authenticated
        credential (``CARD_CREATE_ACTOR``), NEVER the client payload. This is the
        anti-spoof invariant (spec §Create: authority-owned fields supplied by a client
        MUST be ignored, not errored): a caller cannot claim a different actor.
      * DISPATCH PROVENANCE (``model``/``effort``/``job_id`` + any other non-identity
        keys) is DESCRIPTIVE metadata the minting client legitimately owns — it says
        HOW the card was produced and carries NO authority — so it is READ from the
        client's ``created_by`` and MERGED onto the credential identity.

    A client that sends no ``created_by`` (human intake), or one carrying only
    ``type``/``id``, yields exactly ``CARD_CREATE_ACTOR`` (no provenance → no chip on
    the board). Unknown non-identity keys pass through (interop; ``created_by`` is
    additive-optional). The merged shape is re-validated downstream: the Task constructor
    rejects any non-string provenance value (closing the nested-object/array hole, not
    just the modeled keys), and ``check_created_by_limits`` bounds the payload size at the
    create boundary — both surface as ``validation_failed``.
    """
    identity = dict(CARD_CREATE_ACTOR)
    if not isinstance(client_created_by, dict):
        return identity
    provenance = {k: v for k, v in client_created_by.items() if k not in ("type", "id")}
    # Identity always WINS the merge (anti-spoof); provenance never overrides type/id.
    return {**provenance, **identity}


def _patch_tier_to_int(value: Any) -> int:
    """Map a ``card_update`` ``patch.tier`` wire value to the facade's internal int
    domain. The CANONICAL wire form is the tag-id STRING the projection emits into
    ``Card.tags`` and the board declares — ``"tier:N"`` (see
    ``spine.projection._tags_for`` / ``board.tier_tag_id``); the spec Card has no
    native ``tier`` field, so tier lives in ``tags`` and an operator edit round-trips
    that EXACT representation. A bare int is also accepted, a tolerance for non-Kanbantt
    MCP callers (and symmetry with ``card_create``'s int ``tier``). The 1..4 RANGE is
    enforced downstream by ``Spine.update_task`` (one source of truth); a value not
    parseable to an int here raises ``ValueError`` → ``validation_failed``."""
    # bool is an int subclass — reject True/False, which are never a tier.
    if isinstance(value, bool):
        raise ValueError(f"tier must be the tag-id string 'tier:N' or an int 1..4, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.startswith(TIER_TAG_PREFIX):
        suffix = value[len(TIER_TAG_PREFIX):]
        if suffix.isdigit():
            return int(suffix)
    raise ValueError(f"tier must be the tag-id string 'tier:N' or an int 1..4, got {value!r}")


def _tier_from_card_input(card: Dict[str, Any]) -> Optional[int]:
    """Resolve the OPTIONAL tier a ``card_create`` CardInput carries, or ``None`` —
    the UNTIERED default. The spec Card has no native ``tier`` field (tier lives in
    ``tags`` as the projection's ``"tier:N"``), so the canonical carrier is a tier
    tag; a native ``tier`` key is also accepted (the ``card_update`` patch tolerance,
    via the same ``_patch_tier_to_int`` seam) and WINS when present. Non-tier tags
    are ignored — the spine's ``tags`` carry only tier. A malformed tier value or a
    non-list ``tags`` raises ``ValueError`` (→ ``validation_failed``); the 1..4 range
    is the caller's check. NO DEFAULTING TO A TIER: an input carrying neither form is
    genuinely untiered — board-created cards are human intake, and classification is
    a later, separate rung (the old int ``tier=1`` default silently pre-classified
    every create; that is exactly what this resolver retires)."""
    if "tier" in card and card["tier"] is not None:
        return _patch_tier_to_int(card["tier"])
    tags = card.get("tags")
    if tags is None:
        return None
    if not isinstance(tags, list):
        raise ValueError(f"tags must be a list of tag-id strings, got {tags!r}")
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(TIER_TAG_PREFIX):
            return _patch_tier_to_int(tag)
    return None


def _conflict_result(current, store: Store) -> types.CallToolResult:
    """Build the spec's ``conflict`` envelope (spec §Concurrency) for a card-write that
    lost the optimistic-concurrency check OR targeted an immutable tombstone.
    ``meta.current`` is the FRESHLY-READ current Card, so Kanbantt's reconcile has
    immediate ground truth (no extra round trip):

      * a LIVE current task → the normal board projection (escalation badge included);
      * a TOMBSTONE → its tombstone card (``cards.tombstone_card``), which the board
        projection deliberately OMITS — but the spec REQUIRES the tombstone in
        ``meta.current`` for a tombstone-immutability conflict.

    Must be called while ``store`` is still open (it reads escalations for the live
    badge)."""
    if current.deleted_at is not None:
        current_card = tombstone_card(current)
        message = (
            f"card {current.id!r} is a tombstone and is immutable "
            "(no undelete in v1); re-read the board"
        )
    else:
        current_card = project([current], store.escalations.list_all())[0]
        message = (
            f"version conflict on card {current.id!r}: it was modified since you last "
            "read it; reconcile against meta.current and retry"
        )
    return domain_error_result(CONFLICT, message, {"current": current_card})


def build_server(config: ServerConfig) -> FastMCP:
    """Construct the FastMCP server bound to ``config`` (its tools read the spine at
    ``config.db_path``). Tools return a fully-formed ``CallToolResult`` so their
    structuredContent passes through verbatim (see ``result.py``)."""
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=config.enable_dns_rebinding_protection,
        allowed_hosts=config.resolved_allowed_hosts(),
        allowed_origins=[config.origin],
    )
    mcp = FastMCP(
        SERVER_NAME,
        host=config.host,
        port=config.port,
        streamable_http_path=STREAMABLE_HTTP_PATH,
        transport_security=transport_security,
    )
    # FastMCP exposes no version kwarg; the underlying low-level Server carries it,
    # so this is the supported knob for serverInfo.version.
    mcp._mcp_server.version = SERVER_VERSION

    @mcp.tool(
        name="board_get",
        description="Return the read-only board: six columns (one per Task state) and the tier tags.",
        structured_output=False,
    )
    def board_get() -> types.CallToolResult:
        return ok_result(build_board())

    @mcp.tool(
        name="card_list",
        description="Return a full snapshot of the spine's live Tasks projected to Cards, plus a fresh sync_token.",
        structured_output=False,
    )
    def card_list(
        updated_since: Optional[str] = None,
        column_id: Optional[str] = None,
        tag: Optional[str] = None,
        include_deleted: bool = False,
        include_archived: bool = False,
    ) -> types.CallToolResult:
        try:
            result = list_cards(
                config.db_path,
                updated_since=updated_since,
                column_id=column_id,
                tag=tag,
                include_deleted=include_deleted,
                include_archived=include_archived,
                max_bytes=config.max_bytes,
            )
        except PayloadTooLarge as exc:
            return domain_error_result(
                PAYLOAD_TOO_LARGE,
                "card snapshot exceeds the configured size ceiling; narrow the query",
                {"size": exc.size, "limit": exc.limit},
            )
        return ok_result(result)

    @mcp.tool(
        name="escalation_resolve",
        description=(
            "Resolve an escalation: record an operator approve/deny decision with a "
            "rationale. The one human-gated mutating control (it writes the spine)."
        ),
        structured_output=False,
    )
    def escalation_resolve(
        id: str,
        resolution: str,
        resolution_rationale: str,
    ) -> types.CallToolResult:
        # ── ACTOR INVARIANT (security-critical) ──────────────────────────────────
        # The actor is derived from the AUTHENTICATED CREDENTIAL, never from the
        # payload. This tool deliberately takes NO `actor` parameter, so a client
        # CANNOT set it via a JSON-RPC payload field — an extra `actor` field never
        # reaches resolve_escalation. The server authenticates exactly one Bearer
        # token (the operator token; see http.BearerAuthMiddleware), so the
        # authenticated actor is, by construction, 'operator'. THE TOKEN IS THE ACTOR
        # ASSERTION: an agent that steals the operator token still cannot forge a
        # DIFFERENT actor, because resolve_escalation re-asserts actor == 'operator'
        # and hard-aborts otherwise.
        #
        # TOKEN-SCOPING SEAM (v2): when distinct agent credentials exist (the v2 write
        # path), the actor must be resolved from the authenticated identity and agent
        # tokens MUST be refused for this tool — an agent is not 'operator'. Wire the
        # per-credential actor here the moment a second credential is introduced.
        actor = "operator"

        # WRITABLE STORE: the read tools open a Store only to read; this tool WRITES,
        # so it opens its own Store the way the Spine facade does (Spine(Store(path)))
        # and the put inside resolve_escalation commits. WAL serializes writers, so
        # this is safe alongside the read tools' connections and any other writer.
        try:
            with Store(config.db_path) as store:
                resolved = Spine(store).resolve_escalation(
                    id,
                    resolution=resolution,
                    resolution_rationale=resolution_rationale,
                    actor=actor,
                )
        except KeyError:
            return domain_error_result(NOT_FOUND, f"escalation {id!r} does not exist", {"id": id})
        except PermissionError as exc:
            # The actor invariant fired. Unreachable for the operator token today; the
            # defense-in-depth backstop for the v2 agent-credential write path.
            return domain_error_result(UNAUTHORIZED, str(exc), {"id": id})
        except ValueError as exc:
            # Bad resolution enum, or a rationale under the >=10-char semantic floor.
            return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})
        _notify_dirty()
        return ok_result({"escalation": resolved.to_dict()})

    # ── operator card-write tools (FREE / ungoverned: the operator's hand) ───────
    # The four card_* mutating tools. UNLIKE escalation_resolve these are NOT a
    # governed control: they are direct state/field puts with NO transition-legality
    # check and NO actor field (the single Bearer token is the attribution). Each
    # opens its OWN writable Store (exactly as escalation_resolve does — WAL serializes
    # writers) and returns the freshly-projected Card (badge included) for the client
    # to apply. They advertise together so a later slice can wire Kanbantt's canWrite.
    #
    # SPEC CONFORMANCE (kanbantt-mcp-spec §Tool Contract / §Concurrency): card_update /
    # card_move / card_delete speak the spec's exact wire shapes — keyed by `id`, a
    # `patch` object for update, `column_id` for move — and enforce OPTIMISTIC
    # CONCURRENCY. `expected_version` is REQUIRED on these three (the spec's "declare
    # your violence"): a mismatch against the card's current opaque `version` token
    # returns the `conflict` envelope (meta.current = the freshly-read current card).
    # `force: true` (update/move ONLY) crushes the version check; card_delete has NO
    # force — destructive ops never get a bypass. Tombstones are immutable: any of the
    # three targeting one returns `conflict` (meta.current = the tombstone), even under
    # force. card_create speaks the spec's { card: CardInput } shape (the conformance
    # pass that was deferred), extended with a REQUIRED top-level project_id whose
    # enumeration is the project_list read; tier rides as the "tier:N" tag-id string
    # the projection emits (see _patch_tier_to_int / _tier_from_card_input).

    @mcp.tool(
        name="project_list",
        description=(
            "Return the spine's live Projects ({ projects: [{id, name, created_at}] }) "
            "— the project-targeting read card_create's project_id rides on. "
            "Read-only; clients gate their project picker on this tool's advertisement."
        ),
        structured_output=False,
    )
    def project_list() -> types.CallToolResult:
        # Live projects only (a soft-deleted project is not a create target — the
        # same liveness rule jobcard's resolver applies). Sorted (created_at, id):
        # deterministic, oldest-first, with the opaque id as the stable tiebreak.
        with Store(config.db_path) as store:
            projects = [
                {"id": p.id, "name": p.name, "created_at": p.created_at}
                for p in store.projects.list_live()
            ]
        projects.sort(key=lambda p: (p["created_at"] or "", p["id"]))
        return ok_result({"projects": projects})

    @mcp.tool(
        name="card_create",
        description=(
            "Create a card from spec CardInput ({ card, project_id }): an operator-"
            "authored Task in a live project. A free, ungoverned operator write — "
            "HUMAN INTAKE by default: column_id defaults to 'created' and the card is "
            "UNTIERED unless the input itself carries a tier (a 'tier:N' tag, or the "
            "card_update-style tier tolerance). Idempotent on a duplicate card id "
            "(returns the existing card as success; safe to retry). project_id is "
            "REQUIRED on this server — enumerate live projects via project_list."
        ),
        structured_output=False,
    )
    def card_create(
        card: Dict[str, Any],
        project_id: Optional[str] = None,
    ) -> types.CallToolResult:
        # SPEC CONFORMANCE (kanbantt-mcp-spec §Create): CardInput is a Card minus the
        # authority-owned fields — version / created_at / updated_at / created_by /
        # updated_by / deleted_at supplied by a client are IGNORED, not errored (this
        # handler simply never reads them; created_by is stamped from the credential,
        # see CARD_CREATE_ACTOR). Fields the spine does not model (description,
        # priority, checklist, attachments) flatten away at this boundary — the
        # projection re-emits their Card defaults. project_id is the ONE extension
        # over the spec input (this server's Tasks live in Projects); it rides at the
        # top level NEXT TO the card so CardInput itself stays a pure Card subset.
        #
        # HUMAN-INTAKE SEMANTICS (the governance stance): a board-created card is
        # intent capture ONLY. It defaults into 'created', UNTIERED — never
        # auto-tiered, never dispatched; classification and dispatch are later,
        # separate rungs on the Hermes side. This tool adds no gate (ungoverned
        # operator write, exactly as card_update/card_move) and triggers nothing.

        # Input hygiene (→ validation_failed) is checked before any lookup
        # (→ not_found), mirroring escalation_resolve's validate-before-lookup order.
        title = card.get("title")
        if not isinstance(title, str) or not title.strip():
            return domain_error_result(VALIDATION_FAILED, "card.title must be a non-empty string", {})
        try:
            tier = _tier_from_card_input(card)
        except ValueError as exc:
            return domain_error_result(VALIDATION_FAILED, str(exc), {})
        if tier is not None and not (1 <= tier <= 4):
            return domain_error_result(
                VALIDATION_FAILED, f"tier must be an int in 1..4, got {tier!r}", {"tier": tier}
            )
        task_id = card.get("id")
        if task_id is not None and (not isinstance(task_id, str) or not task_id):
            return domain_error_result(
                VALIDATION_FAILED, f"card.id must be a non-empty string, got {task_id!r}", {}
            )
        order = card.get("order")
        if order is not None and (not isinstance(order, str) or not order):
            return domain_error_result(
                VALIDATION_FAILED, f"card.order must be a non-empty string, got {order!r}", {}
            )

        try:
            with Store(config.db_path) as store:
                spine = Spine(store)

                # IDEMPOTENT CREATE (spec §Create): an id the spine already knows —
                # live OR tombstoned — returns the existing card as SUCCESS, no write,
                # no error. This runs BEFORE the project_id requirement so a retry of
                # a create that already landed never trips targeting validation.
                if task_id is not None:
                    existing = spine.get_task(task_id)
                    if existing is not None:
                        if existing.deleted_at is not None:
                            return ok_result({"card": tombstone_card(existing)})
                        return ok_result(
                            {"card": project([existing], store.escalations.list_all())[0]}
                        )

                # PROJECT TARGETING: required, explicit, live-only. No default-project
                # fallback — an untargeted create must not land somewhere silently
                # (and a typo must not mint a phantom project: unknown → not_found,
                # never create-if-missing).
                if project_id is None:
                    return domain_error_result(
                        VALIDATION_FAILED,
                        "card_create on this server requires project targeting: "
                        "pass project_id (enumerate live projects via project_list)",
                        {},
                    )
                proj = spine.get_project(project_id)
                if proj is None or proj.deleted_at is not None:
                    return domain_error_result(
                        NOT_FOUND, f"project {project_id!r} does not exist", {"project_id": project_id}
                    )

                task = spine.create_task(
                    project_id,
                    title,
                    state=card.get("column_id") or "created",  # column IS the state
                    tier=tier,
                    acceptance_criteria=card.get("acceptance_criteria") or "",
                    due=card.get("due"),
                    depends_on=card.get("depends_on"),
                    task_id=task_id,
                    order=order,
                    created_by=_created_by_with_provenance(card.get("created_by")),
                )
                card_out = project([task], store.escalations.list_all())[0]
        except ValueError as exc:
            # create_task validates the target state ∈ STATES, due ISO-8601, and
            # depends_on shape/self-reference (SpineError is a ValueError).
            return domain_error_result(VALIDATION_FAILED, str(exc), {})
        _notify_dirty()
        return ok_result({"card": card_out})

    @mcp.tool(
        name="card_update",
        description=(
            "Edit an operator card's mutable fields via a Card patch (title / "
            "acceptance_criteria / effort / impact / due / depends_on / tier). "
            "RFC 7386 key-presence semantics: absent key = unchanged; present null = "
            "clear for {due, effort, impact}; depends_on uses [] to clear (null → "
            "validation_failed). Guarded: {tier, archived_at, deleted_at} present-null "
            "→ validation_failed; created_by present (any value) → validation_failed "
            "(write-once mint provenance). NOT state or order (use card_move). "
            "expected_version required (optimistic concurrency); force skips the check."
        ),
        structured_output=False,
    )
    def card_update(
        id: str,
        patch: Dict[str, Any],
        expected_version: str,
        force: bool = False,
    ) -> types.CallToolResult:
        # GUARDED FIELDS (amendment 2026-07-06): present-null → validation_failed,
        # naming the governed tool. These fields move ONLY through their governed paths;
        # closing the back-door lifecycle mutation via patch-null.
        if "tier" in patch and patch["tier"] is None:
            return domain_error_result(
                VALIDATION_FAILED,
                "tier cannot be cleared; use card_retier to change a set tier",
                {"id": id},
            )
        if "archived_at" in patch and patch["archived_at"] is None:
            return domain_error_result(
                VALIDATION_FAILED,
                "archived_at is governed by card_archive / card_unarchive; use those tools",
                {"id": id},
            )
        if "deleted_at" in patch and patch["deleted_at"] is None:
            return domain_error_result(
                VALIDATION_FAILED,
                "deleted_at is governed by card_delete; use that tool",
                {"id": id},
            )
        # WRITE-ONCE created_by (mint provenance): rejected on ANY presence, not just
        # present-null. The audit value of created_by is "who/how this card was actually
        # minted"; a mutable stamp destroys it — same rationale that made tier write-once.
        # An EXPLICIT error, never a silent drop: silent-drop is the description bug we are
        # not duplicating. Foreign clients that round-trip a full Card back as a patch are
        # told plainly the field is immutable rather than having it vanish.
        if "created_by" in patch:
            return domain_error_result(
                VALIDATION_FAILED,
                "created_by is write-once mint provenance and cannot be changed by card_update",
                {"id": id},
            )

        # Map the spec's partial-Card patch onto the facade's field kwargs.
        # RFC 7386 key-presence: absent = _UNSET (unchanged); present = value (None clears
        # for clearable fields {effort, impact, due}). depends_on is type-strict: null →
        # validation_failed here; [] clears; list replaces.
        kwargs: Dict[str, Any] = {}
        if "title" in patch:
            kwargs["title"] = patch["title"]
        if "acceptance_criteria" in patch:
            kwargs["acceptance_criteria"] = patch["acceptance_criteria"]
        if "effort" in patch:
            kwargs["effort"] = patch["effort"]   # None clears (key-presence)
        if "impact" in patch:
            kwargs["impact"] = patch["impact"]   # None clears
        if "due" in patch:
            kwargs["due"] = patch["due"]         # None clears; ISO-8601 string sets
        if "depends_on" in patch:
            dep = patch["depends_on"]
            if dep is None:
                return domain_error_result(
                    VALIDATION_FAILED,
                    "depends_on cannot be null; use [] to clear",
                    {"id": id},
                )
            if not isinstance(dep, list):
                return domain_error_result(
                    VALIDATION_FAILED,
                    "depends_on must be a list of task-id strings",
                    {"id": id},
                )
            for entry in dep:
                if not isinstance(entry, str) or not entry:
                    return domain_error_result(
                        VALIDATION_FAILED,
                        "depends_on entries must be non-empty strings",
                        {"id": id},
                    )
            kwargs["depends_on"] = dep
        if "tier" in patch:
            try:
                kwargs["tier"] = _patch_tier_to_int(patch["tier"])
            except ValueError as exc:
                return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})

        with Store(config.db_path) as store:
            spine = Spine(store)
            try:
                task = spine.update_task(
                    id, expected_version=expected_version, force=force, **kwargs
                )
            except KeyError:
                return domain_error_result(NOT_FOUND, f"task {id!r} does not exist", {"id": id})
            except ConflictError as exc:
                return _conflict_result(exc.current, store)
            except ValueError as exc:
                # No modeled field given, bad tier value/range, bad due, bad depends_on.
                return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})
            card = project([task], store.escalations.list_all())[0]
            _notify_dirty()
            return ok_result({"card": card})

    @mcp.tool(
        name="card_move",
        description=(
            "Move an operator card to a column (a Task state) at a LexoRank order. A "
            "free, ungoverned operator write — no transition-legality check. "
            "expected_version is required (optimistic concurrency); force skips the check."
        ),
        structured_output=False,
    )
    def card_move(
        id: str,
        column_id: str,
        order: str,
        expected_version: str,
        force: bool = False,
    ) -> types.CallToolResult:
        # column_id IS the target Task state (state↔column is one-to-one); move_task
        # validates it ∈ STATES (unknown → validation_failed, the existing case).
        with Store(config.db_path) as store:
            spine = Spine(store)
            try:
                task = spine.move_task(
                    id, column_id, order=order, expected_version=expected_version, force=force
                )
            except KeyError:
                return domain_error_result(NOT_FOUND, f"task {id!r} does not exist", {"id": id})
            except ConflictError as exc:
                return _conflict_result(exc.current, store)
            except ValueError as exc:
                # Unknown column_id (not one of the six Task states).
                return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})
            card = project([task], store.escalations.list_all())[0]
            _notify_dirty()
            return ok_result({"card": card})

    @mcp.tool(
        name="card_delete",
        description=(
            "Soft-delete an operator card: a recoverable tombstone (the row is retained, "
            "hidden from the board). Returns the tombstone card. expected_version is "
            "required and has NO force — destructive ops never bypass the version check."
        ),
        structured_output=False,
    )
    def card_delete(id: str, expected_version: str) -> types.CallToolResult:
        with Store(config.db_path) as store:
            spine = Spine(store)
            try:
                task = spine.soft_delete_task(id, expected_version=expected_version)
            except KeyError:
                return domain_error_result(NOT_FOUND, f"task {id!r} does not exist", {"id": id})
            except ConflictError as exc:
                return _conflict_result(exc.current, store)
            # Spec: card_delete returns { card } (the tombstone). The board projection
            # omits a tombstone, so render it via the shared tombstone lens.
            _notify_dirty()
            return ok_result({"card": tombstone_card(task)})

    # ── governed re-tier (audited; the matching write-once guard lives in card_update) ─
    @mcp.tool(
        name="card_retier",
        description=(
            "Re-tier an operator card: change an ALREADY-SET tier to a different valid "
            "tier (1..4), recording an append-only governance audit row. A GOVERNED "
            "write — reason is required (non-empty); expected_version is required "
            "(optimistic concurrency); there is NO force (a re-tier re-decides against "
            "fresh state, never clobbers)."
        ),
        structured_output=False,
    )
    def card_retier(
        id: str,
        new_tier: str,
        expected_version: str,
        reason: str,
    ) -> types.CallToolResult:
        # new_tier rides as the "tier:N" tag-id string the projection emits and the board
        # declares — the SAME wire form as card_update's patch.tier — parsed to the
        # internal int via _patch_tier_to_int. The 1..4 RANGE and every re-tier invariant
        # (currently-tiered, differs-from-current, non-empty reason) live in
        # Spine.retier_task (one source of truth): a value not parseable to a tier here is
        # validation_failed.
        try:
            tier_int = _patch_tier_to_int(new_tier)
        except ValueError as exc:
            return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})
        # ACTOR: card_retier deliberately takes NO actor parameter, so a client cannot set
        # it via the payload. The Spine injects the authenticated-client placeholder
        # (RETIER_ACTOR); at Stage 2 (per-user credentials) derive the real actor from the
        # authenticated identity and pass retier_task(actor=...). This mirrors the
        # escalation_resolve actor stance MINUS the operator-only invariant — any
        # authenticated client may re-tier, because the audit row (not a gate) is the
        # control here.
        with Store(config.db_path) as store:
            spine = Spine(store)
            try:
                task = spine.retier_task(
                    id, tier_int, reason=reason, expected_version=expected_version
                )
            except KeyError:
                return domain_error_result(NOT_FOUND, f"task {id!r} does not exist", {"id": id})
            except ConflictError as exc:
                return _conflict_result(exc.current, store)
            except ValueError as exc:
                # untiered card / tier out of range / no-op same tier / empty reason.
                return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})
            card = project([task], store.escalations.list_all())[0]
            _notify_dirty()
            return ok_result({"card": card})

    # ── governed archive pair (audited; mirrors card_retier's shape verbatim) ────
    @mcp.tool(
        name="card_archive",
        description=(
            "Archive an operator card: set the orthogonal archived_at flag, hiding it "
            "from the default card_list view (NOT a delete, NOT a column move), "
            "recording an append-only governance audit row. A GOVERNED write — "
            "expected_version is required (optimistic concurrency); there is NO force. "
            "An already-archived card is validation_failed (loud idempotency); a card "
            "with an unresolved escalation cannot be archived. reason is optional "
            "(defaults to 'manual_archive'; the audit ledger always records one)."
        ),
        structured_output=False,
    )
    def card_archive(
        id: str,
        expected_version: str,
        reason: Optional[str] = None,
    ) -> types.CallToolResult:
        # REASON DEFAULTING (the ergonomic half of the ledger's hard non-empty-reason
        # invariant): an OMITTED reason becomes the deterministic default here, so the
        # manual path has zero friction while 100% of archive_audit rows stay reasoned.
        # An EXPLICIT empty/whitespace reason is NOT defaulted — the ledger rejects it
        # (→ validation_failed): explicit garbage is loud, omission is ergonomic.
        # Bulk/auto contexts (Pass 2 sweepers) pass their own canned strings.
        if reason is None:
            reason = "manual_archive"
        # ACTOR: as card_retier — NO actor parameter, so a client cannot set it via the
        # payload; the Spine injects the authenticated-client placeholder (ARCHIVE_ACTOR).
        with Store(config.db_path) as store:
            spine = Spine(store)
            try:
                task = spine.archive_task(id, reason=reason, expected_version=expected_version)
            except KeyError:
                return domain_error_result(NOT_FOUND, f"task {id!r} does not exist", {"id": id})
            except ConflictError as exc:
                return _conflict_result(exc.current, store)
            except ValueError as exc:
                # already archived / open escalation / explicit empty reason (ledger).
                return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})
            card = project([task], store.escalations.list_all())[0]
            _notify_dirty()
            return ok_result({"card": card})

    @mcp.tool(
        name="card_unarchive",
        description=(
            "Unarchive an operator card: clear the archived_at flag, returning it to "
            "the default card_list view, recording an append-only governance audit row. "
            "A GOVERNED write — expected_version is required (optimistic concurrency); "
            "there is NO force. A card that is not archived is validation_failed (loud "
            "idempotency). reason is optional (defaults to 'manual_unarchive'; the "
            "audit ledger always records one)."
        ),
        structured_output=False,
    )
    def card_unarchive(
        id: str,
        expected_version: str,
        reason: Optional[str] = None,
    ) -> types.CallToolResult:
        # Same reason-defaulting and actor stance as card_archive. No escalation gate:
        # unarchiving restores a card to view, which never buries anything.
        if reason is None:
            reason = "manual_unarchive"
        with Store(config.db_path) as store:
            spine = Spine(store)
            try:
                task = spine.unarchive_task(id, reason=reason, expected_version=expected_version)
            except KeyError:
                return domain_error_result(NOT_FOUND, f"task {id!r} does not exist", {"id": id})
            except ConflictError as exc:
                return _conflict_result(exc.current, store)
            except ValueError as exc:
                # not archived / explicit empty reason (ledger).
                return domain_error_result(VALIDATION_FAILED, str(exc), {"id": id})
            card = project([task], store.escalations.list_all())[0]
            _notify_dirty()
            return ok_result({"card": card})

    # ASYMMETRIC-ADVERTISING / DEFERRED CAPABILITY GAP (deliberate, NOT permanent):
    # escalation_resolve is advertised WITHOUT a matching escalation_list query. The
    # resolve payload already rides in card_list's per-card badge, so a separate list
    # query is unnecessary for THIS slice — Kanbantt gates the resolve control on the
    # advertised escalation_resolve name and reads the badge from card_list. At v2
    # (multi-client / a real approval queue) realign discovery by adding escalation_list
    # so the read and write halves of the escalation surface are symmetric again.

    return mcp


def create_app(config: Optional[ServerConfig] = None) -> Starlette:
    """The Starlette ASGI app: FastMCP's Streamable HTTP transport (POST requests,
    GET SSE stream, DELETE teardown — all three wired) wrapped with the Bearer gate
    (inner) and the CORS layer (outer)."""
    config = config or from_env()
    app = build_server(config).streamable_http_app()
    # add_middleware inserts outermost-last → CORS wraps Auth wraps the transport.
    app.add_middleware(BearerAuthMiddleware, token=config.token)
    app.add_middleware(CORSMiddleware, origin=config.origin)
    return app


def main() -> None:
    """Console entry point. Refuses to start without a configured token (no
    unauthenticated fallback). Initializes Drive backup (DORMANT when SA key absent)
    and handles --restore-from-drive gated restore."""
    import sys
    import uvicorn

    config = from_env()
    if not config.token:
        raise SystemExit("CLAUNKER_SPINE_TOKEN is required (no unauthenticated fallback).")

    from spine.drive_backup import DriveBackup, restore_from_drive

    restore = "--restore-from-drive" in sys.argv

    # Gated restore: only guard when backup is configured (SA key present).
    # Build a temp backup to check dormancy before full init.
    _probe = DriveBackup(config.db_path)
    if not _probe.dormant:
        db_exists = os.path.exists(config.db_path) and os.path.getsize(config.db_path) > 0
        if not db_exists:
            if restore:
                restore_from_drive(config.db_path)
            else:
                raise SystemExit(
                    "FATAL: Local ledger absent. "
                    "To adopt remote Drive backup, restart with --restore-from-drive"
                )

    backup = DriveBackup(config.db_path)
    _set_backup(backup)
    backup.startup_flush()

    try:
        uvicorn.run(create_app(config), host=config.host, port=config.port)
    finally:
        backup.shutdown_flush()


if __name__ == "__main__":
    main()
