"""Ingest stores job postings through sync_postings, with the same per-source commit as metrics."""

from sqlalchemy import func, select

from app.collectors.base import FetchResult, PostingIn
from app.models import Competitor, JobPosting, PipelineRun, Source
from app.processing.ingest import run_ingestion


def _posting(n: int) -> PostingIn:
    return PostingIn(
        external_id=f"p-{n}",
        title=f"Senior Backend Engineer {n}",
        department_raw="Engineering",
        location="Remote - US",
        remote=True,
        url=f"https://jobs.example.test/{n}",
        description_text=f"Role {n}.",
    )


async def _run(session_factory, ids, http_factory) -> int:
    return await run_ingestion(session_factory, source_ids=ids, http_factory=http_factory)


async def _stats(session_factory, run_id: int) -> dict:
    async with session_factory() as s:
        return (await s.get(PipelineRun, run_id)).stats


async def _open(session_factory, source_id: int) -> int:
    async with session_factory() as s:
        return await s.scalar(
            select(func.count())
            .select_from(JobPosting)
            .where(JobPosting.source_id == source_id, JobPosting.closed_at.is_(None))
        )


async def _board(session, make_source, competitor_factory, **comp):
    c = competitor_factory(**comp)
    session.add(c)
    await session.flush()
    src = await make_source(
        kind="job_board",
        name=f"{c.name} jobs",
        url=f"https://api.lever.co/v0/postings/{c.slug}?mode=json",
        competitor_id=c.id,
        reliability="A",
        config={"ats": "lever", "token": c.slug},
    )
    return c, src


async def test_ingest_stores_postings_and_counts_new_and_closed(
    session,
    session_factory,
    make_source,
    competitor_factory,
    fake_collectors,
    make_polite_client,
    a_day_passes,
):
    """One run per day (a board is read at most once a day)."""
    comp, src = await _board(session, make_source, competitor_factory)
    fc = fake_collectors(
        {
            src.name: FetchResult(
                postings=[_posting(1), _posting(2), _posting(3)], postings_complete=True
            )
        }
    )
    run1 = await _run(session_factory, [src.id], make_polite_client)
    stats = await _stats(session_factory, run1)
    assert (stats["postings_new"], stats["postings_closed"]) == (3, 0)
    assert stats["sources_failed"] == 0 and stats["fetched"] == 0  # postings are not raw items
    assert await _open(session_factory, src.id) == 3
    async with session_factory() as s:
        row = await s.scalar(select(JobPosting).where(JobPosting.external_id == "p-1"))
        assert row.competitor_id == comp.id and row.department == "Engineering"
        assert row.seniority == "Senior" and row.remote is True

    await a_day_passes(src.id)
    fc.by_name[src.name] = FetchResult(postings=[_posting(1), _posting(2)], postings_complete=True)
    run2 = await _run(session_factory, [src.id], make_polite_client)
    stats = await _stats(session_factory, run2)
    assert (stats["postings_new"], stats["postings_closed"]) == (0, 1)
    assert await _open(session_factory, src.id) == 2

    await a_day_passes(src.id)
    # an incomplete listing stores what it has and closes nothing
    fc.by_name[src.name] = FetchResult(postings=[_posting(4)], postings_complete=False)
    run3 = await _run(session_factory, [src.id], make_polite_client)
    stats = await _stats(session_factory, run3)
    assert (stats["postings_new"], stats["postings_closed"]) == (1, 0)
    assert await _open(session_factory, src.id) == 3

    await a_day_passes(src.id)
    # one successful, complete but empty listing closes nothing (an ATS glitch?) ...
    fc.by_name[src.name] = FetchResult(postings=[], postings_complete=True)
    run4 = await _run(session_factory, [src.id], make_polite_client)
    assert (await _stats(session_factory, run4))["postings_closed"] == 0
    assert await _open(session_factory, src.id) == 3
    await a_day_passes(src.id)
    # ... and the second one in a row confirms the board is empty: everything closes
    run5 = await _run(session_factory, [src.id], make_polite_client)
    assert (await _stats(session_factory, run5))["postings_closed"] == 3
    assert await _open(session_factory, src.id) == 0
    async with session_factory() as s:
        assert (await s.get(Source, src.id)).consecutive_empty == 2


async def test_postings_need_a_competitor_and_never_land_on_self(
    session, session_factory, make_source, competitor_factory, fake_collectors, make_polite_client
):
    loose = await make_source(
        kind="job_board",
        name="Loose board",
        url="https://api.lever.co/v0/postings/loose?mode=json",
        config={"ats": "lever", "token": "loose"},
    )
    _, own = await _board(
        session, make_source, competitor_factory, tier=0, relationship="self", is_self=True
    )
    fake_collectors(
        {
            "Loose board": FetchResult(postings=[_posting(1)], postings_complete=True),
            own.name: FetchResult(postings=[_posting(2)], postings_complete=True),
        }
    )
    run_id = await _run(session_factory, [loose.id, own.id], make_polite_client)
    stats = await _stats(session_factory, run_id)
    assert stats["postings_new"] == 0 and stats["sources_failed"] == 0
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(JobPosting)) == 0
        assert (await s.get(Competitor, own.competitor_id)).is_self


async def test_no_postings_and_not_complete_skips_sync(
    session, session_factory, make_source, competitor_factory, fake_collectors, make_polite_client
):
    _, src = await _board(session, make_source, competitor_factory)
    fake_collectors({src.name: FetchResult()})  # e.g. an RSS-style empty result
    run_id = await _run(session_factory, [src.id], make_polite_client)
    stats = await _stats(session_factory, run_id)
    assert (stats["postings_new"], stats["postings_closed"]) == (0, 0)
