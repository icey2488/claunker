# Claunker — Dispatch-Lane Ledger Honesty (design note)

**Status:** Design note, 2026-07-03. Sequence decided; items 1–2 not yet built; item 3 blocked on 1+2.
**Context docs:** spine schema (ratified, amended 2026-06-28), storage ratification, kanbantt-mcp-spec v0.4.0, FT-008 grant inventory.

---

## 1. The lane this governs

The claude-async lane: Claude chat/Desktop → claude-async MCP bridge → Claude Code CLI on the local machine. It is a real execution lane (commits, pushes, file mutations), subscription-priced rather than per-token, with a correlated Claude proposer/executor pair behind a decorrelated Gemini review seat — the same judge-seat decorrelation shape as the Hermes lane, without the classifier or allowlist gates.

Until 2026-07-03 this lane ran off-ledger: work executed and shipped with no spine record. The first card (backfilled: `086a67c9`, "Doc mirror fix", Dispatch Log project, delivered) proved the jobcard write path and surfaced two structural gaps.

## 2. The two gaps

**Gap 1 — Task carries no actor.** The MCP spec's Card wire shape defines `created_by`/`updated_by`, and the tier- and archive-audit ledgers stamp actors, but the spine Task entity stores none. The projection therefore has nothing honest to emit (absent fields surface `null`, never fabricated — the ratified divergence rule), and the ledger cannot distinguish a Hermes dispatch from a claude-async job from a hand-made card. The board renders WHAT happened with no record of WHO.

**Gap 2 — no receipts.** Task has no description/body field, so job substance (commit hashes, push tips) has nowhere to live and dies with the chat transcript. The schema-native answer is not a description field; it is Artifact rows: git commit hashes are R6-approved durable refs, so a completed job should carry `kind: delivery` (and where apt `kind: diff`) Artifacts pointing at its commits. Receipts on the ledger, not prose.

## 3. The three items, sequenced

1. **`Task.created_by` — nullable actor field.** `{ "type": "human" | "agent", "id": string } | null`, null default. Follows the `archived_at` precedent exactly: nullable-with-null-default, NO schema_version bump, existing blobs load untouched. Projection passes it through to the Card's `created_by`; absent stays `null`, never fabricated. jobcard gains an actor argument (agent-typed, e.g. `claude-code`); the MCP write path continues to derive actor from authenticated context per spec, never the payload.
2. **jobcard `artifact` subcommand.** Attach delivery/diff Artifacts to a card with git-hash (or other R6-durable) refs. MI-1 applies as everywhere: no artifacts on a tombstoned parent.
3. **claude-async start/finish hook.** The bridge mints a card at dispatch (`dispatched` state, actor stamped) and closes it at completion (`delivered` + delivery Artifact carrying the session's commit refs; failure → `failed` with an error-kind receipt). Cards become a byproduct on this lane, matching the Hermes ingest path's property.

**Sequencing is load-bearing: 3 is blocked on 1+2.** Automating the minting of cards that cannot say who minted them or what they delivered would scale the all-green-with-the-substance-missing shape, not close it. Actor and receipts land first; only then does minting become automatic.

## 4. FT-008 rider

claude-async (an MCP server allowing any connected Claude session to run arbitrary Claude Code locally) and the Desktop Claude Code grant both enter the grant inventory as standing grants on this lane at the punch-list #2 refresh. The auto-card hook, once live, is itself the lane's provenance record — FT-005's log-the-attribution principle applied to executor dispatch.

---

## One-line summary

The claude-async lane gets ledger parity with the Hermes lane in three steps: actor on the Task (nullable, no schema bump), receipts as R6-durable Artifacts via jobcard, then auto-carding in the bridge — in that order, because automated cards without actor and receipts would be green without substance.
