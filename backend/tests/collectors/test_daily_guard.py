"""At most one request per job board or watched page per local day.

Every path that can fetch one is counted here, request by request, against real collectors
behind respx: scheduled and manual ingestion runs, check_sources, the admin test fetch and
job-board discovery; and the one operator escape hatch, `run_ingestion --allow-refetch-today`.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import delete, select

from app.cli import check_sources
from app.cli import run_ingestion as run_ingestion_cli
from app.cli.discover_job_boards import discover
from app.collectors.daily_guard import (
    ALREADY_FETCHED,
    claim_daily_fetches,
    day_start,
)
from app.collectors.jobs.collector import ADAPTERS
from app.models import PipelineRun, Source
from app.processing.ingest import run_ingestion

FIXTURES = Path(__file__).parent.parent / "fixtures" / "jobs"
BOARD_URL = ADAPTERS["lever"].url("acmesec")
PAGE_URL = "https://acme.test/pricing"
FEED_URL = "https://acme.test/feed.xml"
HTML = (
    "<html><body><main><h1>Plans</h1><p>The Team plan costs 49 dollars per user per month "
    "and includes unlimited repositories, single sign-on and audit logs for everyone.</p>"
    "</main></body></html>"
)
RSS = (
    '<?xml version="1.0"?><rss version="2.0"><channel><title>Acme</title>'
    "<item><title>Acme ships</title><link>https://acme.test/a</link><guid>a</guid>"
    "</item></channel></rss>"
)


@pytest.fixture
async def acme(session, competitor_factory, make_source):
    """Acme with a Lever board, a watched pricing page and an RSS feed (feeds are not
    limited to once a day: they have their own politeness)."""
    comp = competitor_factory(slug="acmesec", name="Acmesec", aliases=["Acme"], tier=1)
    session.add(comp)
    await session.flush()
    board = await make_source(
        kind="job_board",
        name="Acmesec · Lever jobs",
        url=BOARD_URL,
        competitor_id=comp.id,
        reliability="A",
        config={"ats": "lever", "token": "acmesec"},
    )
    page = await make_source(
        kind="page_watch",
        name="Acmesec · pricing page",
        url=PAGE_URL,
        competitor_id=comp.id,
        reliability="A",
        config={"page": "pricing", "url": PAGE_URL, "fetch_fulltext": False},
    )
    feed = await make_source(
        kind="rss",
        name="Acme feed",
        url=FEED_URL,
        competitor_id=comp.id,
        reliability="B",
        config={"fetch_fulltext": False},
    )
    await session.commit()
    return comp, board, page, feed


def _routes():
    """The three live endpoints, each a counted respx route (robots.txt for the page)."""
    respx.get("https://acme.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    board = respx.get(BOARD_URL).mock(
        return_value=httpx.Response(200, text=(FIXTURES / "lever.json").read_text("utf-8"))
    )
    page = respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=HTML))
    feed = respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=RSS))
    return board, page, feed


async def _stats(session_factory, run_id: int) -> dict:
    async with session_factory() as s:
        return (await s.get(PipelineRun, run_id)).stats


async def _source(session_factory, source_id: int) -> Source:
    async with session_factory() as s:
        return await s.get(Source, source_id)


@respx.mock
async def test_two_runs_in_one_day_make_one_request_per_board_and_page(
    acme, session_factory, make_polite_client
):
    """A scheduled run, then a manual run naming the sources: the board and the page are
    each asked once; the feed twice (feeds are not once-a-day). The second run records why
    it skipped them."""
    _, board, page, feed = acme
    board_route, page_route, feed_route = _routes()
    first = await run_ingestion(session_factory, http_factory=make_polite_client)
    second = await run_ingestion(
        session_factory,
        trigger="manual",
        source_ids=[board.id, page.id, feed.id],
        http_factory=make_polite_client,
    )
    assert (board_route.call_count, page_route.call_count) == (1, 1)
    assert feed_route.call_count == 2
    assert (await _stats(session_factory, first))["sources_skipped_today"] == 0
    stats = await _stats(session_factory, second)
    assert stats["sources_skipped_today"] == 2 and stats["sources_attempted"] == 1
    # the first run's request is the day's attempt, and the day's snapshot is stored
    stored = await _source(session_factory, board.id)
    assert stored.last_attempt_at is not None and stored.last_complete_fetch_at is not None


@respx.mock
async def test_a_failed_attempt_spends_the_day_too(acme, session_factory, make_polite_client):
    """`last_fetched_at` is set only on success: the attempt stamp is what stops a failing
    page from being retried by every manual run."""
    _, _, page, _ = acme
    respx.get("https://acme.test/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get(PAGE_URL).mock(return_value=httpx.Response(500, text="oops"))
    for _ in range(3):
        await run_ingestion(session_factory, source_ids=[page.id], http_factory=make_polite_client)
    calls = route.call_count  # PoliteClient's own retries of one attempt, never a second run's
    stored = await _source(session_factory, page.id)
    assert stored.last_fetched_at is None and stored.consecutive_failures == 1
    await run_ingestion(session_factory, source_ids=[page.id], http_factory=make_polite_client)
    assert route.call_count == calls


@respx.mock
async def test_the_next_local_day_fetches_again(
    acme, session_factory, make_polite_client, a_day_passes
):
    _, board, page, _ = acme
    board_route, page_route, _ = _routes()
    ids = [board.id, page.id]
    await run_ingestion(session_factory, source_ids=ids, http_factory=make_polite_client)
    await a_day_passes(*ids)
    await run_ingestion(session_factory, source_ids=ids, http_factory=make_polite_client)
    assert (board_route.call_count, page_route.call_count) == (2, 2)


@respx.mock
async def test_the_operator_flag_allows_exactly_one_more(
    acme, session_factory, make_polite_client, caplog
):
    """`--allow-refetch-today` (the same-day acceptance snapshot): one more request per
    board and page, with a warning; a normal run after it still skips them."""
    _, board, page, _ = acme
    board_route, page_route, _ = _routes()
    ids = [board.id, page.id]
    await run_ingestion(session_factory, source_ids=ids, http_factory=make_polite_client)
    await run_ingestion(session_factory, source_ids=ids, http_factory=make_polite_client)
    assert (board_route.call_count, page_route.call_count) == (1, 1)
    with caplog.at_level(logging.WARNING, logger="app.processing.ingest"):
        flagged = await run_ingestion(
            session_factory,
            trigger="cli",
            source_ids=ids,
            http_factory=make_polite_client,
            allow_refetch_today=True,
        )
    assert (board_route.call_count, page_route.call_count) == (2, 2)
    assert "allow-refetch-today" in caplog.text
    assert (await _stats(session_factory, flagged))["sources_skipped_today"] == 0
    await run_ingestion(session_factory, source_ids=ids, http_factory=make_polite_client)
    assert (board_route.call_count, page_route.call_count) == (2, 2)


def test_the_flag_is_documented_and_only_the_cli_passes_it(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        run_ingestion_cli.main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--allow-refetch-today" in help_text
    assert "never" in help_text and "scheduler" in help_text
    seen: list[bool] = []

    async def fake_run(allow_refetch_today: bool) -> dict:
        seen.append(allow_refetch_today)
        return {}

    monkeypatch.setattr(run_ingestion_cli, "_run", fake_run)
    run_ingestion_cli.main([])
    run_ingestion_cli.main(["--allow-refetch-today"])
    assert seen == [False, True]
    # the scheduled tasks never pass it
    tasks = (Path(__file__).parents[2] / "app" / "workers" / "tasks.py").read_text("utf-8")
    assert "allow_refetch_today" not in tasks


async def test_a_claim_is_once_per_local_day(acme, session_factory):
    """A claim is one UPDATE that matches only sources not attempted since the local day
    began, so a second claim that day gets nothing. The day is Jerusalem's: an attempt at
    20:30 UTC on the 19th is 23:30 there, and 21:00 UTC is already the 20th."""
    _, board, page, _ = acme
    at = datetime(2026, 10, 19, 20, 30, tzinfo=UTC)  # 23:30 in Jerusalem (UTC+3)
    assert day_start(at) == datetime(2026, 10, 18, 21, 0, tzinfo=UTC)

    async def claim(now: datetime, **kw) -> set[int]:
        async with session_factory() as s:
            got = await claim_daily_fetches(s, [board.id, page.id], now=now, **kw)
            await s.commit()
            return got

    assert await claim(at) == {board.id, page.id}
    assert await claim(at) == set()
    assert await claim(at + timedelta(minutes=29)) == set()  # 23:59 on the 19th
    assert await claim(at + timedelta(minutes=30)) == {board.id, page.id}  # 00:00 on the 20th
    assert await claim(at + timedelta(hours=1), allow_refetch_today=True) == {board.id, page.id}


@respx.mock
async def test_check_sources_claims_and_skips_like_a_run(
    acme, session_factory, make_polite_client, monkeypatch
):
    """check_sources reads the seed, but a board or page counts against the database row it
    is: one already fetched today is skipped, one it fetches is today's attempt."""
    comp, board, page, feed = acme
    board_route, page_route, _ = _routes()
    specs = [
        Source(
            kind=s.kind,
            name=s.name,
            url=s.url,
            config=dict(s.config),
            competitor=comp,
            enabled=True,
        )
        for s in (board, page)
    ]
    rows, to_check = await check_sources.daily_gate(specs, session_factory, now=datetime.now(UTC))
    assert rows == [] and len(to_check) == 2
    async with make_polite_client() as client:
        for spec in to_check:
            await check_sources._check(spec, client, asyncio.Semaphore(1))
    assert (board_route.call_count, page_route.call_count) == (1, 1)
    # the day's run skips both; a second check skips both too
    run_id = await run_ingestion(
        session_factory, source_ids=[board.id, page.id], http_factory=make_polite_client
    )
    assert (await _stats(session_factory, run_id))["sources_skipped_today"] == 2
    rows, to_check = await check_sources.daily_gate(specs, session_factory, now=datetime.now(UTC))
    assert to_check == [] and [r[2] for r in rows] == ["skipped", "skipped"]
    assert all(r[4] == ALREADY_FETCHED for r in rows)
    assert (board_route.call_count, page_route.call_count) == (1, 1)


