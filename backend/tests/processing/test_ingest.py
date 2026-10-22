from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select, text

from app.collectors.base import CollectorError, FetchResult, MetricPointIn, RawItemIn
from app.models import Document, PipelineRun, Source
from app.models.ops import MetricPoint
from app.processing import ingest as ingest_mod
from app.processing.ingest import INGEST_LOCK_KEY, RunAlreadyActive, run_ingestion

WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november "
    "oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu"
).split()


def _body(seed: int) -> str:
    # long (> SHORT_TEXT_CHARS, so no full-text fetch) and distinct per seed (no simhash dups)
    rot = WORDS[seed % 26 :] + WORDS[: seed % 26]
    return "Sonatype releases update. " + " ".join(f"{w}{seed}" for w in rot * 6)


def _item(n: int) -> RawItemIn:
    return RawItemIn(
        external_id=f"ext-{n}",
        url=f"https://x.test/post-{n}",
        title=f"Sonatype story {n}",
        published_at=datetime(2026, 10, 15, tzinfo=UTC),
        raw_text=_body(n),
        payload={},
    )


@pytest.fixture
def fake_http(make_polite_client):
    return make_polite_client


async def _ids(session, *names: str) -> list[int]:
    rows = await session.execute(select(Source.id).where(Source.name.in_(names)))
    return list(rows.scalars().all())


async def _get_run(session_factory, run_id: int) -> PipelineRun:
    async with session_factory() as s:
        return await s.get(PipelineRun, run_id)


async def test_run_records_funnel(seeded, session_factory, fake_collectors, fake_http):
    fake_collectors({"Sonatype Blog": FetchResult(items=[_item(1), _item(2), _item(3)])})
    ids = await _ids(seeded, "Sonatype Blog")
    run_id = await run_ingestion(session_factory, source_ids=ids, http_factory=fake_http)
    run = await _get_run(session_factory, run_id)
    assert run.status == "succeeded" and run.finished_at is not None and run.error is None
    expected = {
        "sources_attempted": 1,
        "sources_failed": 0,
        "sources_skipped_today": 0,  # boards and pages already read today
        "fetched": 3,
        "new_raw": 3,
        "documents_created": 3,
        "reprocessed": 0,
        "duplicates": 0,
        "prefilter_passed": 3,
        "prefilter_rejected": 0,
        "metrics_written": 0,
        "postings_new": 0,
        "postings_closed": 0,
    }
    assert run.stats == expected


async def test_second_run_is_idempotent(seeded, session_factory, fake_collectors, fake_http):
    fc = fake_collectors({"Sonatype Blog": FetchResult(items=[_item(1), _item(2)])})
    ids = await _ids(seeded, "Sonatype Blog")
    await run_ingestion(session_factory, source_ids=ids, http_factory=fake_http)
    run2 = await run_ingestion(session_factory, source_ids=ids, http_factory=fake_http)
    stats = (await _get_run(session_factory, run2)).stats
    assert stats["new_raw"] == 0 and stats["documents_created"] == 0
    # the second fetch receives `since` (last_fetched_at minus the lookback)
    assert fc.seen[0][1] is None and fc.seen[1][1] is not None


async def test_failing_source_does_not_abort_run(
    seeded, session_factory, fake_collectors, fake_http
):
    fake_collectors(
        {
            "Sonatype Blog": CollectorError("boom"),
            "Sonatype Press": FetchResult(items=[_item(1), _item(2)]),
        }
    )
    ids = await _ids(seeded, "Sonatype Blog", "Sonatype Press")
    run_id = await run_ingestion(session_factory, source_ids=ids, http_factory=fake_http)
    run = await _get_run(session_factory, run_id)
    assert run.status == "succeeded"
    assert run.stats["sources_failed"] == 1 and run.stats["sources_attempted"] == 2
    assert run.stats["documents_created"] == 2
    async with session_factory() as s:
        bad = (await s.execute(select(Source).where(Source.name == "Sonatype Blog"))).scalar_one()
        assert bad.consecutive_failures == 1 and "boom" in bad.last_error


async def test_same_article_two_sources_one_document(
    seeded, session_factory, fake_collectors, fake_http
):
    same = _body(7)
    a = RawItemIn("a1", "https://x.test/a?utm_source=rss", "Sonatype news", None, same, {})
    b = RawItemIn("b1", "https://x.test/a", "Sonatype news", None, same, {})
    fake_collectors(
        {"Sonatype Blog": FetchResult(items=[a]), "Sonatype Press": FetchResult(items=[b])}
    )
    ids = await _ids(seeded, "Sonatype Blog", "Sonatype Press")
    run_id = await run_ingestion(session_factory, source_ids=ids, http_factory=fake_http)
    run = await _get_run(session_factory, run_id)
    assert run.stats["new_raw"] == 2 and run.stats["documents_created"] == 1
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Document)) == 1


