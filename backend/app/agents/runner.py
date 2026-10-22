"""Run (or resume) one Daily Intelligence graph run.

1. Take the ingestion advisory lock (one pipeline run at a time) and mark runs a dead
   worker left "running": pipeline runs failed, daily agent runs interrupted.
2. Record a `PipelineRun(kind="daily")` and an `AgentRun(graph="daily_intel")` with a fresh
   `thread_id`, or, with `resume=True`, reopen the latest agent run if it is interrupted and
   less than 6 h old.
3. Without an OpenAI key (and no LLM passed in) the graph only ingests: notes
   `llm_disabled`.
4. Invoke the graph on the Postgres checkpointer (or the one passed in, e.g. a MemorySaver
   in tests) under that thread; a resume continues it with `ainvoke(None, config)`.
5. Finalize both runs: tokens and cost summed from the run's `agent_steps`, the funnel (plus
   the ingest stats) into `PipelineRun.stats`, node errors and flags into `AgentRun.notes`.
   An ingest failure fails the run and is re-raised; a dying worker (a BaseException such
   as cancellation) leaves the agent run `interrupted` for a later `--resume`.
6. Prune the checkpoint threads of earlier runs older than RESUME_WINDOW: those can
   never be resumed, and LangGraph keeps every thread otherwise. Best effort.
7. Release the lock.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.graphs.daily_intel import (
    GRAPH_NAME,
    DailyDeps,
    build_daily_graph,
    initial_state,
)
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.checkpointer import open_checkpointer
from app.core.config import Settings, get_settings
from app.core.host_guard import DbHostGuard
from app.core.http import PoliteClient
from app.models import PipelineRun
from app.models.agents import AgentRun, AgentStep
from app.models.enums import AgentRunStatus, RunStatus
from app.processing.ingest import (
    INTERRUPTED_ERROR,
    acquire_run_lock,
    mark_interrupted_runs,
    release_run_lock,
)

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 200
RESUME_WINDOW = timedelta(hours=6)
PRUNE_BATCH = 50  # threads pruned per run: a daily cadence prunes yesterday's on each run
NOTES_CHARS = 2000
ERROR_CHARS = 500
LLM_DISABLED = "llm_disabled"
BUDGET_STOPPED = "budget_stopped"


@dataclass
class DailyResult:
    pipeline_run_id: int
    agent_run_id: int
    briefing_ids: list[int]
    status: str  # the agent run's final status


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NothingToResume(Exception):
    """No interrupted daily run younger than RESUME_WINDOW."""


@dataclass(frozen=True)
class _Run:
    pipeline_run_id: int
    agent_run_id: int
    thread_id: str
    started_at: datetime


async def _start(session_factory: async_sessionmaker[AsyncSession], trigger: str) -> _Run:
    async with session_factory() as session:
        await mark_interrupted_runs(session)
        run = PipelineRun(kind="daily", trigger=trigger, status=RunStatus.running.value)
        session.add(run)
        await session.flush()
        agent = AgentRun(
            graph=GRAPH_NAME,
            thread_id=f"daily-{uuid.uuid4()}",
            trigger=trigger,
            status=AgentRunStatus.running.value,
            pipeline_run_id=run.id,
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return _Run(run.id, agent.id, agent.thread_id, agent.started_at)


async def _reopen(session_factory: async_sessionmaker[AsyncSession]) -> _Run:
    """Reopen the latest daily agent run if it is interrupted and recent enough."""
    async with session_factory() as session:
        await mark_interrupted_runs(session)
        agent = await session.scalar(
            select(AgentRun)
            .where(AgentRun.graph == GRAPH_NAME)
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        now = datetime.now(UTC)
        if agent is None or agent.status != AgentRunStatus.interrupted:
            await session.commit()  # keep the stale-run marks
            raise NothingToResume("the latest daily run is not interrupted")
        if agent.started_at < now - RESUME_WINDOW:
            await session.commit()
            raise NothingToResume(
                f"the interrupted daily run {agent.id} is older than {RESUME_WINDOW}"
            )
        agent.status, agent.finished_at, agent.error = AgentRunStatus.running.value, None, None
        run = await session.get(PipelineRun, agent.pipeline_run_id)
        run.status, run.finished_at, run.error = RunStatus.running.value, None, None
        await session.commit()
        logger.info("resuming daily run %s (thread %s)", agent.id, agent.thread_id)
        return _Run(run.id, agent.id, agent.thread_id, agent.started_at)


async def prune_checkpoints(
    session_factory: async_sessionmaker[AsyncSession],
    saver: BaseCheckpointSaver,
    *,
    keep: int,
) -> int:
    """Delete the checkpoint threads of daily runs started before RESUME_WINDOW (no resume
    reaches them, `_reopen`), except run `keep`; a no-op for threads already gone. Returns
    the number of threads asked to go. Never raises: a failure only defers the pruning."""
    try:
        async with session_factory() as session:
            threads = list(
                await session.scalars(
                    select(AgentRun.thread_id)
                    .where(
                        AgentRun.graph == GRAPH_NAME,
                        AgentRun.id != keep,
                        AgentRun.status != AgentRunStatus.running.value,
                        AgentRun.started_at < datetime.now(UTC) - RESUME_WINDOW,
                    )
                    .order_by(AgentRun.started_at.desc())
                    .limit(PRUNE_BATCH)
                )
            )
        for thread_id in threads:
            await saver.adelete_thread(thread_id)
        return len(threads)
    except Exception:  # noqa: BLE001 - housekeeping only
        logger.warning("could not prune old checkpoint threads", exc_info=True)
        return 0


async def _totals(session: AsyncSession, agent_run_id: int) -> tuple[int, int, Decimal]:
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(AgentStep.tokens_in), 0),
                func.coalesce(func.sum(AgentStep.tokens_out), 0),
                func.coalesce(func.sum(AgentStep.cost_usd), 0),
            ).where(AgentStep.agent_run_id == agent_run_id)
        )
    ).one()
    return int(row[0]), int(row[1]), Decimal(row[2])


def _notes(state: dict[str, Any]) -> str | None:
    flags = []
    if not state.get("llm_enabled"):
        flags.append(LLM_DISABLED)
    if state.get("budget_stopped"):
        flags.append(BUDGET_STOPPED)
    text = "; ".join([*flags, *state.get("errors", [])])
    return text[:NOTES_CHARS] or None


async def _finalize(
    session_factory: async_sessionmaker[AsyncSession],
    run: _Run,
    *,
    status: str,
    state: dict[str, Any] | None,
    error: str | None = None,
) -> None:
    state = state or {}
    async with session_factory() as session:
        agent = await session.get(AgentRun, run.agent_run_id)
        pipeline = await session.get(PipelineRun, run.pipeline_run_id)
        now = datetime.now(UTC)
        agent.tokens_in, agent.tokens_out, agent.cost_usd = await _totals(session, agent.id)
        agent.status, agent.finished_at, agent.error = status, now, error
        if state:
            agent.notes = _notes(state)
        pipeline.status = (
            RunStatus.succeeded.value
            if status == AgentRunStatus.succeeded
            else RunStatus.failed.value
        )
        pipeline.finished_at = now
        if status == AgentRunStatus.interrupted:
            pipeline.error = INTERRUPTED_ERROR
        else:
            pipeline.error = error
        if state:
            pipeline.stats = {**state.get("ingest_stats", {}), **state.get("funnel", {})}
        await session.commit()


async def run_daily_intel(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trigger: str = "schedule",
    source_ids: list[int] | None = None,
    http_factory: Callable[[], PoliteClient] | None = None,
    llm: AccountedLLM | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    resume: bool = False,
    settings: Settings | None = None,
    now: Callable[[], datetime] | None = None,
) -> DailyResult:
    """Run the daily graph once (see the module docstring). Raises RunAlreadyActive when
    another run holds the lock and NothingToResume when `resume` finds nothing to resume.

    `llm` defaults to an AccountedLLM over OpenAI when a key is configured; build one per run
    (its lock belongs to this event loop). `settings` defaults to `get_settings()`. `now` is
    the graph's clock (aware UTC datetimes): it dates the briefing (local date), the
    briefing's 7-day eligibility window and the scoring decay; tests freeze it.
    """
    settings = settings or get_settings()
    if llm is None and settings.openai_api_key:
        from app.agents.runtime.openai_backend import OpenAIBackend

        llm = AccountedLLM(OpenAIBackend(settings), session_factory, settings)
    deps = DailyDeps(
        session_factory=session_factory,
        settings=settings,
        llm=llm,
        http_factory=http_factory
        or (lambda: PoliteClient(settings, guard=DbHostGuard(session_factory))),
        now=now or _utcnow,
    )

    conn, owned = await acquire_run_lock(session_factory)
    try:
        run = await _reopen(session_factory) if resume else await _start(session_factory, trigger)
        config: Any = {
            "configurable": {"thread_id": run.thread_id},
            "recursion_limit": RECURSION_LIMIT,
        }
        fresh = initial_state(
            pipeline_run_id=run.pipeline_run_id,
            agent_run_id=run.agent_run_id,
            run_started_at=run.started_at,
            llm_enabled=llm is not None,
            source_ids=source_ids,
        )
        try:
            if checkpointer is not None:
                state = await _invoke(deps, checkpointer, config, fresh, resume=resume)
                await prune_checkpoints(session_factory, checkpointer, keep=run.agent_run_id)
            else:
                async with open_checkpointer(settings) as saver:
                    state = await _invoke(deps, saver, config, fresh, resume=resume)
                    await prune_checkpoints(session_factory, saver, keep=run.agent_run_id)
        except Exception as exc:  # the ingest node failed (other nodes log and go on)
            logger.exception("daily run %s failed", run.agent_run_id)
            await _finalize(
                session_factory,
                run,
                status=AgentRunStatus.failed.value,
                state=None,
                error=f"{type(exc).__name__}: {exc}"[:ERROR_CHARS],
            )
            raise
        except BaseException:  # the worker is going away: leave it resumable
            logger.warning("daily run %s interrupted", run.agent_run_id)
            try:
                await _finalize(
                    session_factory, run, status=AgentRunStatus.interrupted.value, state=None
                )
            except BaseException:  # noqa: BLE001 - the next run's stale-run sweep covers it
                logger.exception("could not mark daily run %s interrupted", run.agent_run_id)
            raise
        await _finalize(session_factory, run, status=AgentRunStatus.succeeded.value, state=state)
        return DailyResult(
            pipeline_run_id=run.pipeline_run_id,
            agent_run_id=run.agent_run_id,
            briefing_ids=list(state.get("briefing_ids", [])),
            status=AgentRunStatus.succeeded.value,
        )
    finally:
        await release_run_lock(conn, owned)


async def _invoke(
    deps: DailyDeps,
    checkpointer: BaseCheckpointSaver,
    config: Any,
    fresh: dict[str, Any],
    *,
    resume: bool,
) -> dict[str, Any]:
    graph = build_daily_graph(deps, checkpointer=checkpointer)
    if resume:
        snapshot = await graph.aget_state(config)
        if snapshot.values:
            if not snapshot.next:  # it had finished; only the bookkeeping was lost
                return dict(snapshot.values)
            return await graph.ainvoke(None, config)
        logger.warning("no checkpoint for thread %s: starting it over", config["configurable"])
    return await graph.ainvoke(fresh, config)
