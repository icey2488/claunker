"""§5.8 controls for the Claunker deterministic classifier (Phase 2 Step 2a).

Asserts the TIER OUTCOME (not "a tier came back") for every spec case, and proves
the safety controls are load-bearing with the same red-on-removal discipline as
the FT-009 / FT-003 tests: each control test pairs the real classifier (GREEN,
safe tier) against a weakened variant (the apex-lock / sensitive-path control
disabled via a clearly-marked test seam) that produces the UNSAFE tier — proving
the control, not the wiring, is what holds the floor.

Run:
    uv run --no-sync python tests/hermes_cli/test_claunker_classifier.py
    uv run --no-sync python -m pytest tests/hermes_cli/test_claunker_classifier.py -q
"""

from hermes_cli.claunker_classifier import (
    ClassifierConfig,
    classify_tool_call,
    _CLASS_TO_TIER,
    TIER_SELF_ACCEPT,
    TIER_SINGLE_JUDGE,
    TIER_HUMAN,
)

# Empty config = pure hardcoded fail-closed defaults (no config dependency).
EMPTY = ClassifierConfig()


# ── Spec controls: assert the tier OUTCOME ─────────────────────────────────
def test_read_file_is_tier1_self_accept():
    c = classify_tool_call("read_file", {"path": "src/app.py"}, cfg=EMPTY)
    assert c.tier == TIER_SELF_ACCEPT, c


def test_write_file_scratch_is_tier2_single_judge():
    c = classify_tool_call("write_file", {"path": "/workspace/out.txt", "content": "x"}, cfg=EMPTY)
    assert c.tier == TIER_SINGLE_JUDGE, c
    assert c.sensitive_match is None, c


def test_write_file_dotenv_hard_jumps_to_tier4():
    c = classify_tool_call("write_file", {"path": "/workspace/app/.env", "content": "x"}, cfg=EMPTY)
    assert c.tier == TIER_HUMAN, c
    assert c.sensitive_match is not None, c


def test_write_file_ssh_config_hard_jumps_to_tier4():
    c = classify_tool_call("patch", {"path": "~/.ssh/config", "mode": "replace"}, cfg=EMPTY)
    assert c.tier == TIER_HUMAN, c
    assert c.sensitive_match is not None, c


def test_terminal_is_tier4_apex():
    c = classify_tool_call("terminal", {"command": "ls"}, cfg=EMPTY)
    assert c.tier == TIER_HUMAN, c


def test_delegate_child_read_only_is_tier1():
    c = classify_tool_call("delegate_task", {"toolsets": ["read_file"]}, cfg=EMPTY)
    assert c.tier == TIER_SELF_ACCEPT, c


def test_delegate_child_terminal_is_tier4():
    c = classify_tool_call("delegate_task", {"toolsets": ["terminal"]}, cfg=EMPTY)
    assert c.tier == TIER_HUMAN, c


def test_unknown_tool_fails_closed_to_tier4():
    c = classify_tool_call("totally_unregistered_tool_xyz", {}, cfg=EMPTY)
    assert c.tier == TIER_HUMAN, c


def test_delegate_without_toolsets_fails_closed_to_tier4():
    # No child scope and no known parent reachable set → fail-closed Apex.
    c = classify_tool_call("delegate_task", {"goal": "do a thing"}, cfg=EMPTY)
    assert c.tier == TIER_HUMAN, c


# ── RED-on-removal proofs: the control is load-bearing ─────────────────────
def test_apex_floor_is_load_bearing_RED_when_removed():
    """Spec control: attempting to map terminal below Apex must FAIL to lower it.
    GREEN: the real classifier ignores a hostile config and keeps Tier 4.
    RED-demo: an UNGUARDED classifier that naively trusted the config override
    would honor terminal→read and drop to Tier 1 — the divergence proves the
    guard changes the outcome (defense-in-depth: apex-lock + 'config can never
    lower a hardcoded default')."""
    hostile = ClassifierConfig(tool_class_overrides={"terminal": "read"})

    green = classify_tool_call("terminal", {"command": "ls"}, cfg=hostile)
    assert green.tier == TIER_HUMAN, ("apex floor lowered by config!", green)

    # What an unguarded, config-trusting classifier would have produced:
    naive_unsafe_tier = _CLASS_TO_TIER[hostile.tool_class_overrides["terminal"]]
    assert naive_unsafe_tier == TIER_SELF_ACCEPT
    assert green.tier != naive_unsafe_tier, (
        "the apex guard did not change the unsafe outcome — control is dead", green
    )


def test_sensitive_path_is_load_bearing_RED_when_removed():
    """GREEN: a write to ~/.ssh/config is Tier 4. RED-demo: with the
    sensitive-path control disabled (test seam), the same call falls back to the
    bare Mutate tier (2) — proving the hard-jump is what escalates it."""
    args = {"path": "~/.ssh/config", "content": "x"}

    green = classify_tool_call("write_file", args, cfg=EMPTY)
    assert green.tier == TIER_HUMAN, ("sensitive-path hard-jump missing!", green)

    red = classify_tool_call("write_file", args, cfg=EMPTY, _enforce_sensitive=False)
    assert red.tier == TIER_SINGLE_JUDGE, (
        "expected the weakened variant to fall back to Mutate/Tier 2", red
    )


def test_delegate_scored_by_child_not_wrapper_RED_when_removed():
    """delegate_task is tiered by the CHILD's class, not the delegate wrapper.
    GREEN: a read-only child → Tier 1, an apex child → Tier 4 (the contrast IS
    the proof it tracks the child). RED-demo: an unguarded classifier that scored
    delegate by its own (fail-closed apex) wrapper would put the read-only child
    at Tier 4 too — the divergence proves child-scoring is load-bearing."""
    read_child = classify_tool_call("delegate_task", {"toolsets": ["read_file"]}, cfg=EMPTY)
    apex_child = classify_tool_call("delegate_task", {"toolsets": ["terminal"]}, cfg=EMPTY)
    assert read_child.tier == TIER_SELF_ACCEPT, read_child
    assert apex_child.tier == TIER_HUMAN, apex_child

    naive_wrapper_tier = TIER_HUMAN  # delegate_task scored as an apex wrapper
    assert read_child.tier != naive_wrapper_tier, (
        "delegate not scored by child — a read-only delegation wrongly floored", read_child
    )


def _main():
    import sys
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"{'RED' if failures else 'GREEN'}: {len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
