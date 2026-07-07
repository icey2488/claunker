"""FT-013 §5.8 pytest — launch-path config guard (validate-hermes-config).

RED-CONTRAST case (the confirmed open hole):
  allow_model_override:true + absent allowlist -> FAIL validation.

Green cases: proper pairing, empty-list allowlist, flags-false-without-allowlist,
             flags-absent-without-allowlist, no-plugins-block.

Live config: parse the LIVE config path and assert it currently passes.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

# ── load the script by file path (hyphenated name, not a package) ─────────────
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "validate-hermes-config.py")
_spec = importlib.util.spec_from_file_location("validate_hermes_config", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate_config = _mod.validate_config
main = _mod.main


# ── RED-CONTRAST cases (violations) ──────────────────────────────────────────

def test_model_override_true_absent_allowlist_is_violation():
    """The confirmed open hole: allow_model_override:true with NO allowed_models key."""
    cfg = {
        "plugins": {
            "entries": {
                "judge-verdict": {
                    "llm": {
                        "allow_model_override": True,
                        # allowed_models key is entirely absent — the open hole
                    }
                }
            }
        }
    }
    violations = validate_config(cfg)
    assert len(violations) == 1
    plugin_name, msg = violations[0]
    assert plugin_name == "judge-verdict"
    assert "allowed_models" in msg


def test_provider_override_true_absent_allowlist_is_violation():
    """allow_provider_override:true with NO allowed_providers key."""
    cfg = {
        "plugins": {
            "entries": {
                "my-plugin": {
                    "llm": {
                        "allow_provider_override": True,
                        # allowed_providers absent
                    }
                }
            }
        }
    }
    violations = validate_config(cfg)
    assert len(violations) == 1
    plugin_name, msg = violations[0]
    assert plugin_name == "my-plugin"
    assert "allowed_providers" in msg


def test_null_allowlist_is_violation():
    """allow_model_override:true with allowed_models: null (present but null) is a violation."""
    cfg = {
        "plugins": {
            "entries": {
                "p": {
                    "llm": {
                        "allow_model_override": True,
                        "allowed_models": None,  # explicitly null
                    }
                }
            }
        }
    }
    violations = validate_config(cfg)
    assert len(violations) == 1
    assert "allowed_models" in violations[0][1]


def test_both_overrides_true_both_absent_yields_two_violations():
    """Two violations for one plugin that enables both overrides without allowlists."""
    cfg = {
        "plugins": {
            "entries": {
                "bad-plugin": {
                    "llm": {
                        "allow_model_override": True,
                        "allow_provider_override": True,
                        # neither allowlist present
                    }
                }
            }
        }
    }
    violations = validate_config(cfg)
    assert len(violations) == 2
    keys = {msg for _, msg in violations}
    assert any("allowed_models" in m for m in keys)
    assert any("allowed_providers" in m for m in keys)


# ── GREEN cases (no violations) ───────────────────────────────────────────────

def test_proper_pairing_passes():
    """Both override flags true AND both allowlists present and non-null: valid."""
    cfg = {
        "plugins": {
            "entries": {
                "judge-verdict": {
                    "llm": {
                        "allow_model_override": True,
                        "allow_provider_override": True,
                        "allowed_models": ["gemini-3.5-flash"],
                        "allowed_providers": ["gemini"],
                    }
                }
            }
        }
    }
    assert validate_config(cfg) == []


def test_empty_list_allowlist_passes():
    """Empty list is legal — deny-all, fail-closed (audit case c)."""
    cfg = {
        "plugins": {
            "entries": {
                "judge-verdict": {
                    "llm": {
                        "allow_model_override": True,
                        "allowed_models": [],  # deny-all
                    }
                }
            }
        }
    }
    assert validate_config(cfg) == []


def test_flags_false_without_allowlist_passes():
    """Flags explicitly false without allowlists: no invariant triggered."""
    cfg = {
        "plugins": {
            "entries": {
                "judge-verdict": {
                    "llm": {
                        "allow_model_override": False,
                        "allow_provider_override": False,
                    }
                }
            }
        }
    }
    assert validate_config(cfg) == []


def test_flags_absent_without_allowlist_passes():
    """Override flags absent (not even in the llm block): no invariant triggered."""
    cfg = {
        "plugins": {
            "entries": {
                "judge-verdict": {
                    "llm": {}
                }
            }
        }
    }
    assert validate_config(cfg) == []


def test_no_plugins_block_passes():
    """Config with no plugins section at all: valid."""
    assert validate_config({}) == []


def test_plugin_with_no_llm_block_passes():
    """A plugin entry that has no llm sub-block is skipped cleanly."""
    cfg = {
        "plugins": {
            "entries": {
                "no-llm-plugin": {
                    "some_other_key": True,
                }
            }
        }
    }
    assert validate_config(cfg) == []


# ── LIVE CONFIG — parse the real running config and assert it passes ──────────

def test_live_config_passes():
    """The LIVE Hermes config at %LOCALAPPDATA%/hermes/config.yaml must currently pass."""
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if not localappdata:
        pytest.skip("LOCALAPPDATA not set")
    config_path = os.path.join(localappdata, "hermes", "config.yaml")
    if not os.path.exists(config_path):
        pytest.skip(f"Live config not found at {config_path}")
    rc = main(config_path)
    assert rc == 0, f"Live Hermes config failed FT-013 validation: {config_path}"