async def test_metrics_written_and_competitor_less_skipped(
    seeded, session_factory, fake_collectors, fake_http, make_source
):
    metrics = [MetricPointIn("stars", date(2026, 10, 16), 5.0)]
    loose = await make_source(name="Loose metrics")
    fake_collectors(
        {
            "Sonatype Blog": FetchResult(metrics=metrics),
            "Loose metrics": FetchResult(metrics=metrics),
        }
    )
    ids = await _ids(seeded, "Sonatype Blog") + [loose.id]
    run_id = await run_ingestion(session_factory, source_ids=ids, http_factory=fake_http)
    assert (await _get_run(session_factory, run_id)).stats["metrics_written"] == 1
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(MetricPoint)) == 1


async def test_unexpected_error_marks_run_failed(
    seeded, session_factory, fake_collectors, fake_http, monkeypatch
):
    fake_collectors({})

    async def boom(*a, **k):
        raise RuntimeError("normalize exploded")

    monkeypatch.setattr(ingest_mod, "normalize_new_raw_items", boom)
    with pytest.raises(RuntimeError):
        await run_ingestion(session_factory, source_ids=[], http_factory=fake_http)
    async with session_factory() as s:
        run = (
            await s.execute(select(PipelineRun).order_by(PipelineRun.id.desc()).limit(1))
        ).scalar_one()
    assert run.status == "failed" and "normalize exploded" in run.error


async def test_concurrent_run_rejected(engine, session_factory, seeded, fake_http):
    async with engine.connect() as other:
        await other.execute(text("select pg_advisory_lock(:k)"), {"k": INGEST_LOCK_KEY})
        try:
            with pytest.raises(RunAlreadyActive):
                await run_ingestion(session_factory, source_ids=[], http_factory=fake_http)
        finally:
            await other.execute(text("select pg_advisory_unlock(:k)"), {"k": INGEST_LOCK_KEY})
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(PipelineRun)) == 0


# -- sweep: documents a previous run never screened ----------------------


async def test_sweep_screens_documents_left_new_by_an_earlier_run(
    seeded, session_factory, fake_collectors, fake_http, make_document
):
    # e.g. a run that died between normalize and prefilter, or a doc reset by migration 0003
    src = (await seeded.execute(select(Source).where(Source.name == "Sonatype Blog"))).scalar_one()
    stranded = await make_document(
        source_id=src.id, title="Sonatype ships Nexus 4", clean_text="", status="new"
    )
    screened = await make_document(
        source_id=src.id,
        title="Already screened",
        clean_text="",
        status="new",
        prefilter={"matched_terms": [], "scope_hint": "competitor"},
    )
    fake_collectors({})
    run_id = await run_ingestion(session_factory, source_ids=[], http_factory=fake_http)
    stats = (await _get_run(session_factory, run_id)).stats
    assert stats["documents_created"] == 0 and stats["reprocessed"] == 1
    assert stats["prefilter_passed"] == 1 and stats["duplicates"] == 0
    async with session_factory() as s:
        doc = await s.get(Document, stranded.id)
        assert doc.prefilter is not None and doc.scope_hint == "competitor"
        assert doc.simhash is not None
        assert (await s.get(Document, screened.id)).prefilter["matched_terms"] == []


async def test_sweep_dedups_reset_docs_against_earlier_ones(
    seeded, session_factory, fake_collectors, fake_http, make_document
):
    from app.processing.normalize import content_hash

    src = (await seeded.execute(select(Source).where(Source.name == "Sonatype Blog"))).scalar_one()
    when = datetime(2026, 10, 15, tzinfo=UTC)
    first = await make_document(
        source_id=src.id,
        title="Sonatype news",
        clean_text="",
        content_hash=content_hash("Sonatype news", ""),
        published_at=when,
        prefilter={"matched_terms": ["Sonatype"], "scope_hint": "competitor"},
    )
    same = await make_document(
        source_id=src.id,
        title="Sonatype news",
        clean_text="",
        content_hash=content_hash("Sonatype news", ""),
        published_at=when,
    )
    other = await make_document(
        source_id=src.id,
        title="Sonatype other news",
        clean_text="",
        content_hash=content_hash("Sonatype other news", ""),
        published_at=when,
    )
    fake_collectors({})
    run_id = await run_ingestion(session_factory, source_ids=[], http_factory=fake_http)
    stats = (await _get_run(session_factory, run_id)).stats
    assert stats["reprocessed"] == 2 and stats["duplicates"] == 1
    async with session_factory() as s:
        assert (await s.get(Document, same.id)).duplicate_of == first.id
        assert (await s.get(Document, other.id)).status == "new"


# -- runs left "running" by a stopped worker ----------------------------


async def test_new_run_marks_interrupted_runs_failed(
    seeded, session_factory, fake_collectors, fake_http, session
):
    zombie = PipelineRun(kind="ingestion", trigger="manual", status="running", stats={})
    finished = PipelineRun(kind="ingestion", trigger="manual", status="succeeded", stats={})
    session.add_all([zombie, finished])
    await session.flush()
    fake_collectors({})
    run_id = await run_ingestion(session_factory, source_ids=[], http_factory=fake_http)
    async with session_factory() as s:
        old = await s.get(PipelineRun, zombie.id)
        assert old.status == "failed"
        assert old.error == "interrupted (worker stopped before finishing)"
        assert old.finished_at is not None
        assert (await s.get(PipelineRun, finished.id)).error is None
        assert (await s.get(PipelineRun, run_id)).status == "succeeded"


