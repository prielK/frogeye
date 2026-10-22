from app.cli import check_sources
from app.core.http import InMemoryHostGuard
from app.models import Source


async def test_check_reports_paused_host_without_probing(make_polite_client):
    guard = InMemoryHostGuard()
    await guard.record("paused.test", 403)
    src = Source(kind="rss", name="Feed", url="https://paused.test/feed", config={})
    import asyncio

    async with make_polite_client(guard=guard) as client:
        row = await check_sources._check(src, client, asyncio.Semaphore(1))
    assert row[2] == "paused" and "paused until" in row[4]


async def test_make_guard_falls_back_to_memory_when_db_unreachable(monkeypatch):
    class Broken:
        def __call__(self):
            raise OSError("db down")

    monkeypatch.setattr(check_sources, "SessionLocal", Broken())
    assert isinstance(await check_sources.make_guard(0.5), InMemoryHostGuard)


async def test_make_guard_uses_db_when_available(monkeypatch, session_factory):
    from app.core.host_guard import DbHostGuard

    monkeypatch.setattr(check_sources, "SessionLocal", session_factory)
    assert isinstance(await check_sources.make_guard(), DbHostGuard)


async def test_check_counts_a_boards_postings_and_a_pages_text(monkeypatch, make_polite_client):
    """A working job board or watched page is not "0 items": the items
    column says what it returned, in its own unit."""
    import asyncio

    from app.collectors.base import FetchResult, PostingIn

    results = {
        "job_board": FetchResult(
            postings=[PostingIn(external_id=f"p{i}", title=f"Role {i}") for i in range(3)],
            postings_complete=True,
        ),
        "page_watch": FetchResult(page_text="Team plan: 49 dollars"),
    }

    class Fake:
        def __init__(self, kind):
            self.kind = kind

        async def fetch(self, source, since, http):
            return results[self.kind]

    monkeypatch.setattr(check_sources, "get_collector", Fake)
    board = Source(kind="job_board", name="Jobs", url="https://jobs.test", config={})
    page = Source(kind="page_watch", name="Pricing", url="https://acme.test/pricing", config={})
    async with make_polite_client() as client:
        rows = [await check_sources._check(s, client, asyncio.Semaphore(1)) for s in (board, page)]
    assert [(r[2], r[3]) for r in rows] == [("ok", "3 postings"), ("ok", "1 page, 21 chars")]
    results["job_board"] = FetchResult(postings=[PostingIn(external_id="p", title="R")])
    async with make_polite_client() as client:
        row = await check_sources._check(board, client, asyncio.Semaphore(1))
    assert row[3] == "1 posting (partial listing)"


def test_a_metric_source_reports_its_readings_not_zero_items():
    """A GitHub repository-stats source returns metrics (stars and forks), no items: the
    check reports its readings, never "0"."""
    from datetime import date

    from app.collectors.base import FetchResult, MetricPointIn, summarize

    result = FetchResult(
        metrics=[
            MetricPointIn("github_stars:acme/acme", date(2026, 10, 20), 120),
            MetricPointIn("github_forks:acme/acme", date(2026, 10, 20), 9),
        ]
    )
    summary = summarize("github_repo_stats", result)
    assert (summary.count, summary.metrics) == (2, 2)
    assert check_sources.returned(summary) == "2 readings"
    assert check_sources.returned(summarize("rss", FetchResult())) == "0"
