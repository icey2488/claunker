"""LexoRank-style string ordering for the spine.

``order`` is a lexicographic fractional position (Card spec: "inserting between
'a' and 'c' mints 'b'"). The spine is the authority that mints ranks. New
entities are seeded *append-at-end* in creation order; inserting between two
existing ranks uses ``rank_between``; and ``rebalance`` (OUT-OF-BAND only, never
called synchronously in create) reassigns compact, evenly-spaced ranks when ranks
drift toward the ``MAX_RANK_LENGTH`` ceiling.

The alphabet is base-36 over ``0-9a-z``; their ASCII order matches their alphabet
index, so plain string comparison is the ordinal comparison the spec requires.
"""

from __future__ import annotations

import math
from typing import List, Tuple

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)

# Ceiling on an order string's length. Append/seed stay short; only pathological
# repeated between-inserts at one spot grow length. Crossing this is the signal
# to run an out-of-band ``rebalance`` (the rank space is never exhausted, only
# the compactness budget).
MAX_RANK_LENGTH = 64
_DEPTH_GUARD = MAX_RANK_LENGTH * 4

_VAL = {c: i for i, c in enumerate(ALPHABET)}


def _validate(rank: str) -> None:
    for c in rank:
        if c not in _VAL:
            raise ValueError(f"rank {rank!r} contains {c!r} outside the rank alphabet")


def rank_between(lo: str, hi: str) -> str:
    """Return a rank string strictly between ``lo`` and ``hi`` under ordinal
    comparison. ``lo == ""`` means "before everything"; ``hi == ""`` means "after
    everything" (the append-at-end case)."""
    _validate(lo)
    _validate(hi)
    if lo and hi and lo >= hi:
        raise ValueError(f"rank_between requires lo < hi, got {lo!r} >= {hi!r}")

    result: List[str] = []
    i = 0
    hi_unbounded = hi == ""
    while True:
        if i >= _DEPTH_GUARD:  # pragma: no cover - only a degenerate "insert before min" hits this
            raise ValueError(f"rank_between exceeded depth guard for ({lo!r}, {hi!r})")
        av = _VAL[lo[i]] if i < len(lo) else 0
        if hi_unbounded:
            bv = BASE
        elif i < len(hi):
            bv = _VAL[hi[i]]
        else:
            bv = 0  # matched all of a real (finite) hi's prefix — no room below it here
        if bv - av >= 2:
            result.append(ALPHABET[(av + bv) // 2])
            return "".join(result)
        # No gap at this position: fix the digit to lo's and descend. Once we go
        # strictly below hi's digit, hi no longer constrains deeper positions.
        result.append(ALPHABET[av])
        if av < bv:
            hi_unbounded = True
        i += 1


def append_rank(after: str = "") -> str:
    """Mint a rank that sorts after ``after`` (append-at-end). ``after=""`` seeds
    the first rank."""
    return rank_between(after, "")


def needs_rebalance(ranks: List[str]) -> bool:
    """True if any rank has grown past the compactness ceiling."""
    return any(len(r) > MAX_RANK_LENGTH for r in ranks)


def _encode_fixed(value: int, width: int) -> str:
    digits: List[str] = []
    for _ in range(width):
        value, rem = divmod(value, BASE)
        digits.append(ALPHABET[rem])
    return "".join(reversed(digits))


def rebalance(ordered_ids: List[str]) -> List[Tuple[str, str]]:
    """OUT-OF-BAND ONLY — never called synchronously inside create().

    Given entity ids already in their intended order, return ``[(id, new_rank)]``
    with compact, fixed-width, evenly-spaced ranks. Applying the result (e.g. via
    out-of-band COLUMN_CHANGED order updates) is a maintenance/compaction concern
    outside this read-slice data core; this function only *plans* the new ranks.
    """
    n = len(ordered_ids)
    if n == 0:
        return []
    width = max(1, math.ceil(math.log(n + 2, BASE)))
    span = BASE ** width
    step = max(1, span // (n + 1))
    return [(eid, _encode_fixed(step * (i + 1), width)) for i, eid in enumerate(ordered_ids)]
