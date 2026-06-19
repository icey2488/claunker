"""judge-verdict plugin — registers the judge_verdict tool for the Claunker loop.

Drop this directory at ~/.hermes/plugins/judge-verdict/ and enable it:
    hermes plugins enable judge-verdict

Pin the judge to a decorrelated model (Gemini) in ~/.hermes/config.yaml so the
plugin's ctx.llm.complete_structured call resolves to Gemini rather than the
architect's model. See references/judge-config.md for the exact block.
"""

from .schemas import JUDGE_VERDICT_SCHEMA
from .tools import make_handler


def register(ctx):
    ctx.register_tool(
        name="judge_verdict",
        toolset="claunker_judge",
        schema=JUDGE_VERDICT_SCHEMA,
        handler=make_handler(ctx),
        description=(
            "Adversarial third-model adjudication of executor output against a "
            "task spec. Returns accept | revise | escalate with rationale."
        ),
    )
