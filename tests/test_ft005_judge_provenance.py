"""FT-005 controls: durable judge-call provenance log (append-only, fail-open).

Verifies three properties:
  1. Success path writes a JSONL line with resolved attribution + verdict_verdict.
  2. Trust-error path (PluginLlmTrustError by name) logs outcome=trust_error.
  3. Log-write failure is fail-open — the handler returns a verdict regardless.

The plugin is loaded via importlib (same technique as the FT-009 test) so the
suite does not require Hermes on the Python path.
"""

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import sys
import types

import pytest

_PLUGIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "judge-verdict",
)


def _load_tools():
    pkg_name = "ft005_judge_pkg"
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
    return _load_sub("tools")


TOOLS = _load_tools()

_ORIGINAL_LOG_PATH = TOOLS._LOG_PATH


# ---------------------------------------------------------------------------
# Fakes — identical contract to the FT-009 test infrastructure
# ---------------------------------------------------------------------------

class _Result:
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


SUBSTANTIVE = {
    "task_spec": "Add a pure function double(x) that returns 2*x.",
    "acceptance_criteria": "double(3)==6; double(-1)==-2.",
    "executor_output": "def double(x):\n    return 2 * x",
    "risk_tier": "standard",
}


@pytest.fixture(autouse=True)
def _restore_log_path():
    yield
    TOOLS._LOG_PATH = _ORIGINAL_LOG_PATH


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_success_writes_verdict_log_line(tmp_path):
    log_file = tmp_path / "judge_provenance.jsonl"
    TOOLS._LOG_PATH = log_file

    parsed = {"verdict": "accept", "rationale": "all criteria met", "confidence": "high"}
    handler = TOOLS.make_handler(_FakeCtx(lambda _k: _Result(parsed=parsed)))
    result = json.loads(handler(SUBSTANTIVE))

    assert result.get("verdict") == "accept"
    assert result.get("success") is True

    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1, f"expected 1 log line, got {len(lines)}"
    entry = json.loads(lines[0])

    assert entry["provider"] == "gemini"
    assert entry["model"] == "gemini-3.5-flash"
    assert entry["outcome"] == "verdict"
    assert entry["verdict_verdict"] == "accept"
    assert len(entry["request_sha256"]) == 64
    assert isinstance(entry["duration_ms"], int)
    assert "ts" in entry


def test_trust_error_logs_trust_error_outcome(tmp_path):
    log_file = tmp_path / "judge_provenance.jsonl"
    TOOLS._LOG_PATH = log_file

    class PluginLlmTrustError(RuntimeError):
        pass

    def raise_trust(_k):
        raise PluginLlmTrustError("pin changed off allowlist")

    handler = TOOLS.make_handler(_FakeCtx(raise_trust))
    result = json.loads(handler(SUBSTANTIVE))

    assert result.get("verdict") == "escalate"
    assert result.get("judge_available") is False

    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert entry["outcome"] == "trust_error"
    assert entry["provider"] == "gemini"
    assert entry["model"] == "gemini-3.5-flash"
    assert "verdict_verdict" not in entry


def test_api_error_logs_api_error_outcome(tmp_path):
    log_file = tmp_path / "judge_provenance.jsonl"
    TOOLS._LOG_PATH = log_file

    def raise_api(_k):
        raise RuntimeError("HTTP 503 UNAVAILABLE")

    handler = TOOLS.make_handler(_FakeCtx(raise_api))
    result = json.loads(handler(SUBSTANTIVE))

    assert result.get("verdict") == "escalate"
    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert entry["outcome"] == "api_error"


def test_log_write_failure_does_not_raise(tmp_path):
    # tmp_path is a directory; open(directory, "a") raises PermissionError/IsADirectoryError.
    # _append_log's internal try/except catches it and the handler completes normally.
    TOOLS._LOG_PATH = tmp_path

    parsed = {"verdict": "accept", "rationale": "ok", "confidence": "high"}
    handler = TOOLS.make_handler(_FakeCtx(lambda _k: _Result(parsed=parsed)))
    result = json.loads(handler(SUBSTANTIVE))

    assert result.get("verdict") == "accept"
    assert result.get("success") is True
