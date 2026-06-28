"""Opaque version token minting.

A version is ``{seq}:{content_hash}`` — NOT a bare counter. ``seq`` (the global
append order of the entity's last event) guarantees monotonic change on every
mutation; ``content_hash`` binds the token to the actual reduced content so the
token is collision-safe and content-addressable. v1 is single-writer, so this is
sufficient (see the converged design notes).

By contract the token is OPAQUE: consumers compare it for equality only and never
parse or order it. The ``{seq}:{hash}`` shape is an implementation detail.
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