# -- since lookback ---------------------------------------------------------


async def test_since_looks_back_two_days_before_last_fetch(
    seeded, session_factory, fake_collectors, fake_http
):
    from datetime import timedelta

    fc = fake_collectors({"Sonatype Blog": FetchResult(items=[_item(1)])})
    ids = await _ids(seeded, "Sonatype Blog")
    await run_ingestion(session_factory, source_ids=ids, http_factory=fake_http)
    async with session_factory() as s:
        last = (
            (await s.execute(select(Source).where(Source.name == "Sonatype Blog")))
            .scalar_one()
            .last_fetched_at
        )
    await run_ingestion(session_factory, source_ids=ids, http_factory=fake_http)
    assert fc.seen[1][1] == last - timedelta(days=2)


# -- the advisory lock is always released ------------------------------


async def _lock_free(engine) -> bool:
    """Probe from a different connection: can another session take the lock right now?"""
    async with engine.connect() as other:
        got = (
            await other.execute(text("select pg_try_advisory_lock(:k)"), {"k": INGEST_LOCK_KEY})
        ).scalar_one()
        if got:
            await other.execute(text("select pg_advisory_unlock(:k)"), {"k": INGEST_LOCK_KEY})
        await other.rollback()
    return bool(got)


async def test_lock_released_after_success_and_failure(
    engine, seeded, session_factory, fake_collectors, fake_http, monkeypatch
):
    fake_collectors({})
    await run_ingestion(session_factory, source_ids=[], http_factory=fake_http)
    assert await _lock_free(engine)

    async def boom(*a, **k):
        raise RuntimeError("normalize exploded")

    monkeypatch.setattr(ingest_mod, "normalize_new_raw_items", boom)
    with pytest.raises(RuntimeError):
        await run_ingestion(session_factory, source_ids=[], http_factory=fake_http)
    assert await _lock_free(engine)


async def test_lock_held_probe_matches_pg_locks(engine, session):
    assert not await ingest_mod.ingestion_lock_held(session)
    async with engine.connect() as other:
        await other.execute(text("select pg_advisory_lock(:k)"), {"k": INGEST_LOCK_KEY})
        try:
            assert await ingest_mod.ingestion_lock_held(session)
        finally:
            await other.execute(text("select pg_advisory_unlock(:k)"), {"k": INGEST_LOCK_KEY})
        # a different key (or the two-int4 form of the same number) is not our lock
        await other.execute(text("select pg_advisory_lock(42002)"))
        await other.execute(text("select pg_advisory_lock(0, 42001)"))
        try:
            assert not await ingest_mod.ingestion_lock_held(session)
        finally:
            await other.execute(text("select pg_advisory_unlock(42002)"))
            await other.execute(text("select pg_advisory_unlock(0, 42001)"))
        await other.rollback()


async def test_owned_lock_connection_is_autocommit_and_released(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine)
    conn, owned = await ingest_mod.acquire_run_lock(factory)
    try:
        assert owned
        pid = (await conn.execute(text("select pg_backend_pid()"))).scalar_one()
        async with engine.connect() as other:
            state = (
                await other.execute(
                    text("select state from pg_stat_activity where pid = :p"), {"p": pid}
                )
            ).scalar_one()
            await other.rollback()
        assert state == "idle"  # not "idle in transaction" for the whole run
        assert not await _lock_free(engine)
    finally:
        await ingest_mod.release_run_lock(conn, owned)
    assert await _lock_free(engine)


async def test_failed_unlock_invalidates_the_connection(engine):
    """A connection that may still hold the lock must never go back to the pool."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine)
    conn, owned = await ingest_mod.acquire_run_lock(factory)
    calls: list[str] = []

    class FailingUnlock:
        async def execute(self, *a, **k):
            calls.append("execute")
            raise RuntimeError("connection reset during unlock")

        async def invalidate(self):
            calls.append("invalidate")
            await conn.invalidate()

        async def close(self):
            calls.append("close")
            await conn.close()

    await ingest_mod.release_run_lock(FailingUnlock(), owned)
    assert calls == ["execute", "invalidate", "close"]
    # the invalidated connection's server session ended, which released the lock
    for _ in range(50):
        if await _lock_free(engine):
            break
        await asyncio.sleep(0.1)
    assert await _lock_free(engine)


async def test_unlock_success_returns_connection_without_invalidating():
    calls: list[str] = []

    class Ok:
        async def execute(self, *a, **k):
            calls.append("execute")

        async def invalidate(self):
            calls.append("invalidate")

        async def close(self):
            calls.append("close")

    await ingest_mod.release_run_lock(Ok(), True)
    assert calls == ["execute", "close"]
