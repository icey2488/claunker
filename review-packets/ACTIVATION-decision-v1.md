# Activation decision document v1 (as reviewed)

Reviewed by hostile teardown 2026-07-31; adjudication and ratified v2 set in ACTIVATION-decision-adjudication-r1.md.

DECISION 1 — Record, do not re-engineer. Register entry #13: production spine storage is SQLite, not the ratified Drive-durable blob; durability via scheduled Restic to offsite object storage; restore path = the standing drill. Enforcement test #13 asserts the backend.
DECISION 2 — Consequence: production multi-client convergence is the single live server over MCP, not CRDT blob merge; the convergence machinery (merge core, JS spine server, Drive persistence port) reclassified as the LOCAL/BYO-spine variant — kept, test-covered, dormant, no production role.
DECISION 3 — Superseded roadmap item retired: "production persistence port" — restart durability already real via SQLite.
DECISION 4 — Client activation: deployed board connects over the authenticating tunnel with a bearer token entered in the settings modal; in-memory by default, persisted under explicit remember-token opt-in; standing client grant recorded. Acceptance: (1) live cards render; (2) a newly dispatched card appears within one poll interval without reload.
