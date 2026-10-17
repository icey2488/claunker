"""Opaque version token minting.

A version is ``{seq}:{content_hash}`` — NOT a bare counter. ``seq`` (the global
append order of the entity's last write) guarantees monotonic change on every
mutation; ``content_hash`` binds the token to the actual reduced content so the
token is collision-safe and content-addressable.

``seq`` MUST come from a monotonic source that never restarts. It is minted from a
PERSISTED high-water mark in the store's ``meta`` table, incremented inside the
write transaction (``storage.Store._next_seq`` under ``BEGIN IMMEDIATE``), so it
strictly increases even across the separate processes that each open their own
``Store`` per call. An earlier design kept ``seq`` only in memory and restarted it
at 0 on every open; because a content revert A→B→A then re-minted the IDENTICAL
token, a stale writer's ``expected_version`` could match a later, numerically-equal
token and its lost update slipped through (the ABA hazard proved by repro job
claunker.spine-aba-verify-20261016-syjhcgcm). The persisted HWM removes that; the
store additionally performs a compare-and-swap on write (see ``storage`` §put) to
close the two-writer lost-update race. The MINTING AUTHORITY (the store) may read
the seq prefix of tokens it minted to seed/restore this counter.

By contract the token remains OPAQUE to CONSUMERS: they compare it for equality
only and never parse or order it. The ``{seq}:{hash}`` shape is an implementation
detail.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

# 16 hex chars (64 bits) of SHA-256 — ample collision resistance once namespaced
# by the monotonic ``seq`` prefix, and keeps the token compact.
_HASH_LEN = 16


def content_hash(content: Dict[str, Any]) -> str:
    """Stable hash of an entity's semantic content. Deterministic for equal
    content (canonical key order), so reducing the same events twice is stable."""
    blob = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:_HASH_LEN]


def make_version(seq: int, content: Dict[str, Any]) -> str:
    """Compose the opaque token from the last-event ``seq`` and the content hash."""
    return f"{seq}:{content_hash(content)}"