async def test_check_sources_skips_boards_and_pages_it_cannot_account_for(acme, session_factory):
    """Without the database it cannot tell whether a board or page was read today, and a
    seed entry not yet loaded has no row to stamp: both are skipped, never fetched."""
    comp, board, _, _ = acme
    unseeded = Source(kind="page_watch", name="New page", url="https://new.test/pricing", config={})
    rows, to_check = await check_sources.daily_gate(
        [unseeded], session_factory, now=datetime.now(UTC)
    )
    assert to_check == [] and rows[0][2] == "skipped" and "not in the database" in rows[0][4]

    class Broken:
        def __call__(self):
            raise OSError("db down")

    spec = Source(kind=board.kind, name=board.name, url=board.url, config={}, competitor=comp)
    feed = Source(kind="rss", name="Feed", url="https://feed.test/rss", config={})
    rows, to_check = await check_sources.daily_gate([spec, feed], Broken(), now=datetime.now(UTC))
    assert to_check == [feed] and rows[0][2] == "skipped" and "database" in rows[0][4]


@respx.mock
async def test_the_admin_test_fetch_spends_the_day_and_then_refuses(
    acme, auth_client, seeded, session_factory, make_polite_client
):
    from app.api.v1 import sources as sources_api
    from app.main import app

    _, board, page, _ = acme
    board_route, page_route, _ = _routes()

    async def http_override():
        async with make_polite_client() as c:
            yield c

    app.dependency_overrides[sources_api.get_http] = http_override
    try:
        analyst = await auth_client("analyst")
        first = await analyst.post(f"/api/v1/sources/{page.id}/test")
        assert first.status_code == 200 and first.json()["ok"] is True
        again = await analyst.post(f"/api/v1/sources/{page.id}/test")
        assert again.status_code == 409 and again.json()["detail"] == ALREADY_FETCHED
        # the day's run skips the page the test fetch read, and reads the board
        await run_ingestion(
            session_factory, source_ids=[board.id, page.id], http_factory=make_polite_client
        )
        refused = await analyst.post(f"/api/v1/sources/{board.id}/test")
        assert refused.status_code == 409 and refused.json()["detail"] == ALREADY_FETCHED
    finally:
        app.dependency_overrides.pop(sources_api.get_http, None)
    assert (board_route.call_count, page_route.call_count) == (1, 1)


