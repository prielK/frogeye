"""Newsroom entry points: run the subgraph over the selected signals, and file the result."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.briefing_schema import (
    EDITION_ROLES,
    BriefingContent,
    content_signal_ids,
    make_briefing_models,
)
from app.agents.graphs.newsroom.graph import QUIET_HEADLINE, NewsroomEnv, Window, newsroom_graph
from app.agents.graphs.newsroom.selection import (
    LIVE_STATUSES,
    WINDOW,
    _load_views,
    quiet_competitors,
    select_signals,
)
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt
from app.agents.schemas import RunEnums
from app.agents.tools.base import ToolDeps
from app.core.config import Settings
from app.models.agents import AgentStep
from app.models.enums import BriefingEdition, BriefingStatus
from app.models.gold import Briefing


@dataclass
class NewsroomResult:
    master: BriefingContent
    editions: dict[str, BriefingContent]  # role -> edition, in EDITION_ROLES order
    signal_ids: list[int]  # the selected signals, importance desc
    status: str  # BriefingStatus
    model: str | None = None
    prompt_version: str | None = None
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


async def _newsroom_cost(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    ctx: RunCtx,
    spent_before: float,
) -> float:
    """SUM(agent_steps.cost_usd) of this run under the newsroom's node (LLM, tool and
    embed steps alike). Without an agent run (tests, ad-hoc calls) it falls back to the
    global spend delta, which also counts any concurrent spend elsewhere."""
    if ctx.agent_run_id is None:
        return max(0.0, await llm.spent_total() - spent_before)
    async with session_factory() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(AgentStep.cost_usd), 0)).where(
                AgentStep.agent_run_id == ctx.agent_run_id, AgentStep.node == ctx.node
            )
        )
    return float(total)


async def run_newsroom(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    deps: ToolDeps,
    enums: RunEnums,
    *,
    settings: Settings,
    now: datetime,
    ctx: RunCtx,
) -> NewsroomResult:
    """Select the eligible signals, run the Newsroom subgraph, return the briefing.

    A quiet day (nothing selected) makes no LLM call. `enums` is accepted for a uniform
    node signature; the newsroom's ID enums are the selected signal ids. `ctx.node` must be
    the newsroom's own node name: the cost is summed over that node's steps. Nothing
    is persisted here: hand the result to `persist_briefings`.
    """
    async with session_factory() as session:  # one short session, closed before any LLM call
        views = await _load_views(session, await select_signals(session, settings, now=now))
        quiet = await quiet_competitors(session, settings, now=now)
    window = Window(now - WINDOW, now, ZoneInfo(settings.timezone))
    meta = {"model": settings.model_smart, "prompt_version": load_prompt("editor").version}
    if not views:
        master = BriefingContent(
            headline=QUIET_HEADLINE,
            lead=f"No new signals cleared the briefing bar {window.label}.",
            top_takeaways=[],
            sections=[],
            quiet_competitors=quiet,
            watchlist=[],
        )
        return NewsroomResult(
            master=master,
            editions={r: master.model_copy(deep=True) for r in EDITION_ROLES},
            signal_ids=[],
            status=BriefingStatus.quiet,
            **meta,
        )

    env = NewsroomEnv(
        llm=llm,
        deps=deps,
        ctx=ctx,
        models=make_briefing_models(tuple(sorted(views))),
        signals=views,
        quiet_competitors=tuple(quiet),
        window=window,
    )
    spent_before = await llm.spent_total() if ctx.agent_run_id is None else 0.0
    final = await newsroom_graph().ainvoke({}, context=env)
    cost = await _newsroom_cost(session_factory, llm, ctx, spent_before)
    out = final["result"]
    return NewsroomResult(
        master=BriefingContent.model_validate(out["master"]),
        editions={r: BriefingContent.model_validate(c) for r, c in out["editions"].items()},
        signal_ids=list(views),
        status=out["status"],
        cost_usd=cost,
        errors=list(final.get("errors", [])),
        **meta,
    )


async def persist_briefings(
    session: AsyncSession,
    result: NewsroomResult,
    *,
    date: date,
    agent_run_id: int | None,
    funnel: dict[str, Any] | None,
    now: datetime | None = None,
) -> list[int]:
    """Insert the master and each edition as version max+1 of its (date, edition).

    Flush-only: the caller opens a short session (never across an LLM await),
    calls this and commits. `published_at` is set for published and quiet briefings only;
    skipped and failed ones never count as briefed, so their signals roll over. The
    newsroom's LLM cost is recorded once, on the master row.
    """
    rows: list[Briefing] = []
    published_at = (now or datetime.now(UTC)) if result.status in LIVE_STATUSES else None
    contents = [(BriefingEdition.master.value, result.master), *result.editions.items()]
    for edition, content in contents:
        current = await session.scalar(
            select(func.max(Briefing.version)).where(
                Briefing.date == date, Briefing.edition == edition
            )
        )
        row = Briefing(
            date=date,
            edition=edition,
            version=(current or 0) + 1,
            status=str(result.status),
            content=content.model_dump(mode="json"),
            signal_ids=content_signal_ids(content),
            funnel=dict(funnel or {}),
            model=result.model,
            prompt_version=result.prompt_version,
            cost_usd=Decimal(str(result.cost_usd)) if edition == "master" else Decimal(0),
            agent_run_id=agent_run_id,
            published_at=published_at,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return [r.id for r in rows]
