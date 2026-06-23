"""Canonical home of the Claunker deterministic dispatch classifier.

This package holds the source-of-record for ``claunker_classifier.py``. The
chassis (``hermes-agent``) vendors a stamped copy via ``scripts/deploy_classifier.py``;
edits are made HERE and deployed, never the other way around.

Kept import-light on purpose: importing ``hermes_cli.claunker_classifier`` must
not pull in the full chassis ``hermes_cli`` package, so the standalone test path
(``tests/hermes_cli/test_claunker_classifier.py``) resolves cleanly.
"""
