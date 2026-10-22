"""Persistence helpers for collector output and source health."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import FetchResult, MetricPointIn, RawItemIn
from app.models import RawItem, Source
from app.models.enums import SourceHealth
from app.models.ops import MetricPoint

DEGRADED_AFTER = 3
DOWN_AFTER = 6


def _content_hash(title: str | None, raw_text: str | None) -> str:
    return hashlib.sha256(((title or "") + "\n" + (raw_text or "")).encode("utf-8")).hexdigest()


async def store_raw_items(session: AsyncSession, source: Source, items: list[RawItemIn]) -> int:
    """Insert items, skipping ones already stored. Returns the number of new rows."""
    if not items:
        return 0
    seen: dict[str, RawItemIn] = {}
    for it in items:  # a batch may repeat an id; ON CONFLICT cannot hit the same row twice
        seen.setdefault(it.external_id, it)
    rows = [
        {
            "source_id": source.id,
            "url": it.url,
            "external_id": it.external_id,
            "title": it.title,
            "published_at": it.published_at,
            "content_hash": _content_hash(it.title, it.raw_text),
            "payload": it.payload,
            "raw_text": it.raw_text,
        }
        for it in seen.values()
    ]
    stmt = (
        pg_insert(RawItem)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["source_id", "external_id"])
        .returning(RawItem.id)
    )
    result = await session.execute(stmt)
    return len(result.all())


async def store_metrics(
    session: AsyncSession, competitor_id: int, metrics: list[MetricPointIn]
) -> int:
    """Upsert metric points on (competitor_id, metric, date). Returns rows written."""
    if not metrics:
        return 0
    latest: dict[tuple[str, object], MetricPointIn] = {}
    for m in metrics:
        latest[(m.metric, m.date)] = m
    rows = [
        {"competitor_id": competitor_id, "metric": m.metric, "date": m.date, "value": m.value}
        for m in latest.values()
    ]
    stmt = pg_insert(MetricPoint).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["competitor_id", "metric", "date"], set_={"value": stmt.excluded.value}
    )
    result = await session.execute(stmt.returning(MetricPoint.metric))
    return len(result.all())


async def mark_source_result(
    session: AsyncSession,
    source: Source,
    *,
    ok: bool,
    error: str | None = None,
    result: FetchResult | None = None,
) -> None:
    if ok:
        source.consecutive_failures = 0
        source.health = SourceHealth.ok.value
        source.last_error = None
        source.last_fetched_at = datetime.now(UTC)
        if result is not None:
            if result.etag is not None:
                source.etag = result.etag
            if result.last_modified is not None:
                source.last_modified = result.last_modified
    else:
        source.consecutive_failures = (source.consecutive_failures or 0) + 1
        source.last_error = error
        if source.consecutive_failures >= DOWN_AFTER:
            source.health = SourceHealth.down.value
        elif source.consecutive_failures >= DEGRADED_AFTER:
            source.health = SourceHealth.degraded.value
    await session.flush()
