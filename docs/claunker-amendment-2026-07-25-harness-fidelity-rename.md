# Claunker — Amendment Record 2026-07-25 (Harness-Fidelity Contract: creation + corrective rename)

**Status:** Logged 2026-07-25, log-and-proceed (NON-MATERIAL, classification below).
Covers BOTH the 2026-07-24 creation of the corpus doc `PROVIDER-PARITY-CONTRACT.md`
(which landed without an amendment record — the process defect this record also
repairs, loudly, per governance §4's after-the-fact clause) and the 2026-07-25
corrective that renames it to `HARNESS-FIDELITY-CONTRACT.md`.
**Card:** `e7da7a59-e0da-4b21-8116-45f1301ca792` (Dispatch Log, state created;
card and this record reference each other per governance §2).

---

## What changed

1. **Creation (2026-07-24, recorded here after the fact):** kanbantt-app PR #15
   (source commit `22ff8b0`, merge `995cea6`) authored
   `docs/PROVIDER-PARITY-CONTRACT.md` — the mock spine harness's governing
   contract (exact parity with the board's `LocalProvider`; spine relation is
   boundary adaptation) — and propagated it with provenance headers to
   `claunker-hermes` (`1114115`) and `claunker-ops` (`0b138f4`), linked from
   three READMEs.
2. **Corrective (2026-07-25):** the doc is renamed, in all three repos via
   `git mv`, to `HARNESS-FIDELITY-CONTRACT.md`
   (kanbantt-app source commit `b4e1c9148d79281c439c564dfeae4b76b710de9b`,
   branch `harness-fidelity-rename`, PR #16). Old title → new title:
   "Provider-Parity Contract — the mock spine harness's actual contract" →
   "Harness-Fidelity Contract — the mock spine harness's fidelity to
   card-store". Additions: §1.1 subordination to the spec's Provider Parity
   Contract; a §4 artifact-map row disclosing the UNTESTED axis
   (LocalProvider vs the spec's own parity clause). Both propagated copies
   re-synced with refreshed provenance headers carrying the NEW source path
   and the renaming commit's SHA. Doc content otherwise unchanged.

## Justification

- **Name collision:** `kanbantt-mcp-spec.md` already defines a normative
  section titled "Provider Parity Contract" — a requirement on the
  **LocalProvider** (version-token minting, tombstone retention floor,
  idempotent create, actor stamping `{"type":"human","id":"local"}`, no UI
  branching on provider identity). The new doc took that exact title for a
  different subject; two governing texts under one name is the
  drift-in-governing-docs hazard `DOC-PROPAGATION-POLICY.md` exists to close.
- **Inaccurate label:** the harness is a SERVER (a conforming
  `@modelcontextprotocol/sdk` `Server`), not a provider; the doc governs its
  fidelity to `card-store`/`LocalProvider`.
- **Missing subordination:** nothing stated the doc does not supersede the
  spec's clause; §1.1 now does, including the interaction: a harness that
  mirrors `card-store` faithfully reproduces (rather than catches) a
  LocalProvider spec-conformance defect.
- **Missing axis:** no artifact verifies the LocalProvider against the spec's
  parity clause — every existing suite compares against an implementation
  (probe: harness-vs-spine; `test_spec_divergences.py`: spine-vs-spec;
  pushback suite: board-vs-rejections; provider suites:
  LocalProvider-vs-harness). Concrete smell: probe finding F5 (harness
  defaults to an `agent:spine` actor where the spec's clause names
  `human:local`), previously classified by comparing against `card-store`,
  never against the spec. Disclosed as UNTESTED; finding card
  `83fc4f1d-1982-4ac7-baad-d3e418cdb011`. Building the coverage and
  resolving F5 is deliberately NOT done here — its own work order.

## Upstream effects

The spec's Provider Parity Contract section is untouched and its authority is
strengthened (the colliding title is retired; subordination is now explicit).
No ratified control, invariant (R1–R6, MI-*), floor, or the governance doc
itself is modified. `DOC-PROPAGATION-POLICY.md` is honored, not amended
(headers refreshed per Rules 2/3).

## Downstream effects

Three repos re-synced in the same pass: kanbantt-app (authoring; doc + README
link), claunker-hermes (copy + README link + this record), claunker-ops (copy
+ README index row). No code, wire consumer, or test changes — the
kanbantt-app suite is pinned unchanged at 489 (488 pass, 1 skip) before and
after. The old filename survives nowhere except as the "renamed from" note in
the doc's own Status line and the history this record narrates.

## Materiality classification — NON-MATERIAL (governance §3)

- **(a) reduces/removes a ratified control or invariant?** No — no control is
  reduced; the change documents existing behavior and ADDS a disclosure
  (an untested axis made visible). Checks got stricter in visibility, none
  looser.
- **(b) changes a wire contract another component consumes?** No — no method
  surface, schema field, error code, or capability derivation changes; this
  is a documentation rename plus prose additions.
- **(c) alters the authorization schedule, the floors, or the governance
  doc?** No.

None of the three prongs fires and the change is classifiable, so the
fail-closed clause is not triggered: **log-and-proceed** applies, record
mandatory (this file). Note the up-only composition: nothing here demotes a
future judgment that the F5/untested-axis WORK (as opposed to this
disclosure) is material — that classification belongs to the work order that
closes it.

## Location note

The dispatch brief defaulted this record to `claunker-ops` "unless the
governance doc specifies otherwise." The governance doc
(`claunker-amendment-governance.md`) and the sole precedent record
(`claunker-amendment-2026-07-06-v050.md`) both live in `claunker-hermes/docs`
— the corpus the governance doc's Scope names. The codified practice wins:
this record authors here.
