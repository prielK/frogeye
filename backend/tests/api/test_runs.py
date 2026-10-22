import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.models import PipelineRun
from app.models.agents import AgentRun
from app.processing.ingest import INGEST_LOCK_KEY


def _enqueue(monkeypatch, task_id: str) -> list[tuple[str, dict]]:
    """Stub both Celery tasks POST /runs may enqueue; returns [(task, kwargs)]."""
    calls: list[tuple[str, dict]] = []
    for name in ("ingest", "daily_intel"):
        monkeypatch.setattr(
            f"app.api.v1.runs.{name}.delay",
            lambda _n=name, **kw: calls.append((_n, kw)) or type("R", (), {"id": task_id})(),
        )
    return calls


@pytest.fixture
def make_run(session):
    async def _make(**fields) -> PipelineRun:
        data = {"kind": "ingestion", "trigger": "manual", "status": "succeeded", "stats": {}}
        data.update(fields)
        run = PipelineRun(**data)
        session.add(run)
        await session.flush()
        return run

    return _make


async def test_post_run_requires_analyst(auth_client):
    assert (await (await auth_client("sales")).post("/api/v1/runs", json={})).status_code == 403


async def test_post_run_enqueues(auth_client, monkeypatch):
    calls = _enqueue(monkeypatch, "t1")
    r = await (await auth_client("analyst")).post("/api/v1/runs", json={"mode": "ingest"})
    assert r.status_code == 202
    assert r.json() == {"task_id": "t1", "mode": "ingest"}
    assert calls == [("ingest", {"trigger": "manual", "source_ids": None})]


async def test_post_runs_full_enqueues_daily_intel(auth_client, monkeypatch):
    calls = _enqueue(monkeypatch, "t7")
    analyst = await auth_client("analyst")
    r = await analyst.post("/api/v1/runs", json={})  # "full" is the default
    assert r.status_code == 202
    assert r.json() == {"task_id": "t7", "mode": "full"}
    r = await analyst.post("/api/v1/runs", json={"mode": "full", "source_ids": [4]})
    assert r.status_code == 202
    assert calls == [
        ("daily_intel", {"trigger": "manual", "source_ids": None}),
        ("daily_intel", {"trigger": "manual", "source_ids": [4]}),
    ]
    assert (await analyst.post("/api/v1/runs", json={"mode": "bogus"})).status_code == 422


async def test_runs_report_mode_and_agent_run(auth_client, make_run, session):
    daily = await make_run(kind="daily")
    agent = AgentRun(
        graph="daily_intel",
        thread_id="daily-api-test",
        trigger="manual",
        status="succeeded",
        pipeline_run_id=daily.id,
    )
    session.add(agent)
    await session.flush()
    plain = await make_run(kind="ingestion")
    c = await auth_client("pm")
    listed = {x["id"]: x for x in (await c.get("/api/v1/runs")).json()}
    assert (listed[daily.id]["mode"], listed[daily.id]["agent_run_id"]) == ("full", agent.id)
    assert (listed[plain.id]["mode"], listed[plain.id]["agent_run_id"]) == ("ingest", None)
    one = (await c.get(f"/api/v1/runs/{daily.id}")).json()
    assert one["agent_run_id"] == agent.id and one["mode"] == "full"


@pytest.fixture
def lock_held_elsewhere(engine):
    """Hold the ingestion advisory lock from another Postgres session, as a live worker does."""

    @asynccontextmanager
    async def _hold():
        async with engine.connect() as other:
            await other.execute(text("select pg_advisory_lock(:k)"), {"k": INGEST_LOCK_KEY})
            try:
                yield
            finally:
                await other.execute(text("select pg_advisory_unlock(:k)"), {"k": INGEST_LOCK_KEY})
                await other.rollback()

    return _hold


async def test_post_run_conflict_only_while_lock_held(
    auth_client, make_run, monkeypatch, lock_held_elsewhere
):
    _enqueue(monkeypatch, "t3")
    await make_run(status="running")
    analyst = await auth_client("analyst")
    async with lock_held_elsewhere():
        r = await analyst.post("/api/v1/runs", json={})
        assert r.status_code == 409 and r.json() == {"detail": "A run is already in progress"}
    # same "running" row, but no worker holds the lock: it was interrupted, so a run may start
    r = await analyst.post("/api/v1/runs", json={})
    assert r.status_code == 202


async def test_post_run_conflict_for_long_live_run(
    auth_client, make_run, monkeypatch, lock_held_elsewhere
):
    # a genuinely long run (lock held) is not "stale" just because it started hours ago
    await make_run(status="running", started_at=datetime.now(UTC) - timedelta(hours=3))
    async with lock_held_elsewhere():
        r = await (await auth_client("analyst")).post("/api/v1/runs", json={})
    assert r.status_code == 409


async def test_runs_report_active_only_while_lock_held(auth_client, make_run, lock_held_elsewhere):
    running = await make_run(status="running", stats={})
    done = await make_run(status="succeeded")
    c = await auth_client("pm")

    def active(body):
        return {x["id"]: x["active"] for x in body}

    async with lock_held_elsewhere():
        listed = active((await c.get("/api/v1/runs")).json())
        assert listed[running.id] is True and listed[done.id] is False
        assert (await c.get(f"/api/v1/runs/{running.id}")).json()["active"] is True
    listed = active((await c.get("/api/v1/runs")).json())
    assert listed[running.id] is False and listed[done.id] is False
    assert (await c.get(f"/api/v1/runs/{running.id}")).json()["active"] is False


