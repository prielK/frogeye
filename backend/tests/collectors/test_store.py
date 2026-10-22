from datetime import date

from sqlalchemy import select

from app.collectors import get_collector
from app.collectors.base import FetchResult, MetricPointIn, RawItemIn
from app.collectors.rss import RssCollector
from app.collectors.store import mark_source_result, store_metrics, store_raw_items
from app.models import Competitor, RawItem
from app.models.enums import SourceKind
from app.models.ops import MetricPoint


async def test_store_dedupes_by_external_id(session, make_source):
    s = await make_source()
    item = RawItemIn("id-1", "https://x.test/a", "T", None, "body", {})
    assert await store_raw_items(session, s, [item, item]) == 1
    assert await store_raw_items(session, s, [item]) == 0
    row = (await session.execute(select(RawItem).where(RawItem.source_id == s.id))).scalar_one()
    assert len(row.content_hash) == 64


async def test_store_empty_list(session, make_source):
    assert await store_raw_items(session, await make_source(), []) == 0


async def test_health_degrades_and_recovers(session, make_source):
    s = await make_source()
    for _ in range(3):
        await mark_source_result(session, s, ok=False, error="boom")
    assert s.health == "degraded" and s.consecutive_failures == 3
    for _ in range(3):
        await mark_source_result(session, s, ok=False, error="boom")
    assert s.health == "down"
    await mark_source_result(session, s, ok=True, result=FetchResult(etag='"n"'))
    assert (s.health, s.consecutive_failures, s.etag) == ("ok", 0, '"n"')
    assert s.last_fetched_at is not None and s.last_error is None


async def test_success_keeps_previous_validators(session, make_source):
    s = await make_source(etag='"old"', last_modified="lm")
    await mark_source_result(session, s, ok=True, result=FetchResult(not_modified=True))
    assert (s.etag, s.last_modified) == ('"old"', "lm")


async def test_store_metrics_upserts(session):
    comp = Competitor(slug="c-x", name="C", tier=1, relationship="competitor")
    session.add(comp)
    await session.flush()
    d = date(2026, 10, 1)
    assert await store_metrics(session, comp.id, [MetricPointIn("stars", d, 1.0)]) == 1
    assert await store_metrics(session, comp.id, [MetricPointIn("stars", d, 5.0)]) == 1
    rows = (await session.execute(select(MetricPoint))).scalars().all()
    assert [r.value for r in rows] == [5.0]


def test_registry_has_rss():
    assert isinstance(get_collector(SourceKind.rss), RssCollector)
