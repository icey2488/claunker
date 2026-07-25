> **PROPAGATED COPY — NOT THE RECORD.** This is a working copy synced from its
> authoring repo per `DOC-PROPAGATION-POLICY.md` (Rule 2/3, ops repo). If this
> copy and the source disagree, the source wins.
>
> - Source repo: `kanbantt-app`
> - Source path: `docs/PROVIDER-PARITY-CONTRACT.md`
> - Source commit SHA: `22ff8b0ccfc2843d193b14ccfbd51035318f3a8a`
> - Sync date: 2026-07-24
>
> **Read this if you work on the spine:** this document states plainly that
> Kanbantt's mock harness is contracted to parity with the board's own
> `LocalProvider`, NOT to mirroring this repo's spine. Do not assume the
> harness's behavior describes or constrains the spine's — see §1 and §3.

---

# Provider-Parity Contract — the mock spine harness's actual contract

**Status:** ratified 2026-07-24. Authoring repo: `kanbantt-app` (Rule 1 of
`DOC-PROPAGATION-POLICY.md` — a doc governing the mock harness or board
authors here). Propagated, with provenance headers, to `claunker-hermes` and
`claunker-ops` under their own `docs/`.

---

## 1. Identity — what the harness is, and what it is contracted to

`src/lib/spine-mcp-test-server.js` is a **conforming MCP server**: a real
`@modelcontextprotocol/sdk` `Server`, speaking the actual `initialize` →
`tools/list` → `tools/call` wire protocol over a real (in-process)
`StreamableHTTP` transport — never a hand-rolled fetch mock. It is backed by
`src/lib/card-store.js`, **the same store the board's `LocalProvider`
uses** (file header, lines 16-19: "the harness server behaves with exact
LocalProvider parity ... The spec's Provider Parity contract made real: the
same store backs both providers under test.").

**The harness's contract is exact parity with the board's `LocalProvider`.**
It is NOT contracted to mirror the Claunker orchestration spine
(`claunker-hermes`). Its relationship to that spine is **boundary
adaptation** — the already-ratified rule that task-semantic servers adapt at
the boundary (`claunker-spine-schema-ratified.md`): the spine's Task model
and the board's Card model are different domains meeting at an MCP
projection, not one server pretending to be the other's twin.

**Plainly stated:** a divergence between the harness and the Claunker spine
is not *per se* a defect. A divergence between the harness and the
`LocalProvider` **is** — that pairing has no adaptation boundary between
them; they are contracted to be identical.

---

## 2. How expected differences are enforced — code is authoritative, this document is rationale

The authoritative, machine-checked list of tolerated harness-vs-spine
differences lives in code:

- `src/lib/parity-register.js` — `PARITY_REGISTER`, typed entries
  (`RATIFIED_EQUIVALENT` | `KNOWN_DEBT`), exact-value/format-id pinned,
  count-pinned to exactly 3 entries by `parity-probe.test.js` (test `T4b`,
  lines 120-128).
- `src/lib/parity-mask.js` — `MASK_INVENTORY`, type-assert-and-mask, count-
  pinned to exactly 9 entries by `parity-probe.test.js` (test `T4`, lines
  100-114).

**This document does not contain the authoritative list.** It explains why
each tolerated or pinned-as-expected difference is legitimate, and points at
its enforcement locus. If this document and the code disagree, **code wins**
and this document is stale. Adding a tolerated difference requires editing a
count-pinned list in code — a visible, deliberate edit, caught by `T4`/`T4b`
if the count silently changes — never an edit to this prose.

An expected difference must never mean an unchecked surface: a field MAY be
absent, but the fields that ARE present are still diffed; a version format
MAY differ, but presence is still format-asserted; a message MAY differ, but
the error `code` is still pinned equal. Where no residual pin exists on a
surface, that is said plainly below, with the gap's finding-card id.

### Enforcement table (verified 2026-07-24)

| ID | Rationale | Enforcement locus | Residual pin |
|---|---|---|---|
| **F3** — harness Card omits ~12 spine fields (`description`, `priority`, `checklist`, `attachments`, `gate_status`, `badge`, `effort`, `impact`, `due`, `depends_on`, `acceptance_criteria`) | The harness's Card projection and the spine's Card projection are independently evolving surfaces; the harness has not yet modeled every spine field. | Explicit probe assertion: `src/lib/parity-coverage.test.js:152-156` — `card_create`'s happy path is pinned `assert.throws(() => assertParity(...))`, so this stays visibly RED, never silently passes. | Pinned for `card_create` only. The same claim for `card_update`/`card_move`/`card_delete`/`card_retier`/`card_archive` is COVERAGE_TABLE prose (`parity-coverage.test.js:84-105`), not independently asserted — **GAP**, see §5. |
| **F4** — `version` bare int (mock) vs spine `"N:hexhash"` token | Independent version-token schemes; the mock predates the spine's token format. | `src/lib/parity-mask.js:65` — `MASK_INVENTORY`'s `version` entry pins the format to `VERSION_TOKEN_RE`. The mock's bare int **fails** that pinned format on every diff, surfacing as an explicit `kind:'mask-format'` violation (`applyMask`, `parity-mask.js:84-108`), not a silent pass. Also bundled into the F3 RED pin above. | Format is asserted (and currently fails loudly, correctly, rather than being masked-and-ignored). |
| **F5** — `created_by`/`updated_by` default `agent:spine` (mock, `spine-mcp-test-server.js:210`) vs spine's `human:operator` | Different default actors: the mock has no authenticated-credential concept; the spine derives the actor from the Bearer token. | Bundled into the general payload byte-diff at the same F3 RED pin (`parity-coverage.test.js:152-156`). No isolated value-pair assertion exists. | Caught only as part of the whole-body RED pin — not isolated. |
| **F6** — harness emits internal `seq` on the wire; spine never has it | `seq` is the mock store's own delta-sync/version-bump counter (`card-store.js:80-83` `CONTROLLED_FIELDS`), an implementation detail that leaked onto the projected Card. | Not in `MASK_INVENTORY` (9 entries, no `seq` — `parity-mask.js:63-73`). Caught only via the bundled RED pin (`parity-coverage.test.js:152-156`) as an unmasked payload-diff field. | Same as F5 — bundled, not isolated. |
| **F7** — `not_found` message wording differs across every stateful card tool (mock "no card X" / spine "task 'X' does not exist") | Independent error-message authors on each side; only `code`/`meta` are contracted to match. | Explicit probe assertions, 5 tools: `card_update` (`parity-coverage.test.js:181-188`), `card_move` (:231-238), `card_delete` (:253-260), `card_retier` (:285-292), `card_archive` (:325-332). | Residual pin present on all 5: `r.body.code === m.body.code === 'not_found'` is asserted equal; only the full-message parity is `assert.throws`-pinned as expected to fail. |
| **F8** — `conflict` message + `meta.current` shape differ (compounds F3-F6, since `meta.current` is a full Card) | Same root cause as F3-F6: the conflict envelope carries a Card, so its own field-shape divergence rides along. | Explicit probe assertion: `card_update` only (`parity-coverage.test.js:190-198`). | Residual pin: `r.body.code === m.body.code === 'conflict'` asserted equal. Scope caveat: not independently pinned for `card_move`/`card_delete`/`card_retier`/`card_archive` — **GAP**, see §5 (same card as F3's scope gap; same underlying `conflict()` code path, so this is a coverage-breadth gap, not a behavior gap). |
| **F13** — `sync_token` wire-format differs (board mints base64url JSON; spine mints `st_<uuid4hex>`) | Independent opaque delta-sync cursor formats, same volatility class as `version`. | **FIXED.** `parity-mask.js:72` — `MASK_INVENTORY` `sync_token` entry (`isSyncToken`, lines 49-51, accepts either named shape, never a blanket opaque-string accept). Proven at `parity-coverage.test.js:137-141` — the full `card_list` response now parities GREEN on an empty snapshot. | Format is asserted per-side (either exact named shape, nothing looser); presence is required. |
| **MANIFEST** — 9 harness-only tools (`artifact_list`, `card_get`, `column_create/update/delete`, `escalation_list`, `tag_create/update/delete`) not deployed on the real spine | These are undeployed optional capabilities, not tools the spine will never have (`parity-manifest.test.js:111-124`). | **FIXED.** `parity-manifest.test.js:133-136` — `OPTIONAL_CAPABILITY_TOOLS`, pinned to exactly these 9 names; wired via `omitTools` (`spine-mcp-test-server.js:209,243`). Proven GREEN at test `M6` (`parity-manifest.test.js:138-155`). | A genuinely real-only tool (present on the spine, absent from the harness) still reds via `onlyB` — this pin cannot, and does not, silence that class. |
| **BUDGET** — harness enforces none of the spine's 5 write-boundary caps (`description` 16384 chars; `metadata` 24 keys / 2048 chars-per-value / depth 4 / 32768 bytes — `parity-budget.js:14-23`, sourced from `claunker-hermes/spine/entity.py`) | Resource limits are server-owned; duplicating spine budget accounting on the board is brittle dual-maintenance (operator-ratified, Finding 2). | `parity-register.js:128-145` — `budget-cap-non-enforcement` entry, disposition `KNOWN_DEBT`, `finding_card_id: '870b54db-62d4-4b8e-b162-0070b19d5598'`. Whole-envelope tolerance consulted in `parity-differ.js:44-60`; count-pinned by `T4b`. | Explicitly **KNOWN_DEBT**, not silently tolerated: an over-budget payload passes locally and is rejected only by the live spine — the board is proven to survive that rejection (see §4, board pushback suite). |

---

## 3. Non-goals — strictly bounded

This section covers **topology, transport, and identity only**:

- The harness does not implement the spine's opaque `version`-token wire
  *encoding scheme* as its own storage primitive (it stores a bare int
  internally; F4 above governs the wire-format assertion).
- The harness does not require the spine's Bearer-token auth headers — it
  runs in-process with no network boundary.
- The harness does not share the spine's SQLite storage, audit ledgers
  (`tier_audit`, `archive_audit`), or lineage — it is a separate,
  independent store (`card-store.js`) that happens to also back the
  `LocalProvider`.

**This section may not be invoked for payload shape, error behavior, or
capability.** A behavioral divergence is never excused by "the harness
doesn't mirror the spine" — it must go through a count-pinned register entry
(§2) or it is a bug. An unbounded "we don't mirror the spine" clause would
be a get-out-of-jail card that lets a real regression get reframed as an
intentional non-goal; this contract exists specifically to close that door.

---

## 4. What covers what — the artifact map

| Parity axis | Owning artifact |
|---|---|
| `LocalProvider` vs harness (the ONE contract this doc governs) | The board's provider test suites (`src/lib/spine-mcp-provider.test.js`, `card-store.test.js`, and related). |
| Harness vs Claunker spine (boundary adaptation, scope-limited by §2's register) | The parity probe: `npm run probe` (`src/lib/parity-receipt-write.mjs`), plus `src/lib/parity-coverage.test.js`, `parity-manifest.test.js`, `parity-probe.test.js`. |
| Claunker spine vs its own spec | `claunker-hermes/tests/spine_server/test_spec_divergences.py` — **not** this document, **not** the probe. |
| The board's handling of spine REJECTIONS (`validation_failed`, `not_found`, `payload_too_large`, `conflict`) | The board pushback suite, merged `3d03227`. Finding: the board's error path (`MCPProviderError` → `failureTruth`/`snapBackCards` → `writeError`/`mutationNotice`) handles every spec'd rejection class correctly — the prior gap was in *assertions*, not implementation. |

---

## 5. Registered debts and open threads

- **F9** — board forwarded structurally-invalid `depends_on:null`: **FIXED**,
  merge `b716d86`, card `bda9480c`.
- **BUDGET caps** — ratified `KNOWN_DEBT` (see §2 table). Register entry +
  card `870b54db-62d4-4b8e-b162-0070b19d5598`. Consequence: an over-budget
  payload passes locally and is rejected only by the live spine; the board
  is proven to survive that rejection (§4).
- **Open** — sibling null-forwarding on `title` / `acceptance_criteria` in
  `card_update` (same class as F9, different contracts): needs its own
  investigation, not yet carded.
- **Open (this job's Step-1 finding)** — **Gap: F3-F8 scope**, card
  `9aa6ee8d-d35c-4988-ab91-bd36cb3c13ea`. The F3-F6 happy-path RED pin and
  the F8 conflict-path RED pin are each asserted for exactly one tool
  (`card_create` and `card_update` respectively, §2 table); the identical
  claims for `card_move`/`card_delete`/`card_retier`/`card_archive` exist
  only as `COVERAGE_TABLE` data, not as `assert.throws`-pinned tests. A new
  divergence on those four tools' happy-path or conflict-path shape would
  currently pass with no test exercising it at all. Closing this gap
  (mirroring the existing two pins onto the other four tools) is out of
  scope for this documentation-only job.

---

## 6. Provenance

Built after: a parity probe run that measured the harness against the
Claunker spine and surfaced ~11 divergences; a forensic read of the
harness's own origin commit establishing its real contract (`LocalProvider`
parity, not spine mirroring); a source-grounded sort separating boundary
differences from defects; and two adversarial review rounds. The category
error this document exists to prevent: the probe initially measured the
harness against the wrong target, because no document stated what the
harness was actually contracted to.
