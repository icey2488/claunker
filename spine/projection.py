"""One-way projection ``Task → Card`` (kanbantt-mcp-spec v0.3.0 Card shape).

A LENS, not a mirror: strictly one-directional (the spine never reads a Card back)
and deliberately flattening. The locked v1 mapping:

    Task.state         → column_id    (one-to-one: six states, six columns)
    Task.tier          → a tag        ("tier:N" in the tags array)
    acceptance_criteria → echoed      (a direct Task-field copy; not a native
                         kanbantt Card field, so it survives via the unknown-field
                         round-trip rule, letting a card_update read its own write back)
    escalation (per card) → a badge   (three-state extension field; see below)
    archived_at        → echoed       (the orthogonal archive flag, mirrored onto the
                         Card so archive state round-trips; the DEFAULT card_list view
                         filters archived cards out — in ``spine_server.cards`` — but
                         the lens itself projects them: archived ≠ deleted)
    gate_status        → extension field, hardcoded "COMMITTED"
    id, title, order, version, created_at → pass through
    created_by         → pass through from Task.created_by (null when the task
                         was created without actor attribution; NEVER fabricated)
    everything else    → Card schema defaults (priority "med", empty collections,
                         nulls). The v1 entities carry no update timestamp, so
                         updated_by/updated_at project as null.

Escalation is ORTHOGONAL to the board column: a badge never moves the card out of
its ``state`` column (escalation is neither a column nor a tag). The badge reduces
ALL of a card's escalations to ONE of three states (``_badge_for``), exposed in the
badge's ``status`` discriminator — precedence unresolved > denied > none:

    unresolved  a live, not-yet-resolved escalation. {kind, status:'unresolved',
                id, reason, control_diff} — the approval-queue affordance.
    denied      no unresolved one, and the most-recently-resolved was a DENY.
                {kind, status:'denied', id, reason, control_diff,
                resolution_rationale} — a persistent kill-signal receipt.
    none        no unresolved one, and the most-recent resolution was an APPROVE
                (or there are no escalations). NO badge (``None``): an approved
                control change is committed and needs no affordance.

Two Claunker extension fields ride along (``gate_status``, ``badge``). The spec
mandates unknown fields are "preserved and round-tripped, never stripped", so they
survive a Kanbantt round trip cleanly. Soft-deleted Tasks are OMITTED entirely.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .entity import Escalation, Task

# Every projected card is committed state by construction (the write/gate path is a
# later slice). Claunker extension field, not a native Card field.
GATE_STATUS_COMMITTED = "COMMITTED"

# Card spec default when a card carries no explicit priority.
DEFAULT_PRIORITY = "med"

# The complete set of keys ``to_card`` emits — the known Card surface (native spec fields
# + Claunker extensions ``acceptance_criteria`` / ``gate_status`` / ``badge``). SINGLE
# SOURCE OF TRUTH for "what the spine models": the server subtracts this set (plus the
# input-only ``tier``/``project_id`` carriers) from a CardInput/patch to find the UNMODELED
# foreign keys it must PRESERVE into ``Task.metadata`` rather than flatten away. Keeping it
# here — next to the projection that defines the shape — stops it drifting from ``to_card``.
# Also the guard on the metadata overlay: a preserved foreign key can NEVER clobber a
# modeled/authority/extension field, even from a hand-tampered row.
CARD_FIELD_KEYS = frozenset({
    "id", "title", "description", "acceptance_criteria", "column_id", "order",
    "tags", "checklist", "due", "depends_on", "priority", "effort", "impact",
    "version", "deleted_at", "archived_at", "created_at", "updated_at",
    "created_by", "updated_by", "attachments", "gate_status", "badge",
})

# Spec-DEFINED Card fields the spine does not model as first-class columns. The spec
# declares them (``priority``/``checklist``/``attachments``), but the v1 work model has no
# native column for any of them. v0.8.0 STOPS DROPPING them: rather than projecting a fixed
# documented default and discarding the client's value (which punished spec compliance while
# a genuinely-foreign key was preserved — the backwards scope this fix corrects), a client-
# supplied value for one of these is PRESERVED through the SAME ``metadata`` path as an
# unknown foreign key and echoed on read. When the client sends none, ``to_card`` still emits
# the documented default below (``priority: "med"``, empty collections). They are the ONLY
# keys the metadata overlay may write onto the projected Card among CARD_FIELD_KEYS — see
# PROTECTED_CARD_KEYS.
UNMODELED_CARD_KEYS = frozenset({"priority", "checklist", "attachments"})

# The MODELED / authority / extension Card keys the metadata overlay may NEVER clobber — the
# full known surface MINUS the known-but-unmodeled fields that legitimately round-trip through
# metadata. This is the guard on projection: a preserved key can overwrite a projected default
# ONLY for a key in UNMODELED_CARD_KEYS; ``id``/``title``/``description``/``created_by``/
# ``gate_status``/… stay authority-owned even against a hand-tampered row. It is also what the
# server subtracts (plus the input-only ``tier``/``project_id`` carriers) to decide which
# CardInput keys to PRESERVE into ``Task.metadata`` — so the known-but-unmodeled trio flow in.
PROTECTED_CARD_KEYS = CARD_FIELD_KEYS - UNMODELED_CARD_KEYS


def _tags_for(task: Task) -> List[str]:
    """tier → a tag id in the Card ``tags`` array (omitted until a tier is set)."""
    return [f"tier:{task.tier}"] if task.tier is not None else []


def _badge_for(escalations: List[Escalation]) -> Optional[Dict[str, Any]]:
    """Reduce ALL of one task's escalations to its single badge, or ``None``.

    Three-state precedence — unresolved > denied > none:
      * any live UNRESOLVED escalation (``resolved_at`` None, not deleted) →
        a ``status:'unresolved'`` badge. On multiple, the OLDEST wins (min
        ``created_at``, ``id`` tiebreak) — the one that has waited longest.
      * else the most-recently-resolved live escalation, IF it was a DENY →
        a ``status:'denied'`` badge carrying ``resolution_rationale`` (the
        receipt). "Most recent" = max (``resolved_at``, ``created_at``, ``id``).
      * else (the most-recent resolution was an approve, or there are none) →
        ``None``. An approval commits the change; it needs no badge.

    One badge per card by construction (a single dict or ``None``)."""
    live = [e for e in escalations if e.deleted_at is None]

    unresolved = [e for e in live if e.resolved_at is None]
    if unresolved:
        e = min(unresolved, key=lambda x: (x.created_at or "", x.id))
        return {
            "kind": "escalation",
            "status": "unresolved",
            "id": e.id,
            "reason": e.reason,
            "control_diff": e.control_diff,
        }

    resolved = [e for e in live if e.resolved_at is not None]
    if resolved:
        e = max(resolved, key=lambda x: (x.resolved_at or "", x.created_at or "", x.id))
        if e.resolution == "deny":
            return {
                "kind": "escalation",
                "status": "denied",
                "id": e.id,
                "reason": e.reason,
                "control_diff": e.control_diff,
                "resolution_rationale": e.resolution_rationale,
            }
    return None


def to_card(task: Optional[Task], badge: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Project one Task to a Card dict, or ``None`` if it is soft-deleted (and thus
    omitted from board output). ``badge`` is the task's pre-computed escalation
    badge (or ``None``) — see ``_badge_for``; it rides as an extension field and
    never affects ``column_id``."""
    if task is None or task.deleted_at is not None:
        return None

    card = {
        # ── native Card fields (kanbantt-mcp-spec §Card) ──────────────────────
        "id": task.id,
        "title": task.title,
        "description": task.description,    # the real narrative body (null when unset);
                                            # NO LONGER a constant "" — that was the silent
                                            # drop this contract fixes
        "acceptance_criteria": task.acceptance_criteria,  # echoed so a write round-trips
        "column_id": task.state,            # state → column, one-to-one
        "order": task.order,
        "tags": _tags_for(task),
        "checklist": [],                    # documented default; a client-supplied checklist
                                            # overrides this from metadata (see overlay below)
        "due": task.due,
        "depends_on": task.depends_on,
        "priority": DEFAULT_PRIORITY,       # documented default; client value overrides via
                                            # the metadata overlay (known-but-unmodeled field)
        "effort": task.effort,
        "impact": task.impact,
        "version": task.version,
        "deleted_at": None,                 # soft-deleted tasks are omitted, never projected
        "archived_at": task.archived_at,    # echoed so archive state round-trips to the client
        "created_at": task.created_at,
        "updated_at": None,                 # v1 entities track no update timestamp
        "created_by": task.created_by,      # pass through; null when entity has no actor
        "updated_by": None,
        "attachments": [],                  # documented default; client value overrides via
                                            # the metadata overlay (known-but-unmodeled field)
        # ── Claunker extensions (preserved by the unknown-field round-trip rule) ─
        "gate_status": GATE_STATUS_COMMITTED,
        "badge": badge,
    }
    # PRESERVE-AND-ROUND-TRIP: echo the preserved keys the write boundary stored in
    # ``Task.metadata`` (spec §Schema Versioning). Two kinds ride here — genuinely-foreign
    # keys, and the known-but-unmodeled Card trio (``priority``/``checklist``/``attachments``,
    # v0.8.0). The guard skips only PROTECTED_CARD_KEYS: a foreign key can never clobber a
    # modeled/authority/extension field (``id``/``title``/``created_by``/``gate_status``/…),
    # even from a hand-tampered row — but a preserved value for a key in UNMODELED_CARD_KEYS
    # is ALLOWED to OVERRIDE its documented default above, which is exactly how the trio round-
    # trips. (A foreign key that happens to equal an unmodeled key overrides the same slot; the
    # server only ever stores the trio there deliberately, so this is the intended behaviour.)
    for key, value in (task.metadata or {}).items():
        if key not in PROTECTED_CARD_KEYS:
            card[key] = value
    return card


