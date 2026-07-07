"""FT-013: Hermes gateway config validator.

Parses the live Hermes config (%LOCALAPPDATA%/hermes/config.yaml) and enforces
the plugin LLM block pairing invariant:

  allow_model_override: true    requires allowed_models key PRESENT and non-null
  allow_provider_override: true requires allowed_providers key PRESENT and non-null

An EMPTY list ([]) is legal — deny-all, fail-closed per audit case (c).
flags false/absent with no allowlist is also legal.

Exits nonzero on violation, naming the plugin and the missing key.

# TODO: classifier file-hash verification vs known-good checksum gates enforce:true
# at arming time (Gemini ruling 2026-07-07; not implemented this pass).
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    sys.exit("FATAL: pyyaml is required — install it with: uv sync")


def _default_config_path() -> str:
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if not localappdata:
        sys.exit("FATAL: LOCALAPPDATA environment variable not set")
    return os.path.join(localappdata, "hermes", "config.yaml")


def validate_config(config: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Walk every plugin llm block and return (plugin_name, error_message) pairs.

    Invariant: allow_model_override:true requires allowed_models PRESENT and non-null;
               allow_provider_override:true requires allowed_providers PRESENT and non-null.
    An empty list is legal (deny-all); false/absent with no allowlist is also legal.
    """
    violations: List[Tuple[str, str]] = []
    entries = (config.get("plugins") or {}).get("entries") or {}
    for plugin_name, plugin_conf in entries.items():
        if not isinstance(plugin_conf, dict):
            continue
        llm = plugin_conf.get("llm")
        if not isinstance(llm, dict):
            continue

        if llm.get("allow_model_override") is True:
            if "allowed_models" not in llm or llm["allowed_models"] is None:
                violations.append((
                    plugin_name,
                    "allow_model_override:true requires allowed_models key PRESENT and non-null "
                    "([] is legal — deny-all; absent/null is the open hole)",
                ))

        if llm.get("allow_provider_override") is True:
            if "allowed_providers" not in llm or llm["allowed_providers"] is None:
                violations.append((
                    plugin_name,
                    "allow_provider_override:true requires allowed_providers key PRESENT and non-null "
                    "([] is legal — deny-all; absent/null is the open hole)",
                ))

    return violations


def main(config_path: Optional[str] = None) -> int:
    path = config_path or _default_config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
    except FileNotFoundError:
        print(f"FATAL: config not found at {path}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FATAL: could not parse {path}: {exc}", file=sys.stderr)
        return 1

    if not isinstance(config, dict):
        print(f"FATAL: config root is not a mapping in {path}", file=sys.stderr)
        return 1

    violations = validate_config(config)
    if violations:
        for plugin_name, msg in violations:
            print(f"CONFIG_VIOLATION plugin={plugin_name!r}: {msg}", file=sys.stderr)
        return 1

    print(f"validate-hermes-config: OK ({path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
