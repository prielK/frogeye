from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.collectors import get_collector
from app.collectors.base import FetchResult, summarize
from app.collectors.daily_guard import ALREADY_FETCHED, claim_daily_fetches, once_a_day
from app.core.config import get_settings
from app.core.db import SessionLocal, get_session
from app.core.host_guard import DbHostGuard
from app.core.http import PoliteClient
from app.models import Competitor, RawItem, Source, User
from app.models.enums import SourceHealth, SourceKind
from app.schemas.sources import SourceOut, SourcePatch, SourceSample, SourceTestOut

router = APIRouter(prefix="/sources", tags=["sources"])

SAMPLE_SIZE = 5


def _out(src: Source, items_7d: int) -> SourceOut:
    return SourceOut(
        id=src.id,
        kind=src.kind,
        name=src.name,
        url=src.url,
        competitor_slug=src.competitor.slug if src.competitor else None,
        reliability=src.reliability,
        enabled=src.enabled,
        health=src.health,
        consecutive_failures=src.consecutive_failures,
        last_fetched_at=src.last_fetched_at,
        last_error=src.last_error,
        items_7d=items_7d,
    )


async def _items_7d(session: AsyncSession, source_ids: list[int]) -> dict[int, int]:
    """Items fetched in the last 7 days per source, in one grouped query."""
    if not source_ids:
        return {}
    cutoff = datetime.now(UTC) - timedelta(days=7)
    rows = await session.execute(
        select(RawItem.source_id, func.count())
        .where(RawItem.source_id.in_(source_ids), RawItem.fetched_at >= cutoff)
        .group_by(RawItem.source_id)
    )
    return {sid: n for sid, n in rows.all()}


async def _get_source(session: AsyncSession, source_id: int) -> Source:
    src = await session.get(Source, source_id)
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return src


@router.get("", response_model=list[SourceOut])
async def list_sources(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    kind: SourceKind | None = None,
    competitor: str | None = None,
    health: SourceHealth | None = None,
) -> list[SourceOut]:
    stmt = select(Source).outerjoin(Competitor, Source.competitor_id == Competitor.id)
    if kind is not None:
        stmt = stmt.where(Source.kind == kind.value)
    if competitor is not None:
        stmt = stmt.where(Competitor.slug == competitor)
    if health is not None:
        stmt = stmt.where(Source.health == health.value)
    sources = list((await session.execute(stmt.order_by(Source.id))).scalars().unique().all())
    counts = await _items_7d(session, [s.id for s in sources])
    return [_out(s, counts.get(s.id, 0)) for s in sources]


@router.patch("/{source_id}", response_model=SourceOut)
async def patch_source(
    source_id: int,
    body: SourcePatch,
    _: Annotated[User, Depends(require_roles("analyst"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SourceOut:
    src = await _get_source(session, source_id)
    src.enabled = body.enabled
    await session.commit()
    counts = await _items_7d(session, [src.id])
    return _out(src, counts.get(src.id, 0))


async def get_http() -> AsyncIterator[PoliteClient]:
    async with PoliteClient(get_settings(), guard=DbHostGuard(SessionLocal)) as http:
        yield http


@router.post(
    "/{source_id}/test",
    response_model=SourceTestOut,
    responses={409: {"description": f'A job board or watched page: "{ALREADY_FETCHED}".'}},
)
async def test_source(
    source_id: int,
    _: Annotated[User, Depends(require_roles("analyst"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    http: Annotated[PoliteClient, Depends(get_http)],
) -> SourceTestOut:
    """Dry-run the collector: nothing it fetches is stored and source health is untouched.

    A job board or watched page is read at most once per local day, by any path
    (app.collectors.daily_guard): a test of one already read today is refused (409), and a
    test that reads one is that day's request (the day's run then skips it)."""
    src = await _get_source(session, source_id)
    if once_a_day(src.kind):
        claimed = await claim_daily_fetches(session, [src.id], now=datetime.now(UTC))
        await session.commit()
        if not claimed:
            raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_FETCHED)
        await session.refresh(src)
    # Detach the source from the session and clear its conditional-GET validators: with the
    # stored ETag / Last-Modified the server answers 304 and the test would report 0 items.
    # Detached, the cleared fields can never be flushed back to the database.
    session.expunge(src)
    src.etag = None
    src.last_modified = None
    try:
        collector = get_collector(src.kind)
        result = await collector.fetch(src, None, http)
    except Exception as e:  # report any failure to the caller
        msg = str(e) or type(e).__name__
        error = f"{type(e).__name__}: {msg}"[:500]
        return SourceTestOut(
            ok=False,
            item_count=0,
            sample=[],
            error=error,
            postings_count=None,
            postings_complete=None,
            page_text_chars=None,
            metrics_count=None,
        )
    summary = summarize(src.kind, result)
    return SourceTestOut(
        ok=True,
        item_count=summary.count,
        sample=_sample(src, result),
        error=None,
        postings_count=summary.postings,
        postings_complete=summary.postings_complete,
        page_text_chars=summary.page_text_chars,
        metrics_count=summary.metrics,
    )


def _sample(src: Source, result: FetchResult) -> list[SourceSample]:
    """The first items, in the source's own unit."""
    if src.kind == SourceKind.job_board:
        return [
            SourceSample(title=p.title, url=p.url or src.url, published_at=p.posted_at)
            for p in result.postings[:SAMPLE_SIZE]
        ]
    if src.kind == SourceKind.page_watch:
        url = str((src.config or {}).get("url") or src.url)
        return (
            [SourceSample(title=src.name, url=url, published_at=None)] if result.page_text else []
        )
    return [
        SourceSample(title=i.title, url=i.url, published_at=i.published_at)
        for i in result.items[:SAMPLE_SIZE]
    ]
