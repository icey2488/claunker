"""Claunker deterministic dispatch classifier — TIERING CORE (Phase 2 Step 2a).

Tiers a tool call by the GRANTED/REACHABLE toolset's highest capability class —
NEVER by natural-language intent. This module reads tool schema/args + the
reachable set ONLY. It must never parse the operator's task text to decide a
tier; doing so would be the "determinism mirage" the design forbids.

Capability classes (hardcoded, keyed on stable registry tool names):
    Read     → read_file, web_search, read-only search/lookup → Tier 1 (self-accept)
    Mutate   → write_file, patch                              → Tier 2 (single judge)
    Apex     → terminal, execute_code                         → Tier 4 (human; ABSOLUTE)
    Delegate → delegate_task → DYNAMIC: scored by the CHILD's requested toolsets,
               mapped through these same classes (NOT by the parent's apex status).

Floor = max class over the reachable set. Tiers:
    1 self-accept / 2 single judge / 3 dual sign-off / 4 human.
Dual sign-off (Tier 3) currently DEGRADES TO HUMAN (no third family wired) — see
``route_for_tier``. No capability class yields Tier 3 in this core; the value and
its degrade are wired for completeness.

FAIL-CLOSED INVARIANTS (enforced here):
  * Unknown / unmapped tool name → Apex / Tier 4 (maximally dangerous, never safe).
  * Apex class can never be lowered by any input (config or args) — ``_APEX_LOCKED``.
  * Sensitive-path match on a Mutate call → Tier 4, cannot be down-tiered.
  * delegate_task tiered by the CHILD's toolset; a child requesting ``terminal``
    → Tier 4 even if the delegation looks routine.

The hardcoded map + sensitive-path enum are the fail-closed floor. An optional
config-authorized override surface (``claunker.classifier`` in config.yaml, loaded
via the (mtime,size)-cached config path) mirrors the judge-verdict trust block:
config may EXTEND/raise, but can never lower a hardcoded default or touch the
apex lock.

The executor allowlist (FT-007) is a SEPARATE follow-on and is NOT in this module.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# ── Capability classes ──────────────────────────────────────────────────────
CLASS_READ = "read"
CLASS_MUTATE = "mutate"
CLASS_DUAL = "dual"
CLASS_APEX = "apex"
CLASS_DELEGATE = "delegate"  # dynamic — scored by the child's toolset

# Rank for max() over a reachable set (higher = more dangerous).
_CLASS_RANK: Dict[str, int] = {
    CLASS_READ: 1,
    CLASS_MUTATE: 2,
    CLASS_DUAL: 3,
    CLASS_APEX: 4,
}
_VALID_CONFIG_CLASSES = frozenset({CLASS_READ, CLASS_MUTATE, CLASS_DUAL, CLASS_APEX})

# ── Tiers ───────────────────────────────────────────────────────────────────
TIER_SELF_ACCEPT = 1
TIER_SINGLE_JUDGE = 2
TIER_DUAL_SIGNOFF = 3
TIER_HUMAN = 4

_CLASS_TO_TIER: Dict[str, int] = {
    CLASS_READ: TIER_SELF_ACCEPT,
    CLASS_MUTATE: TIER_SINGLE_JUDGE,
    CLASS_DUAL: TIER_DUAL_SIGNOFF,
    CLASS_APEX: TIER_HUMAN,
}

# ── Hardcoded capability-class map (stable registry tool names) ─────────────
# Anything NOT in this map is unmapped → fail-closed to Apex/Tier 4. The Read
# set is deliberately conservative: only no-side-effect read-only lookups.
_DEFAULT_TOOL_CLASSES: Dict[str, str] = {
    # Read (Tier 1)
    "read_file": CLASS_READ,
    "web_search": CLASS_READ,
    "web_extract": CLASS_READ,
    "search_files": CLASS_READ,
    "session_search": CLASS_READ,
    "skills_list": CLASS_READ,
    "skill_view": CLASS_READ,
    "read_terminal": CLASS_READ,
    "vision_analyze": CLASS_READ,
    # Mutate (Tier 2)
    "write_file": CLASS_MUTATE,
    "patch": CLASS_MUTATE,
    # Apex (Tier 4) — arbitrary shell / code execution
    "terminal": CLASS_APEX,
    "execute_code": CLASS_APEX,
    # Delegate (dynamic) — scored by the child's requested toolsets
    "delegate_task": CLASS_DELEGATE,
    # ── Card-shaped MCP server (kanban board) ───────────────────────────────
    # Read (Tier 1) — no-side-effect board/card/escalation lookups.
    "board_get": CLASS_READ,
    "card_list": CLASS_READ,
    "escalation_list": CLASS_READ,
    # Mutate (Tier 2) — card writes route to a single judge. card_delete is a
    # SOFT-delete per spec → Mutate, NOT Apex; config can still raise it later.
    "card_create": CLASS_MUTATE,
    "card_update": CLASS_MUTATE,
    "card_move": CLASS_MUTATE,
    "card_delete": CLASS_MUTATE,
    # Apex / human (Tier 4) — resolving an escalation IS the human sign-off; an
    # agent must NEVER do it autonomously. The capability map only expresses
    # Read/Mutate/Apex, so escalation_resolve is pinned to the Apex/human path
    # here AND added to _APEX_LOCKED below, so no config/args can ever lower it.
    "escalation_resolve": CLASS_APEX,
    # NOTE: column_*, tag_*, and artifact_* are deliberately left UNMAPPED. They
    # keep failing closed to Apex/Tier 4 (human) — the correct conservative
    # default until we scope them.
}

# Apex is absolute: these can NEVER be lowered by config or args.
#   terminal / execute_code → arbitrary shell / code execution.
#   escalation_resolve      → human sign-off; an agent must never resolve an
#                             escalation autonomously, so it is pinned absolute too.
_APEX_LOCKED: frozenset = frozenset({"terminal", "execute_code", "escalation_resolve"})

# ── Sensitive-path enum (Mutate hard-jump → Tier 4) ─────────────────────────
# Deterministic native-regex match on a Mutate call's path/target argument. On
# match: bypass Tier 2, hard-jump to Tier 4 (human). Never down-tiered.
_DEFAULT_SENSITIVE_PATH_PATTERNS: List[str] = [
    r".*\.env$",
    r"~/\.ssh/.*",
    r"/config/.*",
    r".*crontab.*",
    # ── add more sensitive-path patterns here ───────────────────────────────
    # (each is a deterministic hard-jump to Tier 4 / human; the free-family
    #  scorer is advisory-on-top only and can NEVER down-tier a match.)
]

# Argument keys that may carry a Mutate call's path/target.
_PATH_ARG_KEYS: Sequence[str] = ("path", "file_path", "filepath", "target", "filename")


@dataclass
class ClassifierConfig:
    """Resolved override surface. Empty = pure hardcoded fail-closed defaults."""
    tool_class_overrides: Dict[str, str] = field(default_factory=dict)
    sensitive_extra: List[str] = field(default_factory=list)
    enforce: bool = False  # observe-only by default; see the hook wiring


@dataclass
class Classification:
    tool: str
    tier: int
    capability_class: str
    reason: str
    sensitive_match: Optional[str] = None


def load_classifier_config() -> ClassifierConfig:
    """Read ``claunker.classifier.{tool_classes, sensitive_paths, enforce}`` from
    config.yaml via the (mtime,size)-cached ``load_config`` path. Missing/invalid
    config → empty (pure hardcoded defaults). Never raises."""
    try:
        from hermes_cli.config import load_config
        cl = ((load_config() or {}).get("claunker") or {}).get("classifier") or {}
    except Exception as exc:  # pragma: no cover — config IO failure
        logger.debug("claunker classifier config load failed: %s", exc)
        return ClassifierConfig()

    raw_classes = cl.get("tool_classes") if isinstance(cl.get("tool_classes"), dict) else {}
    overrides: Dict[str, str] = {}
    for name, klass in raw_classes.items():
        k = str(klass or "").strip().lower()
        # Config may only declare Read/Mutate/Dual/Apex — never "delegate"
        # (delegate-ness is intrinsic, not config-assignable).
        if isinstance(name, str) and k in _VALID_CONFIG_CLASSES:
            overrides[name] = k

    raw_paths = cl.get("sensitive_paths") if isinstance(cl.get("sensitive_paths"), list) else []
    sensitive_extra = [str(p) for p in raw_paths if isinstance(p, str) and p.strip()]

    return ClassifierConfig(
        tool_class_overrides=overrides,
        sensitive_extra=sensitive_extra,
        enforce=bool(cl.get("enforce", False)),
    )


def _max_class(a: str, b: str) -> str:
    return a if _CLASS_RANK.get(a, 4) >= _CLASS_RANK.get(b, 4) else b


def effective_class(
    tool_name: str,
    cfg: Optional[ClassifierConfig] = None,
    *,
    _enforce_apex_lock: bool = True,  # test seam ONLY; production keeps True
) -> str:
    """Resolve a tool's capability class, fail-closed.

    * Apex-locked tools (terminal, execute_code) → Apex, ignoring all input.
    * Hardcoded-mapped tool → its class; config may RAISE it (max), never lower.
    * Unmapped tool → Apex, unless config explicitly authorizes a lower class.
    """
    cfg = cfg or ClassifierConfig()
    if _enforce_apex_lock and tool_name in _APEX_LOCKED:
        return CLASS_APEX

    base = _DEFAULT_TOOL_CLASSES.get(tool_name)
    override = cfg.tool_class_overrides.get(tool_name)

    if base is not None:
        if base == CLASS_DELEGATE:
            return CLASS_DELEGATE  # config cannot reclassify delegate
        # Config may only raise a hardcoded default, never lower it.
        return _max_class(base, override) if override else base

    # Unmapped → fail-closed Apex, unless config authorizes a specific class.
    return override if override is not None else CLASS_APEX


def _resolve_entries_to_tools(entries: Sequence[str]) -> Set[str]:
    """Expand a delegate ``toolsets=`` list to concrete tool names. Each entry is
    resolved as a toolset name; if that yields nothing, it is treated as a bare
    tool name (the spec's ``delegate_task(["read_file"])`` form)."""
    tools: Set[str] = set()
    try:
        from toolsets import resolve_toolset
    except Exception:
        resolve_toolset = None  # type: ignore
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            continue
        resolved: List[str] = []
        if resolve_toolset is not None:
            try:
                resolved = resolve_toolset(entry) or []
            except Exception:
                resolved = []
        if resolved:
            tools.update(resolved)
        else:
            tools.add(entry)  # bare tool name
    return tools


def _max_class_over_tools(
    tools: Sequence[str], cfg: ClassifierConfig, *, _enforce_apex_lock: bool = True
) -> str:
    """Max capability class over a set of tools (fail-closed). Empty → Apex.
    A child that can itself delegate is treated as Apex (maximally capable)."""
    tool_set = {t for t in tools if isinstance(t, str) and t.strip()}
    if not tool_set:
        return CLASS_APEX  # an empty/unknown child scope is not "safe"
    worst = CLASS_READ
    for t in tool_set:
        klass = effective_class(t, cfg, _enforce_apex_lock=_enforce_apex_lock)
        if klass == CLASS_DELEGATE:
            klass = CLASS_APEX  # a child that can delegate can reach anything
        worst = _max_class(worst, klass)
        if worst == CLASS_APEX:
            break
    return worst


def _extract_path(args: Dict[str, Any]) -> Optional[str]:
    for key in _PATH_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _match_sensitive_path(
    args: Dict[str, Any], cfg: ClassifierConfig, *, _enforce: bool = True
) -> Optional[str]:
    """Return the first sensitive pattern matched by the call's path arg, else None."""
    if not _enforce:
        return None
    path = _extract_path(args)
    if not path:
        return None
    norm = path.replace("\\", "/")  # normalize Windows separators
    for pat in list(_DEFAULT_SENSITIVE_PATH_PATTERNS) + list(cfg.sensitive_extra):
        try:
            if re.search(pat, norm):
                return pat
        except re.error:
            continue
    return None


def _classify_delegate(
    args: Dict[str, Any],
    reachable_toolset: Optional[Sequence[str]],
    cfg: ClassifierConfig,
    *,
    _enforce_apex_lock: bool = True,
) -> Classification:
    child_entries: List[str] = []
    if isinstance(args.get("toolsets"), list):
        child_entries += [e for e in args["toolsets"] if isinstance(e, str)]
    for task in (args.get("tasks") or []):
        if isinstance(task, dict) and isinstance(task.get("toolsets"), list):
            child_entries += [e for e in task["toolsets"] if isinstance(e, str)]

    if child_entries:
        child_tools = _resolve_entries_to_tools(child_entries)
        child_class = _max_class_over_tools(child_tools, cfg, _enforce_apex_lock=_enforce_apex_lock)
        why = f"delegate scored by child toolsets {sorted(set(child_entries))}"
    elif reachable_toolset:
        # No narrowing: the child inherits the parent's reachable set.
        child_tools = _resolve_entries_to_tools(list(reachable_toolset))
        child_class = _max_class_over_tools(child_tools, cfg, _enforce_apex_lock=_enforce_apex_lock)
        why = "delegate with no toolsets= → child inherits parent reachable set"
    else:
        # No child scope AND no known parent reachable set → fail-closed Apex.
        child_class = CLASS_APEX
        why = "delegate with unknown child scope → fail-closed Apex"

    return Classification(
        tool="delegate_task",
        tier=_CLASS_TO_TIER[child_class],
        capability_class=f"delegate:{child_class}",
        reason=why,
    )


def classify_tool_call(
    tool_name: str,
    args: Optional[Dict[str, Any]],
    reachable_toolset: Optional[Sequence[str]] = None,
    cfg: Optional[ClassifierConfig] = None,
    *,
    _enforce_apex_lock: bool = True,   # test seam ONLY
    _enforce_sensitive: bool = True,    # test seam ONLY
) -> Classification:
    """Compute the tier for a single tool call. Deterministic; schema/args + the
    reachable set ONLY — never the operator's intent text."""
    cfg = cfg or load_classifier_config()
    args = args if isinstance(args, dict) else {}

    # delegate_task is dynamic: tier by the CHILD's toolset, not the parent.
    if tool_name == "delegate_task":
        return _classify_delegate(args, reachable_toolset, cfg, _enforce_apex_lock=_enforce_apex_lock)

    klass = effective_class(tool_name, cfg, _enforce_apex_lock=_enforce_apex_lock)
    if klass == CLASS_DELEGATE:
        # Only delegate_task is delegate; any other tool mapped delegate is
        # unexpected → route through the (fail-closed) delegate path.
        return _classify_delegate(args, reachable_toolset, cfg, _enforce_apex_lock=_enforce_apex_lock)

    tier = _CLASS_TO_TIER[klass]

    # Mutate calls run the sensitive-path hard-jump.
    if klass == CLASS_MUTATE:
        hit = _match_sensitive_path(args, cfg, _enforce=_enforce_sensitive)
        if hit is not None:
            return Classification(
                tool=tool_name,
                tier=TIER_HUMAN,
                capability_class=klass,
                reason=f"sensitive-path hard-jump (Mutate→human): matched {hit!r}",
                sensitive_match=hit,
            )

    reason = {
        CLASS_READ: "read-class tool → self-accept",
        CLASS_MUTATE: "mutate-class tool → single judge",
        CLASS_APEX: ("apex-class tool → human (absolute)" if tool_name in _APEX_LOCKED
                     else "apex-class (unmapped/fail-closed) → human"),
    }.get(klass, f"{klass} → tier {tier}")
    return Classification(tool=tool_name, tier=tier, capability_class=klass, reason=reason)


def reachable_floor(
    reachable_toolset: Sequence[str], cfg: Optional[ClassifierConfig] = None
) -> int:
    """Dispatch-level floor: the tier of the max capability class over the whole
    reachable set. (Per-call tiering above is the gate's actionable output; this
    is the dispatch floor the spec references.)"""
    cfg = cfg or load_classifier_config()
    tools = _resolve_entries_to_tools(list(reachable_toolset))
    return _CLASS_TO_TIER[_max_class_over_tools(tools, cfg)]


# ── Observability seam (last classification at the gate) ────────────────────
_LAST_CLASSIFICATION: Optional[Classification] = None


def record_last(cls: Classification) -> None:
    """Stash the most recent gate classification (observability / wiring tests)."""
    global _LAST_CLASSIFICATION
    _LAST_CLASSIFICATION = cls


def get_last_classification() -> Optional[Classification]:
    return _LAST_CLASSIFICATION


def route_for_tier(tier: int) -> str:
    """Map a tier to its routing target. Tier 3 (dual sign-off) DEGRADES TO HUMAN
    until a third decorrelated family is wired."""
    if tier <= TIER_SELF_ACCEPT:
        return "self_accept"
    if tier == TIER_SINGLE_JUDGE:
        return "single_judge"
    # Tier 3 dual sign-off degrades to human for now; Tier 4 is human.
    return "human"
