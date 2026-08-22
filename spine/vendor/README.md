# Vendored: sync-merge.js

- **Source repo**: `icey2488/Kanbantt` (https://github.com/icey2488/Kanbantt.git)
- **Source path**: `src/lib/sync-merge.js`
- **Source commit**: `2d5a68651ad9224d831daf85f81b5e1859ff7180` (HEAD, working tree clean at vendor time)
- **Copied**: 2026-08-22

## Rule

`sync-merge.js` in this directory is copied **VERBATIM** from the source repo above
and is **never edited here**. It is the proven convergent merge core (commutative,
associative, idempotent — see the test matrix) and the spine's Drive-sync bridge
(`spine/merge_bridge.py`, `spine/vendor/merge_runner.js`) treats it as a black box.

To pick up an upstream change, re-vendor: copy the file again from
`icey2488/Kanbantt` at the new commit, update the source commit hash and copied
date above, and re-copy its test file into `spine/vendor/tests/`. Any adapter code
needed to reconcile shape differences between the spine's blob and what
`sync-merge.js` expects belongs on the **Python side** (`spine/drive_sync.py` /
`spine/merge_bridge.py`) — never in this vendored file.

## Tests

`spine/vendor/tests/sync-merge.test.js` is the source repo's own test file for
`sync-merge.js` (same commit), with only its relative import path adjusted
(`./sync-merge.js` -> `../sync-merge.js` to match this vendor layout). Run with:

```
node --test spine/vendor/tests/sync-merge.test.js
```

These are the upstream convergence-proof tests (commutativity / associativity /
idempotency / conflict-id determinism / resolver precedence). They are part of
the integration suite that validates the vendored core still behaves as proven
after any re-vendor.
