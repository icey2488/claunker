# judge_verdict — config & verification

This plugin's whole point is that the judge is a **different model family** from
the architect (Claude) and the executor. Decorrelation is the value; everything
below exists to make it *enforced*, not merely intended.

> **Correction (verified against this Hermes build, 2026-06-13).** An earlier
> draft of this doc claimed the plugin "never names a provider" and that you
> "pin the judge identity in config" as a one-line change. **That is false for
> this build and is the exact trap that makes the loop silent theater.**
> `ctx.llm.complete_structured` runs against the user's *active* model when
> `provider`/`model` are omitted — i.e. the architect's Claude. There is no
> config key that injects a default provider/model into an omitted call. The
> real mechanism has two required halves:
>
> 1. **Routing — a call argument.** `tools.py` passes `provider=JUDGE_PROVIDER,
>    model=JUDGE_MODEL` (`"gemini"` / `"gemini-2.5-flash"`) explicitly on every
>    judge call. Omit these and the judge runs on Claude.
> 2. **Authorization — a config trust gate + allowlist.** Per-plugin LLM
>    overrides are fail-closed. `config.yaml` must grant the override AND
>    allowlist it to exactly the Gemini pin (below). Without the grant the call
>    raises `PluginLlmTrustError`; the allowlist is what turns a wrong-model pin
>    into that same loud error instead of a quiet wrong run.
>
> See `/developer-guide/plugin-llm-access` and `agent/plugin_llm.py` in this
> build for the authoritative API.

## 1. Install + enable

Copy this directory to `<hermes-home>/plugins/judge-verdict/` (this build:
`%LOCALAPPDATA%\hermes\plugins\judge-verdict\`), then:

```bash
hermes plugins enable judge-verdict
```

Confirm it loaded with `hermes plugins list` (status `enabled`) and that the
`judge_verdict` tool appears in `hermes tools list`.

## 2. Pin the judge to Gemini (routing + authorization — BOTH required)

### Half 1 — routing (already done in code)
`tools.py` pins the judge as named constants and passes them on every call:

```python
JUDGE_PROVIDER = "gemini"
JUDGE_MODEL    = "gemini-2.5-flash"
# ...
ctx.llm.complete_structured(..., provider=JUDGE_PROVIDER, model=JUDGE_MODEL)
```

The constants are hygiene (a single visible point of truth). They enforce
nothing on their own — a refactor could route around a constant. The allowlist
in Half 2 is the actual guard.

### Half 2 — authorization + allowlist (config.yaml)
The trust gate is fail-closed: an unconfigured plugin cannot choose its provider
or model at all. Grant the override and **lock it to exactly the Gemini pin**:

```yaml
# <hermes-home>/config.yaml
plugins:
  entries:
    judge-verdict:
      llm:
        allow_provider_override: true
        allow_model_override: true
        allowed_providers:
          - gemini            # load-bearing: NOT a wider list, NOT ["*"]
        allowed_models:
          - gemini-2.5-flash  # load-bearing: the only model the gate permits
```

```ini
# <hermes-home>/.env  — the `gemini` (Google AI Studio) provider reads this
GEMINI_API_KEY=<your gemini key>
```

**Do not widen the allowlist.** `allowed_providers: [gemini]` /
`allowed_models: [gemini-2.5-flash]` is what converts "judge accidentally points
at Claude" into a `PluginLlmTrustError`. The gate refusing is the safety
property; a well-behaved model is not.

## 3. Risk-tier behavior (design intent)

- `low` — caller should usually self-accept WITHOUT calling judge_verdict. If
  they call it anyway, the judge still runs but the bar is normal.
- `standard` — default. Judge against criteria literally.
- `high` — irreversible effects / security surface / data-loss potential. The
  judge raises its bar and escalates on doubt. The orchestration skill decides
  the tier; the judge just honors it.

## 4. Verification — assert attribution, not verdict shape

A well-formed `{verdict, rationale, confidence, defects}` comes back whether the
call hit Gemini OR fell through to Claude. So a test that checks "did I get a
valid verdict" passes through the exact regression it must catch. Assert the
**resolved provider/model**, not the output shape:

1. **Decorrelation holds (positive).** Run a judge call and read the resolved
   attribution — `PluginLlmStructuredResult.provider` / `.model`, and the
   `plugin_llm.complete_structured plugin=judge-verdict provider=… model=…`
   line in `agent.log`. PASS only if provider is `gemini` and the model is the
   pinned Gemini model. FAIL explicitly on anything in the Anthropic/Claude
   family.
2. **Gate is closed (negative).** Force a Claude pin (call with
   `provider="anthropic"`, or model off the allowlist). It MUST raise
   `PluginLlmTrustError` — proving the gate refuses, not that the happy path
   happens to work today.
3. **Empty-criteria refusal.** Call with blank `acceptance_criteria`; it must
   return `success: false` with the specify-criteria error and make **no** LLM
   call.
4. **Fail-safe on judge outage.** Break the Gemini key; a judge call must return
   `verdict: escalate` (never a silent accept).
5. **Structured output.** A normal call returns parseable JSON with
   `verdict ∈ {accept, revise, escalate}`, `rationale`, `confidence`, and
   `defects` (array, populated only on revise).

## 5. Pitfalls

- **Judge running on the architect's model** is the silent failure that makes
  the whole loop theater. It happens by *omitting* `provider`/`model` — which is
  why they are mandatory call args, not config defaults. Verification 4.1 exists
  to catch it; assert provider, never eyeball the verdict.
- **Widening the allowlist.** `allowed_providers: ["*"]` or adding `anthropic`
  re-opens the exact hole the gate closes. Keep it pinned to `gemini` /
  `gemini-2.5-flash`.
- **`ctx.llm` signature drift.** This build's `complete_structured` is
  keyword-only: `instructions=`, `input=[...]`, `json_schema=`, `system_prompt=`,
  `provider=`, `model=`; it returns a dataclass whose parsed verdict is on
  `.parsed`. The call site in `tools.py` is the one place to adjust if the API
  shifts across versions.
- **Judging without criteria.** The handler refuses empty criteria on purpose.
  Do not "fix" this by having it infer criteria; that hands bar-setting to the
  thing being judged.
