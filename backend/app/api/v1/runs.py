from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.config import get_settings
from app.core.db import get_session
from app.models import PipelineRun, User
from app.models.agents import AgentRun
from app.models.enums import RunStatus
from app.processing.ingest import LOCKED_RUN_KINDS, ingestion_lock_held
from app.schemas.runs import CooldownOut, RunIn, RunOut, RunQueued
from app.workers.tasks import daily_intel, ingest

router = APIRouter(prefix="/runs", tags=["runs"])


def _out(run: PipelineRun, lock_held: bool, agent_run_id: int | None) -> RunOut:
    # Only one run can hold the lock, and a new run marks leftover "running" rows failed
    # before recording itself, so "running" + lock held means this run is live.
    return RunOut(
        id=run.id,
        kind=run.kind,
        mode="full" if run.kind == "daily" else "ingest",
        trigger=run.trigger,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        stats=run.stats,
        error=run.error,
        agent_run_id=agent_run_id,
        active=run.status == RunStatus.running.value and lock_held,
    )


async def _agent_runs(session: AsyncSession, run_ids: list[int]) -> dict[int, int]:
    """pipeline run id -> its (latest) agent run id."""
    if not run_ids:
        return {}
    rows = await session.execute(
        select(AgentRun.pipeline_run_id, func.max(AgentRun.id))
        .where(AgentRun.pipeline_run_id.in_(run_ids))
        .group_by(AgentRun.pipeline_run_id)
    )
    return dict(rows.all())


@router.get("", response_model=list[RunOut])
async def list_runs(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunOut]:
    rows = await session.execute(
        select(PipelineRun)
        .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
        .limit(limit)
    )
    runs = list(rows.scalars().all())
    held = await ingestion_lock_held(session)
    agents = await _agent_runs(session, [r.id for r in runs])
    return [_out(r, held, agents.get(r.id)) for r in runs]


@router.get("/cooldown", response_model=CooldownOut)
async def get_cooldown(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CooldownOut:
    return CooldownOut(next_manual_run_at=await _next_manual_run_at(session))


@router.get("/{run_id}", response_model=RunOut)
async def get_run(
    run_id: int,
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RunOut:
    run = await session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    agents = await _agent_runs(session, [run.id])
    return _out(run, await ingestion_lock_held(session), agents.get(run.id))


async def _next_manual_run_at(session: AsyncSession) -> datetime | None:
    """When the next manual run is allowed, or None if one is allowed now.

    The cooldown starts at the last succeeded run (ingestion or full); 0 disables it.
    """
    minutes = get_settings().run_cooldown_minutes
    if minutes <= 0:
        return None
    finished = await session.scalar(
        select(func.max(PipelineRun.finished_at)).where(
            PipelineRun.kind.in_(LOCKED_RUN_KINDS),
            PipelineRun.status == RunStatus.succeeded.value,
        )
    )
    if finished is None:
        return None
    available = finished + timedelta(minutes=minutes)
    return available if available > datetime.now(UTC) else None


async def _cooldown_detail(session: AsyncSession) -> str | None:
    """429 message when a manual run is still inside the cooldown after a succeeded run."""
    available = await _next_manual_run_at(session)
    if available is None:
        return None
    settings = get_settings()
    now = datetime.now(UTC)
    finished = available - timedelta(minutes=settings.run_cooldown_minutes)
    minutes = max(0, int((now - finished).total_seconds() // 60))
    at = available.astimezone(ZoneInfo(settings.timezone)).strftime("%H:%M")
    unit = "minute" if minutes == 1 else "minutes"
    return f"A run finished {minutes} {unit} ago. The next manual run is available at {at}."


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=RunQueued)
async def start_run(
    body: RunIn,
    _: Annotated[User, Depends(require_roles("analyst"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RunQueued:
    # The advisory lock is the source of truth: a "running" row whose worker died (restart,
    # crash) must not block new runs, and a genuinely long run must not be treated as stale.
    if await ingestion_lock_held(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "A run is already in progress")
    # Politeness: space manual runs out; scheduled (beat) runs don't come through here.
    if (detail := await _cooldown_detail(session)) is not None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail)
    task = daily_intel if body.mode == "full" else ingest
    result = task.delay(trigger="manual", source_ids=body.source_ids)
    return RunQueued(task_id=str(result.id), mode=body.mode)
