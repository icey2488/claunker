"""The canonical ``TaskEntity`` plus the two boundary mappings the projection
needs: internal lifecycle → board column (one-to-one), and actor string → the
Card ``{type, id}`` actor ref.

``TaskEntity`` is the *reduced* state — never stored directly; always folded from
the event log (``reducer.py``). Its rich orchestration fields (tier, escalation
ref, seq, version, actors) are what the one-way projection deliberately drops or
flattens when conforming to the Card schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


class Lifecycle:
    """Claunker-internal orchestration lifecycle states.

    These are the spine's own vocabulary; the projection maps each one-to-one
    onto a board column of the same id (``created``/``queued``/``executing``/
    ``judging``/``delivered``/``failed``), keeping executing vs judging and
    delivered vs failed distinct for observability rather than collapsing them
    onto Kanbantt's four reserved columns.
    """

    CREATED = "created"
    QUEUED = "queued"
    EXECUTING = "executing"
    JUDGING = "judging"
    DELIVERED = "delivered"
    FAILED = "failed"


# Reserved Kanbantt column ids (the shared agent-routing vocabulary).
RESERVED_COLUMNS = ("backlog", "todo", "in_progress", "done")

# Internal lifecycle → board column, one-to-one: each lifecycle state is its own
# column id (executing vs judging and delivered vs failed kept distinct for
# observability instead of collapsing onto the four reserved columns). Anything
# not here passes through unchanged, landing the card in Kanbantt's visible
# "fallback tray" rather than being silently dropped — the spec-conformant degrade
# for an unknown column.
_LIFECYCLE_TO_COLUMN: Dict[str, str] = {
    Lifecycle.CREATED: "created",
    Lifecycle.QUEUED: "queued",
    Lifecycle.EXECUTING: "executing",
    Lifecycle.JUDGING: "judging",
    Lifecycle.DELIVERED: "delivered",
    Lifecycle.FAILED: "failed",
}


def lifecycle_to_column(lifecycle_state: str) -> str:
    """Map an internal lifecycle state to a Kanbantt column id (passthrough on miss)."""
    return _LIFECYCLE_TO_COLUMN.get(lifecycle_state, lifecycle_state)


class Actor:
    """Who acted. ``created_by``/``updated_by`` are these actor strings."""

    CLAUDE = "claude"      # architect
    OLLAMA = "ollama"      # executor
    GEMINI = "gemini"      # judge
    OPERATOR = "operator"  # the human


# Only the operator is a human; the model actors are agents.
_HUMAN_ACTORS = frozenset({Actor.OPERATOR})


def actor_ref(actor: Optional[str]) -> Dict[str, str]:
    """Flatten an actor string to the Card schema's ``{type, id}`` actor ref.
    A missing actor defaults to the operator (fail safe: attribute to the human)."""
    actor = actor or Actor.OPERATOR
    return {"type": "human" if actor in _HUMAN_ACTORS else "agent", "id": actor}


@dataclass
class TaskEntity:
    """The canonical reduced entity. Mutated in place by the reducer as it folds.

    ``tier`` aligns with the classifier's tier space (1=self-accept .. 4=human)
    but is stored as a plain int here — the spine does not import the classifier.
    """

    id: str
    title: str
    order: str  # LexoRank string (spine-assigned)
    lifecycle_state: str = Lifecycle.CREATED
    tier: Optional[int] = None
    escalated: bool = False
    escalation_ref: Optional[str] = None
    version: Optional[str] = None  # opaque {seq}:{content_hash}; set by the reducer
    seq: int = 0                   # seq of the last event folded into this state
    deleted_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

    def content(self) -> Dict[str, Any]:
        """The semantic content hashed into the version token. Excludes ``version``
        (circular) and ``seq`` (the token's own prefix); includes the mutating
        timestamps/actors so any real change moves the token."""
        return {
            "id": self.id,
            "title": self.title,
            "order": self.order,
            "lifecycle_state": self.lifecycle_state,
            "tier": self.tier,
            "escalated": self.escalated,
            "escalation_ref": self.escalation_ref,
            "deleted_at": self.deleted_at,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }
