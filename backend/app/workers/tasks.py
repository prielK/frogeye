import asyncio
import dataclasses
import logging

from app.agents.runner import run_daily_intel
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.agents.runtime.openai_backend import OpenAIBackend
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.core.host_guard import DbHostGuard
from app.core.http import PoliteClient
from app.intel.briefs import generate_briefs
from app.processing.ingest import RunAlreadyActive, run_ingestion
from app.rag.kb import build_kb as build_kb_pages
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(make_coro):
    """asyncio.run one task body, disposing the engine before its event loop closes.

    Each task gets a fresh event loop (asyncio.run); pooled connections are bound to the loop
    that opened them, so they are dropped at the end of every task.
    """

    async def _main():
        try:
            return await make_coro()
        finally:
            await engine.dispose()

    return asyncio.run(_main())


@celery_app.task(name="app.workers.tasks.ingest")
def ingest(trigger: str = "schedule", source_ids: list[int] | None = None) -> int:
    """Ingestion only. Kept for the CLI, POST /runs mode "ingest" and back-compat."""
    try:
        return _run_async(
            lambda: run_ingestion(SessionLocal, trigger=trigger, source_ids=source_ids)
        )
    except RunAlreadyActive:
        logger.warning("ingestion skipped: a run is already active")
        return -1


@celery_app.task(name="app.workers.tasks.daily_intel")
def daily_intel(trigger: str = "schedule", source_ids: list[int] | None = None) -> int:
    """The Daily Intelligence graph (ingest -> ... -> briefing). Returns the pipeline run id,
    or -1 when another run holds the lock."""
    try:
        result = _run_async(
            lambda: run_daily_intel(SessionLocal, trigger=trigger, source_ids=source_ids)
        )
    except RunAlreadyActive:
        logger.warning("daily intelligence run skipped: a run is already active")
        return -1
    return result.pipeline_run_id


@celery_app.task(name="app.workers.tasks.build_kb")
def build_kb() -> dict:
    """Weekly JFrog knowledge-base refresh. Skips cleanly without an OpenAI key (embeddings)."""
    settings = get_settings()
    if not settings.openai_api_key:
        logger.info("kb refresh skipped: OPENAI_API_KEY is not set")
        return {"skipped": "OPENAI_API_KEY is not set"}

    async def _crawl() -> dict:
        llm = AccountedLLM(OpenAIBackend(settings), SessionLocal, settings)
        async with PoliteClient(settings, guard=DbHostGuard(SessionLocal)) as http:
            stats = await build_kb_pages(
                SessionLocal, llm, http, ctx=RunCtx(agent_run_id=None, node="kb")
            )
        return dataclasses.asdict(stats)

    return _run_async(_crawl)


@celery_app.task(name="app.workers.tasks.intel_briefs")
def intel_briefs() -> dict:
    """Weekly competitor and product briefs: every brief whose evidence changed.
    Skips cleanly without an OpenAI key. The day and total caps hold through AccountedLLM,
    and no brief starts inside the newsroom's reserve. It does not take the daily
    run's lock: a 06:00 daily run that starts while it works is never skipped."""
    settings = get_settings()
    if not settings.openai_api_key:
        logger.info("weekly briefs skipped: OPENAI_API_KEY is not set")
        return {"skipped": "OPENAI_API_KEY is not set"}

    async def _briefs() -> dict:
        llm = AccountedLLM(OpenAIBackend(settings), SessionLocal, settings)
        ctx = RunCtx(agent_run_id=None, node="intel_briefs", agent="brief_writer")
        run = await generate_briefs(
            SessionLocal, llm, settings, ctx, competitors=None, products=None
        )
        return run.as_dict()

    return _run_async(_briefs)
