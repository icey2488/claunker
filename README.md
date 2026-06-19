# claunker-hermes

The **Claunker layer**: the heterogeneous, adversarial multi-agent orchestration
and security layer composed on top of the **Hermes Agent** chassis. *Hermes is the
chassis; Claunker is the brain* — architect (Claude) → executor (Ollama) → judge
(Gemini), with a decorrelated judge that fails safe to the human.

This repo is **not** a fork of Hermes. It holds only the differentiated layer that
turns a stock Hermes runtime into Claunker:

- **`plugins/judge-verdict/`** — the decorrelated judge. A `judge_verdict` tool
  routes a one-shot, structured adjudication through a *pinned* Gemini model and
  returns `accept` / `revise` / `escalate` with rationale. When the judge is
  unreachable it fails safe to `escalate` — never a silent self-accept (FT-009).
- **`skills/orchestration/claunker-orchestration/`** — the architect → executor →
  judge protocol skill: work-order decomposition, parallel fan-out, the triage
  rule (self-accept vs. judge), and the judge-unavailable hard-halt discipline.
- **`config.example.yaml`** — a redacted copy of the Hermes runtime config. The
  Claunker-relevant settings are the docker terminal backend + the `/output`
  host-mount, and the load-bearing `plugins.entries.judge-verdict.llm` trust gate
  that pins the judge to Gemini (fail-closed authorization, not just routing).
- **`docs/`** — the Claunker Foundation corpus: `00` reconciliation, `04` Hermes
  composition, `05` security loop, the index/map, and the build roadmap.

## Relationship to the live runtime

The live Hermes runtime at `%LOCALAPPDATA%\hermes` (`~/.hermes`) is **deployed from
this repo** — the plugin, skill, and the Claunker config blocks are synced there.
Treat this repo as the source of truth; the runtime is a deployment target.

## Install (deploy the layer onto a Hermes chassis)

1. Copy `plugins/judge-verdict/` → `<hermes-home>/plugins/judge-verdict/`, then
   `hermes plugins enable judge-verdict`.
2. Copy `skills/orchestration/claunker-orchestration/` →
   `<hermes-home>/skills/orchestration/`.
3. Merge the Claunker blocks from `config.example.yaml` into
   `<hermes-home>/config.yaml`: the `terminal` docker backend + `/output` mount,
   and the `plugins.entries.judge-verdict.llm` trust gate.
4. Set `GEMINI_API_KEY` in `<hermes-home>/.env` (never commit it).

See `plugins/judge-verdict/references/judge-config.md` for the exact judge pin,
the fail-closed trust-gate config, and the *assert-attribution-not-shape*
verification protocol (a judge silently running on Claude passes a verdict-shape
test — so verify the resolved provider/model, never the output shape).

## Secrets

No credentials are committed. Live secrets stay in `<hermes-home>/.env` and
`auth.json` (both gitignored). `config.example.yaml` is the only config in the
repo and contains no secret values.
