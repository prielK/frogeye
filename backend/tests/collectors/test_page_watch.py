"""Page watch: the collector, snapshots on change, the diff cap, failures keep state."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from app.collectors import get_collector
from app.collectors.base import CollectorError
from app.collectors.page_watch import PageWatchCollector
from app.intel.pages import DIFF_CAP_CHARS, store_page_snapshot
from app.models import PageSnapshot, PipelineRun, Source
from app.processing.ingest import run_ingestion

PAGE_URL = "https://acme.test/pricing"
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)

HTML = """<html><head><title>Pricing</title></head><body><nav>Menu Home</nav><main>
<h1>Plans and pricing</h1>
<p>The Team plan costs 49 dollars per user per month and includes unlimited repositories,
single sign-on, audit logs and email support for every member of the organisation.</p>
<p>The Enterprise plan is priced on request and adds dedicated support and an uptime SLA.</p>
</main><footer>Copyright Acme</footer></body></html>"""


@pytest.fixture
async def watched(session, competitor_factory, make_source):
    comp = competitor_factory(slug="acme", name="Acme", tier=1)
    session.add(comp)
    await session.flush()
    src = await make_source(
        kind="page_watch",
        name="Acme · pricing page",
        url=PAGE_URL,
        competitor_id=comp.id,
        reliability="A",
        config={"page": "pricing", "url": PAGE_URL},
    )
    await session.commit()
    return comp, src


def _allow_robots():
    respx.get("https://acme.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )


async def _snaps(session_factory, source_id):
    async with session_factory() as s:
        return list(
            (
                await s.scalars(
                    select(PageSnapshot)
                    .where(PageSnapshot.source_id == source_id)
                    .order_by(PageSnapshot.id)
                )
            ).all()
        )


def test_registered():
    assert isinstance(get_collector("page_watch"), PageWatchCollector)


@respx.mock
async def test_collector_returns_main_text_and_respects_robots(watched, polite_client):
    _, src = watched
    _allow_robots()
    page = respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=HTML))
    result = await PageWatchCollector().fetch(src, None, polite_client)
    assert result.items == [] and result.page_text is not None
    assert "Team plan costs 49 dollars" in result.page_text
    assert page.call_count == 1
    urls = [str(c.request.url) for c in respx.calls]
    assert urls[0] == "https://acme.test/robots.txt"  # robots.txt is checked first
    ua = respx.calls.last.request.headers["user-agent"]
    assert "@" not in ua and "mailto" not in ua  # generic UA, no contact info


@respx.mock
async def test_collector_rejects_error_status_empty_page_and_self(
    watched, polite_client, session, competitor_factory
):
    _, src = watched
    _allow_robots()
    respx.get(PAGE_URL).mock(return_value=httpx.Response(404, text="gone"))
    with pytest.raises(CollectorError, match="HTTP 404"):
        await PageWatchCollector().fetch(src, None, polite_client)
    respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text="<html><body></body></html>"))
    with pytest.raises(CollectorError, match="no text"):
        await PageWatchCollector().fetch(src, None, polite_client)
    # JFrog itself is never watched
    me = competitor_factory(slug="me", name="Me", tier=0, relationship="self", is_self=True)
    session.add(me)
    await session.flush()
    src.competitor_id = me.id
    await session.flush()
    await session.refresh(src, attribute_names=["competitor"])
    with pytest.raises(CollectorError, match="self"):
        await PageWatchCollector().fetch(src, None, polite_client)


async def test_snapshot_only_on_change(session, session_factory, watched):
    _, src = watched
    first = await store_page_snapshot(session, src, "Plans\nTeam 49 dollars", now=NOW)
    assert first is not None and first.diff_from_prev is None  # the baseline
    assert first.page_kind == "pricing" and first.fetched_at == NOW
    assert first.judged_at is None and first.meaningful is None
    same = await store_page_snapshot(session, src, "Plans\nTeam 49 dollars", now=NOW)
    assert same is None
    changed = await store_page_snapshot(
        session, src, "Plans\nTeam 59 dollars", now=NOW + timedelta(days=1)
    )
    assert changed is not None and changed.id != first.id
    assert "-Team 49 dollars" in changed.diff_from_prev
    assert "+Team 59 dollars" in changed.diff_from_prev
    await session.commit()
    assert len(await _snaps(session_factory, src.id)) == 2
    # an empty fetch never wipes the last snapshot
    assert await store_page_snapshot(session, src, "   ", now=NOW) is None


async def test_diff_capped(session, watched):
    _, src = watched
    old = "\n".join(f"old line {i} with some text" for i in range(2000))
    new = "\n".join(f"new line {i} with some text" for i in range(2000))
    await store_page_snapshot(session, src, old, now=NOW)
    snap = await store_page_snapshot(session, src, new, now=NOW + timedelta(days=1))
    assert snap is not None and DIFF_CAP_CHARS == 20_000
    assert len(snap.diff_from_prev) <= DIFF_CAP_CHARS
    assert snap.text == new  # the full text is kept, only the diff is capped


@pytest.mark.parametrize("failure", ["robots", "http_500", "timeout"])
@respx.mock
async def test_robots_disallowed_records_health_keeps_last_snapshot(
    failure, session, session_factory, watched, make_polite_client
):
    """A blocked or failing page records source health, keeps the snapshot."""
    _, src = watched
    kept = await store_page_snapshot(session, src, "Plans\nTeam 49 dollars", now=NOW)
    await session.commit()
    if failure == "robots":
        respx.get("https://acme.test/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /pricing\n")
        )
        page = respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=HTML))
    else:
        _allow_robots()
        page = respx.get(PAGE_URL)
        if failure == "http_500":
            page.mock(return_value=httpx.Response(500, text="oops"))
        else:
            page.mock(side_effect=httpx.ConnectTimeout("timed out"))

    run_id = await run_ingestion(
        session_factory, source_ids=[src.id], http_factory=make_polite_client
    )
    async with session_factory() as s:
        run = await s.get(PipelineRun, run_id)
        fresh = await s.get(Source, src.id)
    assert run.stats["sources_failed"] == 1
    assert fresh.consecutive_failures == 1 and fresh.last_error
    if failure == "robots":
        assert "RobotsDisallowed" in fresh.last_error and page.call_count == 0
    snaps = await _snaps(session_factory, src.id)
    assert [s.id for s in snaps] == [kept.id]


@respx.mock
async def test_ingest_stores_snapshot_on_change_only(
    session_factory, watched, make_polite_client, a_day_passes
):
    """One run per day (a watched page is read at most once a day)."""
    _, src = watched
    _allow_robots()
    page = respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=HTML))
    for _ in range(2):
        await run_ingestion(session_factory, source_ids=[src.id], http_factory=make_polite_client)
        await a_day_passes(src.id)
    assert page.call_count == 2
    snaps = await _snaps(session_factory, src.id)
    assert len(snaps) == 1 and snaps[0].page_kind == "pricing"  # unchanged page: one snapshot
    page.mock(return_value=httpx.Response(200, text=HTML.replace("49 dollars", "59 dollars")))
    await run_ingestion(session_factory, source_ids=[src.id], http_factory=make_polite_client)
    snaps = await _snaps(session_factory, src.id)
    assert len(snaps) == 2 and "59 dollars" in snaps[1].diff_from_prev
    async with session_factory() as s:
        fresh = await s.get(Source, src.id)
    assert fresh.health == "ok" and fresh.last_fetched_at is not None