async def test_post_run_ignores_stale_running(auth_client, make_run, monkeypatch):
    await make_run(status="running", started_at=datetime.now(UTC) - timedelta(hours=3))
    _enqueue(monkeypatch, "t2")
    r = await (await auth_client("analyst")).post("/api/v1/runs", json={"source_ids": [1]})
    assert r.status_code == 202


async def test_list_runs_newest_first(auth_client, make_run):
    now = datetime.now(UTC)
    old = await make_run(started_at=now - timedelta(hours=5))
    new = await make_run(started_at=now - timedelta(hours=1), stats={"fetched": 2})
    c = await auth_client("sales")
    r = await c.get("/api/v1/runs?limit=50")
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()]
    assert ids.index(new.id) < ids.index(old.id)
    one = await c.get(f"/api/v1/runs/{new.id}")
    assert one.json()["stats"] == {"fetched": 2}
    assert (await c.get("/api/v1/runs/999999")).status_code == 404


# -- manual-run cooldown -------------------------------------------------


@pytest.fixture
def queued(monkeypatch):
    return _enqueue(monkeypatch, "t9")


async def test_post_run_cooldown_429_within_window(auth_client, make_run, queued, settings):
    assert settings.run_cooldown_minutes == 30
    finished = datetime.now(UTC) - timedelta(minutes=10)
    await make_run(status="succeeded", finished_at=finished)
    r = await (await auth_client("analyst")).post("/api/v1/runs", json={})
    assert r.status_code == 429
    detail = r.json()["detail"]
    local = (finished + timedelta(minutes=30)).astimezone(ZoneInfo(settings.timezone))
    at = local.strftime("%H:%M")
    assert re.fullmatch(
        rf"A run finished (9|10) minutes ago\. The next manual run is available at {at}\.",
        detail,
    )
    assert queued == []


async def test_post_run_allowed_after_cooldown(auth_client, make_run, queued):
    await make_run(status="succeeded", finished_at=datetime.now(UTC) - timedelta(minutes=31))
    r = await (await auth_client("analyst")).post("/api/v1/runs", json={})
    assert r.status_code == 202 and len(queued) == 1


async def test_post_run_cooldown_counts_daily_runs(auth_client, make_run, queued):
    await make_run(kind="daily", status="succeeded", finished_at=datetime.now(UTC))
    r = await (await auth_client("analyst")).post("/api/v1/runs", json={"mode": "ingest"})
    assert r.status_code == 429 and queued == []


async def test_post_run_cooldown_ignores_failed_runs(auth_client, make_run, queued):
    await make_run(status="failed", finished_at=datetime.now(UTC) - timedelta(minutes=2))
    r = await (await auth_client("analyst")).post("/api/v1/runs", json={})
    assert r.status_code == 202


async def test_post_run_cooldown_zero_disables(
    auth_client, make_run, queued, settings, monkeypatch
):
    monkeypatch.setattr(settings, "run_cooldown_minutes", 0)
    await make_run(status="succeeded", finished_at=datetime.now(UTC) - timedelta(minutes=1))
    r = await (await auth_client("analyst")).post("/api/v1/runs", json={})
    assert r.status_code == 202


async def test_post_run_lock_conflict_wins_over_cooldown(
    auth_client, make_run, queued, lock_held_elsewhere
):
    await make_run(status="succeeded", finished_at=datetime.now(UTC) - timedelta(minutes=1))
    async with lock_held_elsewhere():
        r = await (await auth_client("analyst")).post("/api/v1/runs", json={})
    assert r.status_code == 409


async def test_cooldown_endpoint(auth_client, make_run):
    c = await auth_client("sales")  # any authenticated user
    assert (await c.get("/api/v1/runs/cooldown")).json() == {"next_manual_run_at": None}
    finished = datetime.now(UTC) - timedelta(minutes=10)
    await make_run(status="succeeded", finished_at=finished)
    body = (await c.get("/api/v1/runs/cooldown")).json()
    nxt = datetime.fromisoformat(body["next_manual_run_at"])
    assert abs((nxt - (finished + timedelta(minutes=30))).total_seconds()) < 1


async def test_cooldown_endpoint_after_window_and_disabled(
    auth_client, make_run, settings, monkeypatch
):
    await make_run(status="succeeded", finished_at=datetime.now(UTC) - timedelta(minutes=31))
    c = await auth_client("analyst")
    assert (await c.get("/api/v1/runs/cooldown")).json() == {"next_manual_run_at": None}
    await make_run(status="succeeded", finished_at=datetime.now(UTC))
    monkeypatch.setattr(settings, "run_cooldown_minutes", 0)
    assert (await c.get("/api/v1/runs/cooldown")).json() == {"next_manual_run_at": None}


async def test_cooldown_endpoint_requires_auth(client):
    assert (await client.get("/api/v1/runs/cooldown")).status_code == 401
