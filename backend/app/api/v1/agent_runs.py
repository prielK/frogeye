"""Agent runs (one per daily graph run) and their step trace, for the Admin run pages.

Every role may read runs and steps. A step's `args` and its result summary
are for analysts only and come back null for everyone else: summaries quote signal titles
and facts whatever the signal's status. Everyone keeps what ran (node, agent, kind, tool or
model), how it ended (`status`, `error_kind`), its tokens, cost and timing. For the same
reason a run's node errors (pydantic errors carry `input_value` snippets of model output)
reach non-analysts as one `stage_errors` flag, and a failed run's error as its class name.
"""

import datetime as dt
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.funnel import FUNNEL_KEYS
from app.agents.runner import BUDGET_STOPPED, LLM_DISABLED
from app.api.deps import get_current_user
from app.api.visibility import is_analyst
from app.core.db import get_session
from app.models import AgentRun, AgentStep, Briefing, PipelineRun, User
from app.processing.ingest import INTERRUPTED_ERROR
from app.schemas.agent_runs import AgentRunOut, AgentStepOut

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])

FLAGS = (LLM_DISABLED, BUDGET_STOPPED)
STAGE_ERRORS = "stage_errors"  # what non-analysts see in place of the node errors
_CLASS = re.compile(r"[A-Z][A-Za-z0-9_]*")


def _error_class(text: str) -> str | None:
    head = text.split(":", 1)[0].strip()
    return head if _CLASS.fullmatch(head) else None


def _reader_notes(notes: str | None) -> str | None:
    """The flags only, plus `stage_errors` when the run noted any node error."""
    if not notes:
        return notes
    parts = [p.strip() for p in notes.split(";") if p.strip()]
    kept = [p for p in parts if p in FLAGS]
    if len(kept) < len(parts):
        kept.append(STAGE_ERRORS)
    return "; ".join(kept)


def _reader_error(error: str | None) -> str | None:
    if not error or error == INTERRUPTED_ERROR:
        return error
    return _error_class(error) or "error"


def step_status(summary: str | None) -> tuple[str, str | None]:
    """(status, error_kind) of a step, from its summary's prefix (accounted.py, node notes)."""
    text = (summary or "").lstrip()
    head = text.lower()
    if head.startswith("error"):
        rest = text.split(":", 1)[1] if ":" in text else ""
        return "error", _error_class(rest)
    if head.startswith("refused"):
        return "refused", None
    if head.startswith("budget"):
        return "budget", None
    return "ok", None


def _runs() -> Select:
    # A run files every edition of one local day (publish_briefings), so MAX is that day.
    briefing_date = (
        select(func.max(Briefing.date))
        .where(Briefing.agent_run_id == AgentRun.id)
        .scalar_subquery()
    )
    return select(AgentRun, PipelineRun.stats, briefing_date).outerjoin(
        PipelineRun, PipelineRun.id == AgentRun.pipeline_run_id
    )


def _out(
    run: AgentRun,
    stats: dict[str, Any] | None,
    briefing_date: dt.date | None,
    *,
    analyst: bool,
) -> AgentRunOut:
    # The graph writes the funnel so far into PipelineRun.stats after every stage.
    funnel = {k: stats[k] for k in FUNNEL_KEYS if k in stats} if stats else None
    return AgentRunOut(
        id=run.id,
        graph=run.graph,
        trigger=run.trigger,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        cost_usd=float(run.cost_usd),
        notes=run.notes if analyst else _reader_notes(run.notes),
        error=run.error if analyst else _reader_error(run.error),
        pipeline_run_id=run.pipeline_run_id,
        funnel=funnel or None,
        briefing_date=briefing_date,
    )


@router.get("", response_model=list[AgentRunOut])
async def list_agent_runs(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[AgentRunOut]:
    analyst = is_analyst(user)
    rows = await session.execute(
        _runs().order_by(AgentRun.started_at.desc(), AgentRun.id.desc()).limit(limit)
    )
    return [_out(*row, analyst=analyst) for row in rows]


@router.get("/{run_id}", response_model=AgentRunOut)
async def get_agent_run(
    run_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentRunOut:
    analyst = is_analyst(user)
    row = (await session.execute(_runs().where(AgentRun.id == run_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent run not found")
    return _out(*row, analyst=analyst)


@router.get("/{run_id}/steps", response_model=list[AgentStepOut])
async def list_agent_steps(
    run_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    after_step: Annotated[
        int, Query(ge=0, description="Return steps with step_no above this (live polling).")
    ] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[AgentStepOut]:
    reveal = is_analyst(user)  # read before any query: the rest see redacted fields
    if await session.get(AgentRun, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent run not found")
    steps = await session.scalars(
        select(AgentStep)
        .where(AgentStep.agent_run_id == run_id, AgentStep.step_no > after_step)
        .order_by(AgentStep.step_no, AgentStep.id)
        .limit(limit)
    )
    return [
        AgentStepOut(
            id=s.id,
            step_no=s.step_no,
            node=s.node,
            agent=s.agent,
            kind=s.kind,
            tool_name=s.tool_name,
            args=s.args if reveal else None,
            result_summary=s.result_summary if reveal else None,
            status=step_status(s.result_summary)[0],
            error_kind=step_status(s.result_summary)[1],
            model=s.model,
            tokens_in=s.tokens_in,
            cached_tokens=s.cached_tokens,
            tokens_out=s.tokens_out,
            cost_usd=float(s.cost_usd),
            latency_ms=s.latency_ms,
            created_at=s.created_at,
        )
        for s in steps
    ]
