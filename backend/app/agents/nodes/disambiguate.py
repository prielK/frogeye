"""Disambiguation agent: a smart model with read-only tools re-judges an unsure triage."""

import logging
from typing import Any

from sqlalchemy import select

from app.agents.nodes.triage import TriageDecision, load_doc_view
from app.agents.runtime.accounted import AccountedLLM, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt, untrusted
from app.agents.runtime.tool_agent import run_tool_agent
from app.agents.schemas import RunEnums, make_models
from app.agents.tools import ToolDeps, get_tools
from app.models import Competitor
from app.models.enums import StepKind

logger = logging.getLogger(__name__)

TASK = "disambiguate"
MAX_TOOL_CALLS = 3


async def disambiguate_one(
    decision: TriageDecision,
    *,
    llm: AccountedLLM,
    deps: ToolDeps,
    enums: RunEnums,
    ctx: RunCtx,
) -> Any:
    """Return the corrected TriageOut (an instance of the run's dynamic model).

    On LLMRefusal/LLMError the first triage result is returned unchanged and a node step is
    logged. BudgetExceeded propagates.
    """
    view = await load_doc_view(deps.session_factory, decision.doc_id)
    if view is None:
        return decision.out
    terms = {t.lower() for t in view["terms"]}
    picked = set(decision.out.competitor_slugs)
    async with deps.session_factory() as session:
        # JFrog itself is never a candidate (its slug is not in the run's enums either)
        comps = (
            (
                await session.execute(
                    select(Competitor).where(~Competitor.is_self).order_by(Competitor.id)
                )
            )
            .scalars()
            .all()
        )
        candidates = [
            {"slug": c.slug, "name": c.name, "hint": c.disambiguation_hint}
            for c in comps
            if c.slug in picked or any(a.lower() in terms for a in (c.ambiguous_aliases or []))
        ]
    try:
        return await run_tool_agent(
            llm,
            task=TASK,
            tier="smart",
            prompt=load_prompt("disambiguate"),
            variables={
                **view,
                "terms": ", ".join(view["terms"]) or "-",
                # The first decision's reason is a model's free text about scraped text.
                "first": untrusted("first", decision.out.model_dump_json()),
                "candidates": candidates,
            },
            tools=get_tools(["lookup_competitor", "search_intel"]),
            output_schema=make_models(enums).TriageOut,
            max_tool_calls=MAX_TOOL_CALLS,
            deps=deps,
            ctx=ctx,
        )
    except (LLMRefusal, LLMError) as exc:
        logger.warning("disambiguation failed for document %s: %s", decision.doc_id, exc)
        await llm.log_step(
            ctx,
            kind=StepKind.node,
            args={"doc_id": decision.doc_id},
            result_summary=f"error: {exc}",
        )
        return decision.out
