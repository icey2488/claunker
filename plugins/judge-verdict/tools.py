"""Handler for judge_verdict — builds the judge prompt and runs a pinned, structured judge call."""

import json

from .schemas import VERDICT_OUTPUT_SCHEMA

# --- Judge identity pin (single point of truth) ----------------------------
# The judge MUST be a different model family from the architect (Claude) and the
# executor. That decorrelation is this plugin's entire reason to exist, so the
# pin lives here as a named, visible constant — not a bare literal buried in the
# call, and never an omittable default.
#
# These are ROUTING: passed explicitly as provider=/model= on every judge call.
# Hermes' ctx.llm runs against the user's ACTIVE model when provider/model are
# omitted — which is the architect's Claude. Omitting them silently reroutes the
# judge to Claude and destroys the loop. So we never omit them.
#
# Routing alone is not enough. The companion AUTHORIZATION lives in config.yaml
# (plugins.entries.judge-verdict.llm.allow_provider_override / allow_model_override
# plus the allowed_providers/allowed_models allowlist). The allowlist is the
# load-bearing safety net: if this constant is ever changed to a non-Gemini
# model, the trust gate raises PluginLlmTrustError (a loud failure) instead of
# quietly adjudicating on the wrong model. See references/judge-config.md.
JUDGE_PROVIDER = "gemini"
JUDGE_MODEL = "gemini-2.5-flash"

# The judge's standing instructions. Deliberately adversarial: its job is to
# find reasons the work fails, not to be agreeable. Separation of duties — it
# adjudicates, it does not author fixes (that returns to the executor).
JUDGE_SYSTEM = """You are the JUDGE in an adversarial multi-model engineering loop.
A different model (the ARCHITECT) specified the task; a different model (the
EXECUTOR) produced the output. You are decorrelated from both on purpose.

Your job is to adjudicate the output against the task spec and acceptance
criteria, NOT to rewrite it and NOT to be agreeable. Assume the executor may be
confidently wrong. Check the work against the criteria literally. Reward
correctness, not effort or plausibility.

Rules:
- Judge ONLY against the provided spec and acceptance criteria. If the output
  is good but solves a different problem than specified, that is a defect.
- 'accept' means every acceptance criterion is met. If you are unsure whether
  one is met, it is NOT met.
- 'revise' means the defects are concrete and fixable by the executor. List
  them specifically — each one independently checkable. Do not write the fix.
- 'escalate' means the situation needs human judgment: the spec is ambiguous or
  internally contradictory, the right call involves a real tradeoff, or you have
  low confidence and the risk tier is not low.
- Higher risk_tier raises your bar. On 'high', escalate when in doubt.
- Be terse. Rationale is one paragraph. No praise, no hedging."""


def build_judge_input(params):
    """Assemble the judge's user-message text from the tool params.

    Hermes' complete_structured takes a system_prompt plus an instructions
    header and a list of input blocks; this is the single text block carrying
    the case to adjudicate. Content is identical to what the judge always saw.
    """
    risk = params.get("risk_tier", "standard")
    return (
        f"RISK TIER: {risk}\n\n"
        f"TASK SPEC (what the executor was asked to do):\n{params['task_spec']}\n\n"
        f"ACCEPTANCE CRITERIA (conditions for done):\n{params['acceptance_criteria']}\n\n"
        f"EXECUTOR OUTPUT (the deliverable to judge):\n{params['executor_output']}\n\n"
        "Return your verdict in the required JSON shape."
    )


def make_handler(ctx):
    """Bind the handler to the plugin context so it can borrow the host LLM."""

    def handle_judge_verdict(params, **kwargs):
        del kwargs
        # Guard: an empty acceptance_criteria means the architect skipped the
        # discipline the loop depends on. Refuse loudly rather than judge vapor.
        if not (params.get("acceptance_criteria") or "").strip():
            return json.dumps({
                "success": False,
                "error": (
                    "acceptance_criteria is empty. The judge cannot adjudicate "
                    "against an unstated bar. Specify checkable criteria, then "
                    "re-call judge_verdict."
                ),
            })

        try:
            # Route through the host's structured-completion helper, PINNED to
            # the decorrelated judge model via explicit provider=/model=. The
            # override is authorized by the trust block in config.yaml; if that
            # block is missing or the pin is ever changed off-allowlist, this
            # raises PluginLlmTrustError and we fail safe to escalate below —
            # we never silently run the wrong model.
            result = ctx.llm.complete_structured(
                system_prompt=JUDGE_SYSTEM,
                instructions=(
                    "Adjudicate the executor output against the task spec and "
                    "acceptance criteria provided below."
                ),
                input=[{"type": "text", "text": build_judge_input(params)}],
                json_schema=VERDICT_OUTPUT_SCHEMA,
                provider=JUDGE_PROVIDER,
                model=JUDGE_MODEL,
                temperature=0.0,
                purpose="claunker.judge_verdict",
            )
        except Exception as exc:  # noqa: BLE001 — any judge failure routes to human, never to self-accept
            # FT-009 fail-safe. A judge that cannot run must produce an
            # UNAMBIGUOUS escalate the architect cannot read as a bare tool
            # error and improvise around. We deliberately return success=True
            # (the TOOL did its fail-safe job) with judge_available=False, so the
            # host does NOT render this as a failed call — the load-bearing
            # signal is the escalate verdict, not an error string. This covers
            # every judge-unreachable condition: 503/5xx, timeout, auth failure,
            # connection error. RC-001 trust errors (pin changed off-allowlist)
            # also land here and escalate safely — never auto-accept.
            return json.dumps({
                "success": True,
                "judge_available": False,
                "verdict": "escalate",
                "reason": "judge_unavailable",
                "rationale": (
                    "Judge model unreachable; per the decorrelation fail-safe "
                    "this HALTS and routes to the human operator. The architect "
                    "must NOT self-verify substantive work when the judge is down."
                ),
                "detail": f"judge call failed: {exc}",
                "defects": [],
            })

        # complete_structured returns a PluginLlmStructuredResult dataclass; the
        # schema-validated verdict object is on .parsed (None when the model did
        # not return schema-valid JSON). Treat unparseable judge output as a
        # judge failure — escalate, never pass — keeping the fail-safe intact.
        verdict = result.parsed
        if not isinstance(verdict, dict):
            # Judge ran but produced no usable verdict — route exactly like an
            # outage: an unambiguous escalate (success=True, judge_available=
            # False), never a bare error the architect could improvise around.
            return json.dumps({
                "success": True,
                "judge_available": False,
                "verdict": "escalate",
                "reason": "judge_no_verdict",
                "rationale": (
                    "Judge output did not parse against the verdict schema; "
                    "escalating to the operator rather than guessing a verdict."
                ),
                "detail": "judge returned no schema-valid JSON verdict",
                "defects": [],
            })

        verdict.setdefault("defects", [])
        # Mark a real, judge-rendered verdict so the architect (and the FT-009
        # controls test) can distinguish a genuine accept/revise/escalate from
        # the judge-unavailable fail-safe above.
        verdict["judge_available"] = True
        verdict["success"] = True
        return json.dumps(verdict)

    return handle_judge_verdict
