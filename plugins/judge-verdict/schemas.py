"""Tool schema for judge_verdict — what the architect model sees when deciding to call it."""

JUDGE_VERDICT_SCHEMA = {
    "name": "judge_verdict",
    "description": (
        "Adjudicate an executor's output against the original task using a "
        "decorrelated judge model (a different model family from the architect "
        "and executor). Call this ONLY for substantive work — code changes, "
        "multi-step deliverables, anything where a silent error is costly. Do "
        "NOT call it for trivial or low-risk output you can self-accept; the "
        "judge has latency and token cost. Returns a structured verdict: "
        "accept | revise | escalate, with rationale and (for revise) specific "
        "actionable defects."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_spec": {
                "type": "string",
                "description": (
                    "The original work order the executor was given — goal plus "
                    "any constraints. This is what the output is judged against. "
                    "Be complete: the judge has zero prior context."
                ),
            },
            "acceptance_criteria": {
                "type": "string",
                "description": (
                    "Explicit, checkable conditions for 'done'. If you cannot "
                    "state these, the work order was underspecified — fix that "
                    "before judging. One per line is fine."
                ),
            },
            "executor_output": {
                "type": "string",
                "description": (
                    "The executor's deliverable to be judged: code, text, a diff, "
                    "a summary of actions taken, or a path reference plus the "
                    "relevant content. Include enough that the judge can assess "
                    "it without running anything."
                ),
            },
            "risk_tier": {
                "type": "string",
                "enum": ["low", "standard", "high"],
                "description": (
                    "Caller's risk assessment. 'low' should usually be "
                    "self-accepted WITHOUT calling this tool. 'high' (irreversible "
                    "effects, security surface, data loss potential) raises the "
                    "judge's bar and biases toward escalate-on-doubt."
                ),
            },
        },
        "required": ["task_spec", "acceptance_criteria", "executor_output"],
    },
}

# The structured-output schema the judge model must return. Enforced via
# ctx.llm.complete_structured so the architect always gets parseable JSON.
VERDICT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["accept", "revise", "escalate"],
            "description": (
                "accept = meets criteria, ship it. revise = fixable defects, "
                "return to executor with the defect list. escalate = needs human "
                "judgment (ambiguous spec, high-risk tradeoff, judge low-confidence)."
            ),
        },
        "rationale": {
            "type": "string",
            "description": "One short paragraph. Why this verdict. Concrete, not generic.",
        },
        "defects": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "For 'revise': specific, actionable defects the executor must fix. "
                "Each one independently checkable. Empty for accept/escalate."
            ),
        },
        "confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": (
                "Judge's confidence in its own verdict. 'low' on a non-trivial "
                "task should bias the loop toward escalate regardless of verdict."
            ),
        },
    },
    "required": ["verdict", "rationale", "confidence"],
}
