"""The Celery tasks: one event loop per task run, so the engine is disposed every time."""

import asyncio
import logging
import os
from types import SimpleNamespace

import pytest
from celery.schedules import crontab
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import PipelineRun
from app.processing.ingest import RunAlreadyActive
from app.workers import tasks
from app.workers.celery_app import celery_app


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = 0

    async def dispose(self) -> None:
        self.disposed += 1


def test_task_disposes_engine_after_every_run(monkeypatch):
    engine = FakeEngine()
    calls: list[tuple] = []

    async def fake_run(factory, *, trigger, source_ids):
        calls.append((trigger, source_ids))
        return 41 + len(calls)

    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks, "run_ingestion", fake_run)
    assert tasks.ingest("manual", [3]) == 42
    assert tasks.ingest() == 43
    assert calls == [("manual", [3]), ("schedule", None)]
    assert engine.disposed == 2


def test_task_disposes_and_warns_when_a_run_is_active(monkeypatch, caplog):
    engine = FakeEngine()

    async def busy(*a, **k):
        raise RunAlreadyActive("an ingestion run is already active")

    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks, "run_ingestion", busy)
    with caplog.at_level(logging.INFO, logger="app.workers.tasks"):
        assert tasks.ingest() == -1
    assert engine.disposed == 1
    skipped = [r for r in caplog.records if "already active" in r.getMessage()]
    assert skipped and all(r.levelno == logging.WARNING for r in skipped)