def _badges_by_task(escalations: Iterable[Escalation]) -> Dict[str, Dict[str, Any]]:
    """task_id → its single badge dict, for the tasks that earn one. Groups every
    escalation by its task, then reduces each group to one badge via ``_badge_for``
    (the three-state precedence). Tasks whose group reduces to ``None`` are absent."""
    grouped: Dict[str, List[Escalation]] = {}
    for e in escalations:
        grouped.setdefault(e.task_id, []).append(e)
    badges: Dict[str, Dict[str, Any]] = {}
    for task_id, group in grouped.items():
        badge = _badge_for(group)
        if badge is not None:
            badges[task_id] = badge
    return badges


def project(tasks: Iterable[Task], escalations: Iterable[Escalation] = ()) -> List[Dict[str, Any]]:
    """Project tasks to the Card list: omit soft-deleted tasks, attach each task's
    three-state escalation badge (unresolved / denied / none; see ``_badge_for``),
    and sort by ``(order, id)`` (the spec's stable tiebreak on an order collision)."""
    badge_by_task = _badges_by_task(escalations)
    cards = []
    for task in tasks:
        card = to_card(task, badge_by_task.get(task.id))
        if card is not None:
            cards.append(card)
    cards.sort(key=lambda c: (c["order"], c["id"]))
    return cards
