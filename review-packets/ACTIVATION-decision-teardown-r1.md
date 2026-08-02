# Activation decision — adversarial teardown round 1 (Gemini, raw)

Received 2026-07-31. Findings verbatim; adjudication in ACTIVATION-decision-adjudication-r1.md.

F1 [BLOCKER] RPO downgrade and backup corruption risk laundered as equivalence — scheduled Restic reading a live SQLite file during an open write transaction risks torn-read corruption; RPO silently downgraded from near-real-time to the backup interval. Fix: document the RPO downgrade; mandate sqlite3 .backup/Litestream, never raw copies of the live database.
F2 [MAJOR] Silent loss of offline-first partition tolerance — the CRDT architecture implied disconnected clients could write locally and merge; single-live-server silently drops that. Fix: declare the abandonment; define the server's conflict-resolution strategy.
F3 [MAJOR] Dormant implementation drift and false security — the JS variant's tests pass against its own frozen mocks while the Python server evolves; "test-covered" becomes a false guarantee. Fix: dual-target contract suite, or deprecate and delete.
F4 [BLOCKER] Missing authentication lifecycle artifact and token exposure — generation, rotation, revocation unspecified; remember-token persists a non-expiring token vulnerable to XSS exfiltration with no revocation mechanism. Fix: define the lifecycle; mandate expiration; server-side revocation.
F5 [MAJOR] Tautological enforcement test games the register convention — assert backend=='sqlite' passes while backups silently stop; tests the technology choice, not the divergent behavior (durability). Fix: assert effects — recent valid backup within the RPO window.
F6 [MINOR] Illegitimate authorization of roadmap retirement — a register entry cancels a roadmap item belonging to the JS/Drive architecture; the BYO variant's missing persistence loses its tracking. Fix: re-scope the item to the BYO variant rather than retiring it.
F7 [BLOCKER] Acceptance criteria pass while system is incapable of writes — both criteria are reads; a read-only token or missing write path passes activation. Fix: add a browser-initiated write that persists and propagates.

BLOCKER: 3, MAJOR: 3, MINOR: 1
