"""FT-009 controls test: judge-unavailable must route to escalate/human, never
to an architect-improvisable bare error or a self-verified accept.

This asserts the ROUTING OUTCOME of the judge_verdict fail-safe — the deterministic
contract that makes architect self-verification impossible to justify. It does NOT
assert "a verdict came back" (a self-verified accept also produces a verdict — that
is exactly the FT-009 bug). It goes RED if the plugin regresses to delivering a
judge outage as `success:false`/bare error, or as anything an architect can read as
"you decide."

Run (green, against the live fixed plugin):
    uv run --no-sync python -m pytest plugins/judge-verdict/test_ft009_judge_unavailable.py -q
    # or, no pytest:
    uv run --no-sync python plugins/judge-verdict/test_ft009_judge_unavailable.py

Demonstrate it is a real control (RED, against the pre-fix backup):
    FT009_TOOLS_PATH=plugins/judge-verdict/tools.py.bak.ft009 uv run --no-sync \
        python plugins/judge-verdict/test_ft009_judge_unavailable.py
"""

import importlib.util
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_plugin_tools():
    """Load the plugin's tools.py (with its `from .schemas import ...`) under a
    synthetic package. FT009_TOOLS_PATH overrides which tools.py is loaded so the
    same test can be pointed at the pre-fix backup to prove it goes RED."""
    tools_path = os.environ.get("FT009_TOOLS_PATH") or os.path.join(_HERE, "tools.py")
    tools_path = os.path.abspath(tools_path)

    pkg_name = "ft009_judge_pkg"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [_HERE]  # schemas.py always resolved from the plugin dir
    sys.modules[pkg_name] = pkg

    def _load_sub(modname, path):
        # Force a source loader so non-.py paths (e.g. tools.py.bak.ft009, used
        # for the RED demonstration) still load as Python source.
        import importlib.machinery
        fqmn = f"{pkg_name}.{modname}"
        loader = importlib.machinery.SourceFileLoader(fqmn, path)
        spec = importlib.util.spec_from_loader(fqmn, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[fqmn] = mod
        loader.exec_module(mod)
        return mod

    _load_sub("schemas", os.path.join(_HERE, "schemas.py"))
    return _load_sub("tools", tools_path)


TOOLS = _load_plugin_tools()


# --- Fakes -----------------------------------------------------------------
class _Result:
    """Stand-in for PluginLlmStructuredResult (only .parsed is read)."""
    def __init__(self, parsed):
        self.parsed = parsed


class _FakeLLM:
    def __init__(self, behavior):
        self._behavior = behavior

    def complete_structured(self, **kwargs):
        return self._behavior(kwargs)


class _FakeCtx:
    def __init__(self, behavior):
        self.llm = _FakeLLM(behavior)


# A genuinely SUBSTANTIVE dispatch (not the self-accept tier).
SUBSTANTIVE = {
    "task_spec": "Implement a pure function categorize(n)->str per the spec.",
    "acceptance_criteria": "negative<0, zero==0, small 1..9, large>=10; all asserts pass.",
    "executor_output": "def categorize(n): ...",
    "risk_tier": "high",
}


def _call(behavior, params=SUBSTANTIVE):
    handler = TOOLS.make_handler(_FakeCtx(behavior))
    return json.loads(handler(params))


# --- The controls ----------------------------------------------------------
def test_judge_503_routes_to_escalate_not_accept():
    """503 (UNAVAILABLE) must yield an unambiguous escalate-to-human, NOT a bare
    error and NOT an accept. RED if the fail-safe regresses to the FT-009 shape."""
    def raise_503(_kwargs):
        raise RuntimeError("Gemini HTTP 503 (UNAVAILABLE): judge backend down")

    out = _call(raise_503)

    # Routing outcome: escalate, and explicitly NOT a self-verifiable pass.
    assert out.get("verdict") == "escalate", out
    assert out.get("verdict") != "accept", out
    # Must NOT be delivered as a bare error the architect can improvise around.
    assert out.get("success") is True, out          # FT-009: was False (rendered as tool error)
    assert out.get("judge_available") is False, out  # FT-009: key was absent
    assert out.get("reason") == "judge_unavailable", out


def test_unparseable_judge_output_routes_to_escalate():
    """Judge ran but returned no schema-valid verdict — same hard halt, same
    unambiguous shape (not a bare error, not an accept)."""
    out = _call(lambda _k: _Result(parsed=None))
    assert out.get("verdict") == "escalate", out
    assert out.get("verdict") != "accept", out
    assert out.get("success") is True, out
    assert out.get("judge_available") is False, out


def test_normal_accept_unchanged():
    """Happy path regression: a real Gemini accept still passes through as a
    judge-rendered verdict, distinguishable from the outage fail-safe."""
    parsed = {"verdict": "accept", "rationale": "meets all criteria", "confidence": "high"}
    out = _call(lambda _k: _Result(parsed=parsed))
    assert out.get("verdict") == "accept", out
    assert out.get("success") is True, out
    assert out.get("judge_available") is True, out   # distinguishes real verdict from outage


def test_empty_criteria_refusal_unchanged():
    """The empty-criteria refusal must remain a loud success:false refusal — NOT
    converted into an escalate (that discipline guard is out of FT-009 scope)."""
    params = dict(SUBSTANTIVE, acceptance_criteria="   ")
    out = _call(lambda _k: _Result(parsed={"verdict": "accept"}), params=params)
    assert out.get("success") is False, out
    assert "verdict" not in out, out


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    src = os.environ.get("FT009_TOOLS_PATH", "<live tools.py>")
    print(f"FT-009 controls test — tools under test: {src}")
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
    sys.exit(_main())