@respx.mock
async def test_discovery_spends_the_new_boards_day(acme, session_factory, make_polite_client):
    """Discovery's probe is the board's first snapshot (no second request that day): the
    same day's run skips the board it found."""
    _, board, _, _ = acme
    async with session_factory() as s:  # Acmesec has no board yet: discovery looks for one
        await s.execute(delete(Source).where(Source.id == board.id))
        await s.commit()
    found = respx.get(BOARD_URL).mock(
        return_value=httpx.Response(200, text=(FIXTURES / "lever.json").read_text("utf-8"))
    )
    respx.get(ADAPTERS["greenhouse"].url("acmesec")).mock(return_value=httpx.Response(404))
    async with make_polite_client() as http:
        out = await discover(session_factory, http, now=datetime.now(UTC), slug="acmesec")
    assert [f["ats"] for f in out.found] == ["lever"]
    async with session_factory() as s:
        new = await s.scalar(select(Source).where(Source.url == BOARD_URL))
    assert new.last_attempt_at is not None
    await run_ingestion(session_factory, source_ids=[new.id], http_factory=make_polite_client)
    assert found.call_count == 1


@respx.mock
async def test_an_explicit_run_skips_a_discovery_placeholder(
    acme, session_factory, make_source, make_polite_client
):
    """A cached miss (`config.missing`) names no board; a run that lists it sends
    nothing for it and leaves its health alone."""
    comp, *_ = acme
    miss = await make_source(
        kind="job_board",
        name="Acmesec · job board (none found)",
        url="discovery://job_board/acmesec",
        competitor_id=comp.id,
        enabled=False,
        config={"missing": True, "checked": {"lever:acmesec": "2026-10-01T04:00:00+00:00"}},
    )
    run_id = await run_ingestion(
        session_factory, source_ids=[miss.id], http_factory=make_polite_client
    )
    stats = await _stats(session_factory, run_id)
    assert stats["sources_attempted"] == 0 and stats["sources_failed"] == 0
    stored = await _source(session_factory, miss.id)
    assert stored.health == "ok" and stored.consecutive_failures == 0
    assert stored.last_attempt_at is None and stored.config["missing"] is True