@pytest.mark.usefixtures("engine")  # migrated even when run alone
def test_two_task_runs_in_one_process_share_the_engine_safely(monkeypatch):
    """Regression: the second asyncio.run must not reuse connections from the first loop.

    Uses a real engine on the (empty, committed-state) test database; source_ids=[] with no
    committed sources collects nothing. The runs it records are deleted afterwards. The
    session's `engine` fixture migrates that database first, so the test passes alone too
    (it used to rely on an earlier test having migrated it).
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    run_ids = []
    try:
        run_ids.append(tasks.ingest("manual", []))
        run_ids.append(tasks.ingest("manual", []))
        assert all(i > 0 for i in run_ids)

        async def statuses():
            try:
                async with factory() as s:
                    rows = await s.execute(
                        select(PipelineRun.id, PipelineRun.status).where(
                            PipelineRun.id.in_(run_ids)
                        )
                    )
                    return dict(rows.all())
            finally:
                await engine.dispose()

        assert asyncio.run(statuses()) == {i: "succeeded" for i in run_ids}
    finally:

        async def cleanup():
            try:
                async with factory() as s:
                    await s.execute(delete(PipelineRun).where(PipelineRun.id.in_(run_ids)))
                    await s.commit()
            finally:
                await engine.dispose()

        asyncio.run(cleanup())


# -- daily intelligence + knowledge base ----------------------------


def test_celery_beat_has_daily_intel_not_ingest():
    beat = celery_app.conf.beat_schedule
    assert "ingest-daily" not in beat  # the daily graph ingests first; one 06:00 entry
    assert beat["daily-intel"]["task"] == "app.workers.tasks.daily_intel"
    assert beat["daily-intel"]["schedule"] == crontab(hour=6, minute=0)
    assert beat["kb-weekly"]["task"] == "app.workers.tasks.build_kb"
    assert beat["kb-weekly"]["schedule"] == crontab(hour=5, minute=0, day_of_week="sun")
    assert celery_app.conf.timezone == "Asia/Jerusalem"  # crontabs are local times
    routes = celery_app.conf.task_routes
    assert routes["app.workers.tasks.daily_intel"] == {"queue": "daily"}
    assert routes["app.workers.tasks.ingest"] == {"queue": "daily"}
    assert routes["app.workers.tasks.build_kb"] == {"queue": "agents"}
    assert {"app.workers.tasks.ingest", "app.workers.tasks.daily_intel"} <= set(celery_app.tasks)


def test_daily_intel_task_disposes_engine_and_returns_the_run(monkeypatch):
    engine = FakeEngine()
    calls: list[tuple] = []

    async def fake_daily(factory, *, trigger, source_ids):
        calls.append((trigger, source_ids))
        return SimpleNamespace(pipeline_run_id=70 + len(calls))

    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks, "run_daily_intel", fake_daily)
    assert tasks.daily_intel("manual", [5]) == 71
    assert tasks.daily_intel() == 72
    assert calls == [("manual", [5]), ("schedule", None)]
    assert engine.disposed == 2


def test_daily_intel_task_skips_when_a_run_is_active(monkeypatch, caplog):
    engine = FakeEngine()

    async def busy(*a, **k):
        raise RunAlreadyActive("an ingestion run is already active")

    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks, "run_daily_intel", busy)
    with caplog.at_level(logging.INFO, logger="app.workers.tasks"):
        assert tasks.daily_intel() == -1
    assert engine.disposed == 1
    assert any("already active" in r.getMessage() for r in caplog.records)


def test_build_kb_task_skips_cleanly_without_a_key(monkeypatch, settings, caplog):
    engine = FakeEngine()
    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(
        tasks, "get_settings", lambda: settings.model_copy(update={"openai_api_key": None})
    )

    async def never(*a, **k):
        raise AssertionError("must not crawl without a key")

    monkeypatch.setattr(tasks, "build_kb_pages", never)
    with caplog.at_level(logging.INFO, logger="app.workers.tasks"):
        assert tasks.build_kb() == {"skipped": "OPENAI_API_KEY is not set"}
    assert any("OPENAI_API_KEY" in r.getMessage() for r in caplog.records)


# -- weekly competitor and product briefs (competitor intelligence) -----


def test_weekly_beat_registered_and_skips_without_key(monkeypatch, settings, caplog):
    beat = celery_app.conf.beat_schedule
    assert beat["intel-weekly"]["task"] == "app.workers.tasks.intel_briefs"
    assert beat["intel-weekly"]["schedule"] == crontab(hour=5, minute=30, day_of_week="sun")
    assert celery_app.conf.task_routes["app.workers.tasks.intel_briefs"] == {"queue": "agents"}
    assert "app.workers.tasks.intel_briefs" in celery_app.tasks

    engine = FakeEngine()
    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(
        tasks, "get_settings", lambda: settings.model_copy(update={"openai_api_key": None})
    )

    async def never(*a, **k):
        raise AssertionError("must not write briefs without a key")

    monkeypatch.setattr(tasks, "generate_briefs", never)
    with caplog.at_level(logging.INFO, logger="app.workers.tasks"):
        assert tasks.intel_briefs() == {"skipped": "OPENAI_API_KEY is not set"}
    assert any("OPENAI_API_KEY" in r.getMessage() for r in caplog.records)
    assert engine.disposed == 0  # nothing was opened


def test_intel_briefs_task_runs_every_brief_and_disposes_the_engine(monkeypatch, settings):
    from app.agents.runtime.accounted import AccountedLLM
    from app.intel.briefs import BriefsRun

    engine = FakeEngine()
    seen: dict = {}
    keyed = settings.model_copy(update={"openai_api_key": "k"})

    async def fake(session_factory, llm, run_settings, ctx, *, competitors, products):
        seen.update(llm=llm, settings=run_settings, competitors=competitors, products=products)
        seen["ctx"] = ctx
        return BriefsRun(generated=2, unchanged=5, skipped=7, removed=1)

    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks, "get_settings", lambda: keyed)
    monkeypatch.setattr(tasks, "generate_briefs", fake)
    out = tasks.intel_briefs()
    assert out == BriefsRun(generated=2, unchanged=5, skipped=7, removed=1).as_dict()
    assert out["generated"] == 2 and out["budget_stopped"] is False
    assert seen["competitors"] is None and seen["products"] is None  # every brief
    assert isinstance(seen["llm"], AccountedLLM) and seen["settings"] is keyed  # its caps
    assert seen["ctx"].agent_run_id is None
    assert engine.disposed == 1
