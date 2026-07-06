"""Live smoke test — judge_verdict attribution assertion (RC-001).

Drives the FULL plugin path:
  config.yaml trust gate → PluginLlm → Gemini API → PluginLlmStructuredResult

Asserts:
  - success: True
  - judge_available: True
  - resolved provider == "gemini"
  - resolved model  == "gemini-3.5-flash"

Run:
    uv run --no-sync python smoke_judge_attribution.py
    (must be run from repo root with hermes-agent venv on PYTHONPATH)
"""

import json
import sys
import os

# Inject hermes-agent into the path so the real PluginLlm is available.
_HERMES_AGENT = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "hermes", "hermes-agent",
)
if _HERMES_AGENT not in sys.path:
    sys.path.insert(0, _HERMES_AGENT)

# Load the installed plugin from the live hermes plugins dir.
_PLUGIN_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "hermes", "plugins", "judge-verdict",
)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

# Trivial test case.
SMOKE_PARAMS = {
    "task_spec": "Write a Python function add(a, b) that returns a+b.",
    "acceptance_criteria": "add(1, 2) returns 3; add(-1, 1) returns 0.",
    "executor_output": "def add(a, b):\n    return a + b",
    "risk_tier": "low",
}


def main():
    from agent.plugin_llm import PluginLlm, PluginLlmStructuredResult

    captured: list[PluginLlmStructuredResult] = []

    class _CapturingLlm(PluginLlm):
        """Thin wrapper that captures the StructuredResult for attribution check."""
        def complete_structured(self, **kwargs):
            result = super().complete_structured(**kwargs)
            captured.append(result)
            return result

    ctx_llm = _CapturingLlm(plugin_id="judge-verdict")

    class _Ctx:
        llm = ctx_llm

    # Load the installed tools.py under a synthetic package so relative
    # imports (from .schemas import ...) resolve correctly — same technique
    # as the FT-009 test.
    import importlib.util
    import importlib.machinery
    import types

    pkg_name = "smoke_judge_pkg"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [_PLUGIN_DIR]
    sys.modules[pkg_name] = pkg

    def _load_sub(modname):
        fqmn = f"{pkg_name}.{modname}"
        path = os.path.join(_PLUGIN_DIR, f"{modname}.py")
        loader = importlib.machinery.SourceFileLoader(fqmn, path)
        spec = importlib.util.spec_from_loader(fqmn, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[fqmn] = mod
        loader.exec_module(mod)
        return mod

    _load_sub("schemas")
    judge_tools = _load_sub("tools")

    handler = judge_tools.make_handler(_Ctx())
    raw = handler(SMOKE_PARAMS)
    verdict = json.loads(raw)

    # --- Assertions ---
    errors = []

    if verdict.get("success") is not True:
        errors.append(f"success expected True, got {verdict.get('success')!r}")

    if verdict.get("judge_available") is not True:
        errors.append(
            f"judge_available expected True, got {verdict.get('judge_available')!r}\n"
            f"  full response: {verdict}"
        )

    if not captured:
        errors.append("complete_structured was never called — attribution unverifiable")
    else:
        r = captured[0]
        if r.provider != "gemini":
            errors.append(f"provider expected 'gemini', got {r.provider!r}")
        if r.model != "gemini-3.5-flash":
            errors.append(f"model expected 'gemini-3.5-flash', got {r.model!r}")

    if errors:
        print("SMOKE FAIL")
        for e in errors:
            print(f"  FAIL: {e}")
        sys.exit(1)

    # Attribution line — verbatim per RC-001.
    r = captured[0]
    attribution_line = f"provider={r.provider} model={r.model}"
    print(f"SMOKE PASS")
    print(f"  verdict:         {verdict.get('verdict')}")
    print(f"  judge_available: {verdict.get('judge_available')}")
    print(f"  attribution:     {attribution_line}")
    print(f"  rationale:       {verdict.get('rationale', '')[:120]}")


if __name__ == "__main__":
    main()
