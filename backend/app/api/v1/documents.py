import base64
import binascii
import json
import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_session
from app.models import Competitor, Document, DocumentCompetitor, RawItem, Source, User
from app.models.enums import DocStatus, Scope
from app.processing.self_news import self_news_document
from app.schemas.documents import DocumentOut, DocumentPage

router = APIRouter(prefix="/documents", tags=["documents"])

EXCERPT_CHARS = 300


def encode_cursor(published_at: datetime | None, doc_id: int) -> str:
    payload = {"p": published_at.isoformat() if published_at else None, "i": doc_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime | None, int]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        p = data["p"]
        return (datetime.fromisoformat(p) if p is not None else None), int(data["i"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid cursor") from None


def _like_escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _excerpt(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:EXCERPT_CHARS]


@router.get("", response_model=DocumentPage)
async def list_documents(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    competitor: str | None = None,
    scope: Scope | None = None,
    doc_status: Annotated[DocStatus | None, Query(alias="status")] = None,
    q: str | None = None,
    since: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DocumentPage:
    # Link to the item's original URL; canonical_url is a dedup key (tracking params
    # stripped, query re-ordered), not necessarily an address the publisher serves.
    stmt = (
        select(Document, Source.name, RawItem.url)
        .join(Source, Source.id == Document.source_id)
        .join(RawItem, RawItem.id == Document.raw_item_id)
    )
    stmt = stmt.where(~self_news_document())  # never JFrog-self news, any role
    if doc_status is None:
        stmt = stmt.where(Document.status != DocStatus.duplicate.value)
    else:
        stmt = stmt.where(Document.status == doc_status.value)
    if competitor is not None:
        stmt = stmt.where(
            exists().where(
                DocumentCompetitor.document_id == Document.id,
                DocumentCompetitor.competitor_id == Competitor.id,
                Competitor.slug == competitor,
            )
        )
    if scope is not None:
        stmt = stmt.where(Document.scope_hint == scope.value)
    if q:
        stmt = stmt.where(Document.title.ilike(f"%{_like_escape(q)}%", escape="\\"))
    if since is not None:
        stmt = stmt.where(
            or_(
                Document.published_at >= since,
                and_(Document.published_at.is_(None), Document.created_at >= since),
            )
        )
    if cursor:
        c_pub, c_id = decode_cursor(cursor)
        if c_pub is None:
            stmt = stmt.where(Document.published_at.is_(None), Document.id < c_id)
        else:
            stmt = stmt.where(
                or_(
                    Document.published_at < c_pub,
                    and_(Document.published_at == c_pub, Document.id < c_id),
                    Document.published_at.is_(None),
                )
            )
    stmt = stmt.order_by(Document.published_at.desc().nulls_last(), Document.id.desc()).limit(
        limit + 1
    )
    rows = (await session.execute(stmt)).all()
    page, extra = rows[:limit], rows[limit:]

    slugs: dict[int, list[str]] = {}
    if page:
        link = await session.execute(
            select(DocumentCompetitor.document_id, Competitor.slug)
            .join(Competitor, Competitor.id == DocumentCompetitor.competitor_id)
            .where(DocumentCompetitor.document_id.in_([d.id for d, _, _ in page]))
            .order_by(Competitor.slug)
        )
        for doc_id, slug in link.all():
            slugs.setdefault(doc_id, []).append(slug)

    items = [
        DocumentOut(
            id=d.id,
            title=d.title,
            url=raw_url or d.canonical_url,
            source_name=source_name,
            published_at=d.published_at,
            status=d.status,
            scope_hint=d.scope_hint,
            competitors=slugs.get(d.id, []),
            matched_terms=list((d.prefilter or {}).get("matched_terms") or []),
            excerpt=_excerpt(d.clean_text),
        )
        for d, source_name, raw_url in page
    ]
    last = page[-1][0] if extra else None
    return DocumentPage(
        items=items, next_cursor=encode_cursor(last.published_at, last.id) if last else None
    )
