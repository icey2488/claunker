# claunker-spine

The Claunker spine: a governed MCP board server (JSON-RPC 2.0 over Streamable HTTP)
that backs Kanbantt ([github.com/icey2488/Kanbantt](https://github.com/icey2488/Kanbantt),
live at [kanbantt.icehunter.net](https://kanbantt.icehunter.net)). The spine holds the
durable orchestration state in SQLite and projects its Tasks onto the Card shape
Kanbantt renders. Authorization is tiered (tier 1 self-accept through tier 4 human),
and every governed tier change is written to an append-only audit ledger, so who
changed a card's oversight level, when, and why is a fact on disk rather than a guess.

The wire contract is the Kanbantt MCP spec (`docs/kanbantt-mcp-spec.md`, v0.5.0).
Kanbantt gates its features on the tool names this server advertises, so the surface
below is also the capability declaration.

## Quickstart

Install and run with uv:

```
uv sync
CLAUNKER_SPINE_TOKEN=dev-secret uv run python -m spine_server.server
```

`main()` refuses to start without a token: there is no unauthenticated fallback, and
any request without a matching `Authorization: Bearer <token>` gets a 401 at the
transport. The console script `claunker-spine` is the same entry point
(`uv run claunker-spine`).

Every knob is an env var (defaults from `spine_server/config.py`):

| Env var | Default | Meaning |
|---|---|---|
| `CLAUNKER_SPINE_TOKEN` | none (required) | Bearer token. Unset means fail-closed: every request 401, and `main()` will not start. |
| `CLAUNKER_SPINE_ORIGIN` | `http://localhost:5173` | The single allowed CORS origin. Echoed verbatim into `Access-Control-Allow-Origin`, never wildcarded, and enforced by the transport's Origin check. |
| `CLAUNKER_SPINE_DB` | `spine/spine.db` | SQLite file (WAL) for the spine store. |
| `CLAUNKER_SPINE_HOST` | `127.0.0.1` | Bind host. |
| `CLAUNKER_SPINE_PORT` | `8848` | Bind port. |
| `CLAUNKER_SPINE_MAX_BYTES` | `8388608` (8 MiB) | Snapshot ceiling for `card_list`. The list never truncates: an over-ceiling snapshot fails with `payload_too_large`. |
| `CLAUNKER_SPINE_ALLOWED_HOSTS` | empty | Extra Host-header values the transport accepts (comma-separated), on top of the configured host/port and the loopback aliases. |
| `CLAUNKER_SPINE_SA_KEY` | `claunker-spine-sa-key.json` at repo root | Path to the Google service-account JSON key that activates the Drive-durable spine backup. Backup is dormant while the file is absent. Do NOT use the default: that path is not gitignored and this repo is public. Store the key outside the repo and point this variable at it. |

## The tool surface (8 tools)

`spine_server/server.py` advertises eight tools. Kanbantt derives `canWrite` from the
four `card_*` write tools as a set, and `canRetier` from `card_retier` alone.

- **`board_get`**: return the read-only board, six columns (one per Task state, derived from the `State` enum) plus the tier tags. No input.
- **`card_list`**: return a full snapshot of the spine's live Tasks projected to Cards, plus a fresh `sync_token`. Filters: `updated_since`, `column_id`, `tag`, `include_deleted`. Never truncates; an over-ceiling snapshot fails with `payload_too_large`.
- **`card_create`**: create a Task (an operator-authored card) in a project. An ungoverned operator write. Takes `project_id`, `title`, `state`, an int `tier` (1..4), and `acceptance_criteria`.
- **`card_update`**: edit a card's mutable fields via a `patch` (title, acceptance_criteria, tier). `expected_version` is required (optimistic concurrency); `force` skips only the version check. A set tier is write-once here: a `patch.tier` that differs from the current set tier is rejected with `validation_failed` and must go through `card_retier`.
- **`card_move`**: move a card to a `column_id` (a Task state) at a LexoRank `order`. `expected_version` is required; `force` skips the version check. There is no transition-legality check.
- **`card_delete`**: soft-delete a card to a recoverable tombstone (the row is retained, hidden from the board). Returns the tombstone card. `expected_version` is required, and there is no `force`: destructive ops never bypass the version check.
- **`card_retier`**: the governed, audited tier change. Move an already-set tier to a different valid tier (1..4). `reason` is required (non-empty), `expected_version` is required, and there is no `force`. On success it appends exactly one `tier_audit` row atomically with the change.
- **`escalation_resolve`**: the one human-gated control. Record an operator approve/deny decision with a rationale, writing the spine. It takes no `actor` parameter; the actor is derived from the authenticated credential (the Bearer token is the operator assertion), never from the payload.

Conflict semantics are uniform across the writes. An `expected_version` mismatch returns
a `conflict` envelope whose `meta.current` is the freshly-read current card, so the
client reconciles without an extra round trip. Tombstones are immutable: any update,
move, or delete targeting one returns `conflict` (with the tombstone in `meta.current`),
even under `force`.

## Governance notes

- **Tier lives in tags.** A card's tier is the `tier:N` tag in its `tags` array, not a native Card field. The board declares one tag per tier, and the projection emits `tier:N` for a tiered task.
- **A set tier only moves through `card_retier`.** `card_update` enforces the matching write-once guard, so once a tier is set it cannot be changed off the audited path. The free initial classification (an untiered card getting its first tier) still goes through `card_update`.
- **The `tier_audit` ledger is append-only.** Each `card_retier` appends one INSERT-only row: `card_id`, `old_tier`, `new_tier`, `reduces_control` (true iff the new tier is lower, weakening oversight), `actor`, `reason`, `ts`. The actor is a placeholder (`client:bearer`) until per-user tokens land. There is no ledger read tool in this version; the record is written now for a later history surface.

## Connecting from Kanbantt

Point Kanbantt's `mcp.url` at `http://<host>:<port>/mcp` (the `/mcp` path is the
Streamable HTTP endpoint), supply the Bearer token that matches `CLAUNKER_SPINE_TOKEN`,
and set `CLAUNKER_SPINE_ORIGIN` to the exact origin the Kanbantt site is served from (it
is echoed, not wildcarded, so a mismatch is a CORS failure). `docs/kanbantt-mcp-spec.md`
is the wire contract this server implements, kept in sync with Kanbantt's canonical copy.

## Testing

```
uv run pytest
```

Expect 115 passing tests. `pytest` is a dev dependency (`[dependency-groups] dev`), so
`uv sync` installs it and no `--with` injection is needed.
`[tool.pytest.ini_options] testpaths = ["tests"]` scopes collection to the suite, so a
bare `pytest` from the repo root collects cleanly too.

## The judge-verdict plugin

The repo also ships the Claunker judge layer: a Hermes plugin and an orchestration skill
for the architect (Claude) to executor (Ollama) to judge (Gemini) protocol.

- **`plugins/judge-verdict/`**: a `judge_verdict` tool routes a one-shot, structured adjudication through a pinned Gemini model and returns `accept` / `revise` / `escalate` with rationale. When the judge is unreachable it fails safe to `escalate`, never a silent self-accept (FT-009).
- **`skills/orchestration/claunker-orchestration/`**: the architect to executor to judge protocol skill, covering work-order decomposition, parallel fan-out, the self-accept vs. judge triage rule, and the judge-unavailable hard-halt discipline.
- **`config.example.yaml`**: a redacted copy of the Hermes runtime config. The load-bearing setting is the `plugins.entries.judge-verdict.llm` trust gate that pins the judge to Gemini (fail-closed authorization, not just routing).

See `plugins/judge-verdict/references/judge-config.md` for the exact judge pin and the
assert-attribution-not-shape verification protocol: a judge silently running on Claude
passes a verdict-shape test, so verify the resolved provider and model, never the output
shape.

## Secrets

No credentials are committed. Live secrets stay in gitignored files (`.env`, `auth.json`);
`config.example.yaml` carries no secret values, and `CLAUNKER_SPINE_TOKEN` is supplied
through the environment.
