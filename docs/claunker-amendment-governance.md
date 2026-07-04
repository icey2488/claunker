# Claunker — Foundational-Doc Amendment Governance

**Status:** Ratified 2026-07-04 (operator directive, on record in session). Codifies the practice established by the 2026-06-28 spine reconciliation record.
**Scope:** all corpus documents — Foundations, ratified schema/storage decisions, the MCP spec, the roadmap, design notes.

---

## 1. The stance

The foundational docs are the overarching goal the buildout moves toward — and they are design, not scripture. As code is written and features tested, reality will demand additions, and executors will sometimes find a better way. Amendment is legitimate. What is never legitimate is a silent one: **the justification is logged, every time.** The buildout mirrors the shape and function of Claunker itself — the corpus governs the system, so the same governance shape applies to the corpus.

## 2. The amendment record (the unit of process)

Every amendment produces a dated entry, in the 2026-06-28 reconciliation-record form:

- **What changed** — the specific clause/field/invariant, old → new.
- **Justification** — the evidence that forced it: the test that failed, the build finding, the better way, with refs (commit hash, test name, card id).
- **Upstream effects** — which governing docs/invariants depend on the changed thing; confirmed intact or amended in the same motion.
- **Downstream effects** — which code, clients, and wire consumers are affected; what re-syncs (spec copies, mirrors, tests).
- **Card** — every amendment record has a board card; card and record reference each other.

Superseded doc versions move to the Superseded folder; the amended doc's Status line names the amendment date and what it supersedes (existing convention, kept).

## 3. Materiality floor (deterministic, mirrors §5.6)

An amendment is **MATERIAL** if it does any of:
- (a) reduces or removes a ratified control or invariant — R1–R6, MI-*, write-once tier, fail-closed defaults, allowlists, the classifier floors;
- (b) changes a wire contract another component consumes — spec method surface, entity schema fields, error codes, capability derivation;
- (c) alters the authorization schedule, the floors, or this governance document itself (the recursive case — maximally material by definition).

**Material → surfaces to the operator BEFORE implementation**, presented as a legible diff naming which control/contract changes and how (the control_diff discipline applied to prose), with a reduces-control flag where applicable. Gemini is pulled in for adversarial review as requested or as stakes warrant. Non-material amendments — additive nullable fields on the archived_at pattern, clarifications, corrections aligning a doc with verified reality — log the record and proceed; the record is still mandatory.

The floor composes up-only, as everywhere: judgment can promote a non-material change to material; nothing demotes below the floor. Unclassifiable → treat as material (fail closed).

## 4. Sequencing rule

Upstream/downstream consideration happens **before implementation**, inside the record — not as post-hoc annotation. A spec change lands in this order: record drafted (with effects) → materiality routed (operator sign-off if material) → implementation → mirrors re-synced (repo + corpus) → card closed. Code found ahead of docs (the 6/28 case) is repaired by an amendment record after the fact, loudly — the record exists to make that rare, not to pretend it never happens.

## 5. One-line summary

Foundational docs amend the way the system changes its own floors: never silently, justification and effects on record before the change lands, material reductions routed to the human with the diff made legible, and the ledger card closing the loop.
