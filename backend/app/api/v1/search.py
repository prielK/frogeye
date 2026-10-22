"""Search everything: articles, signals and JFrog docs ranked together (GET /search).

Hybrid retrieval with query understanding and an optional rerank (rag.search). Without an
OpenAI key, or when the query embedding fails or the budget is spent, it answers
keyword-only and says so (`search_mode`). Visibility is applied in the retrieval SQL,
before the candidate cap and the vector floor: signal hits follow the signal's current status,
article hits need a document triage judged relevant that is not JFrog-self news (the Feed's
predicate; JFrog's own docs stay).
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.api.deps import get_current_user, get_optional_llm, get_session_factory
from app.api.ratelimit import SlidingWindowLimiter
from app.api.visibility import HIDDEN_SCOPES, visible_statuses
from app.collectors.attribution import display_title
from app.core.db import get_session
from app.models import Competitor, Document, JfrogKbDoc, RawItem, User
from app.models.enums import DocStatus, RetrievalKind
from app.models.gold import Signal
from app.rag.kb import kb_title
from app.rag.search import QueryPlan, SearchFilters, SearchHit, make_snippet, search
from app.schemas.search import QueryPlanOut, SearchHitOut, SearchResponse

router = APIRouter(prefix="/search", tags=["search"])

SEARCHABLE_DOCUMENTS = [DocStatus.relevant.value]  # what triage kept

# 30 searches per minute per user; /signals?q holds its own bucket.
limiter = SlidingWindowLimiter(30, 60.0)


def rate_limit(user: Annotated[User, Depends(get_current_user)]) -> User:
    limiter.hit(user.id)
    return user


def _plan_out(plan: QueryPlan) -> QueryPlanOut:
    return QueryPlanOut(
        rewritten=plan.rewritten,
        variants=plan.variants,
        competitors=list(plan.competitor_slugs),
        date_from=plan.date_from,
        date_to=plan.date_to,
        kinds=[str(k) for k in plan.kinds],
    )


async def _visible(
    session: AsyncSession, hits: list[SearchHit], statuses: list[str]
) -> list[SearchHit]:
    """A signal hit on /search shows only while the signal's CURRENT status is
    in `statuses`, the user's `visible_statuses` (readers auto|approved; analysts also
    needs_review; rejected and suppressed no one). The index keeps every analysed signal for
    the agents and carries no status, so this is decided here. Other kinds pass through."""
    signal_ids = [h.ref_id for h in hits if h.kind == RetrievalKind.signal]
    if not signal_ids:
        return hits
    allowed = set(
        await session.scalars(
            select(Signal.id).where(
                Signal.id.in_(signal_ids),
                Signal.status.in_(statuses),
                Signal.scope.not_in(HIDDEN_SCOPES),
            )
        )
    )
    return [h for h in hits if h.kind != RetrievalKind.signal or h.ref_id in allowed]


async def _describe(session: AsyncSession, hits: list[SearchHit], q: str) -> list[SearchHitOut]:
    def ids(kind: RetrievalKind) -> list[int]:
        return [h.ref_id for h in hits if h.kind == kind]

    docs = {
        d.id: (display_title(d.title, raw_url or d.canonical_url), raw_url or d.canonical_url)
        for d, raw_url in (
            await session.execute(
                select(Document, RawItem.url)
                .join(RawItem, RawItem.id == Document.raw_item_id)
                .where(Document.id.in_(ids(RetrievalKind.article_chunk)))
            )
        ).all()
    }
    signals = dict(
        (
            await session.execute(
                select(Signal.id, Signal.title).where(Signal.id.in_(ids("signal")))
            )
        ).all()
    )
    kb = {
        k.id: (kb_title(k.title, k.url), k.url)  # the product, not the URL
        for k in await session.scalars(
            select(JfrogKbDoc).where(JfrogKbDoc.id.in_(ids(RetrievalKind.jfrog_kb)))
        )
    }
    comp_ids = {c for h in hits for c in h.competitor_ids}
    slugs = dict(
        (
            await session.execute(
                select(Competitor.id, Competitor.slug).where(
                    Competitor.id.in_(comp_ids),
                    Competitor.is_self.is_(False),
                )
            )
        ).all()
    )

    out = []
    for h in hits:
        title: str | None
        url: str | None
        if h.kind == RetrievalKind.article_chunk:
            title, url = docs.get(h.ref_id, (None, None))
        elif h.kind == RetrievalKind.signal:
            title, url = signals.get(h.ref_id), f"/signals/{h.ref_id}"
        else:
            title, url = kb.get(h.ref_id, (None, None))
        out.append(
            SearchHitOut(
                kind=h.kind,
                ref_id=h.ref_id,
                title=title or "Untitled",
                url=url,
                snippet=make_snippet(h.text, q),
                published_at=h.published_at,
                competitors=sorted(slugs[c] for c in h.competitor_ids if c in slugs),
                score=h.score,
            )
        )
    return out


@router.get("", response_model=SearchResponse)
async def search_endpoint(
    user: Annotated[User, Depends(rate_limit)],
    llm: Annotated[AccountedLLM | None, Depends(get_optional_llm)],
    session: Annotated[AsyncSession, Depends(get_session)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    q: Annotated[str, Query(min_length=2, max_length=300)],
    kinds: Annotated[list[RetrievalKind] | None, Query()] = None,
    competitor: Annotated[list[str] | None, Query()] = None,
    since: datetime | None = None,
    rerank: bool | None = None,
) -> SearchResponse:
    # Read the role before the rollback below expires `user` (as /signals does).
    statuses = visible_statuses(user)
    # end the auth transaction so no connection sits idle-in-transaction across LLM awaits.
    await session.rollback()
    result = await search(
        session_factory,
        llm,
        q,
        filters=SearchFilters(
            kinds=[str(k) for k in kinds] if kinds else None,
            competitor_slugs=competitor or None,
            date_from=since,
            signal_statuses=statuses,
            document_statuses=SEARCHABLE_DOCUMENTS,
            hide_self_documents=True,  # for every role
        ),
        rerank=rerank,
        vector_floor=True,  # no unrelated vector-only tail in the UI
        ctx=RunCtx(agent_run_id=None, node="search", agent="search"),
    )
    async with session_factory() as describe_session:
        visible = await _visible(describe_session, result.hits, statuses)
        hits = await _describe(describe_session, visible, q)
    return SearchResponse(
        plan=_plan_out(result.plan) if result.plan else None, hits=hits, search_mode=result.mode
    )
