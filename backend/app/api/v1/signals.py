"""Signals Explorer API: a keyset-paged, faceted list; hybrid search (`q`); detail.

Visibility: non-analysts only ever see status auto|approved (list, search, detail,
related); analysts default to auto|approved|needs_review and may ask for any status.
Search: hybrid retrieval over signal items, keyword-only when no query embedding can
be had (no OpenAI key, an embedding error or the budget); top 50, unpaged. The facets and
the reader's visibility apply inside the retrieval SQL, so the candidate cap and the
vector floor ("best similarity - 0.12") are taken over the signals the reader asked for and
may see, never over the whole index.
"""

import base64
import binascii
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Select, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError
from app.agents.runtime.context import RunCtx
from app.api.analysis_view import CardAnalysis, build_sources_and_analysis, card_analyses
from app.api.deps import get_current_user, get_optional_llm, get_session_factory
from app.api.ratelimit import SlidingWindowLimiter
from app.api.visibility import (
    HIDDEN_SCOPES,
    PUBLIC_STATUSES,
    is_analyst,
    visible_signal_clause,
    visible_statuses,
)
from app.collectors.attribution import source_label
from app.core.db import get_session
from app.models import (
    Capability,
    Competitor,
    Document,
    Evidence,
    Kit,
    RawItem,
    Signal,
    SignalCapability,
    SignalCompetitor,
    SignalDocument,
    SignalKit,
    Source,
    User,
)
from app.models.enums import RetrievalKind, Scope, SignalStatus, SignalType, Stance
from app.rag.search import hybrid_search
from app.schemas.signals import (
    CapabilityRef,
    EvidenceOut,
    KitRef,
    RoleActions,
    SignalCard,
    SignalDetail,
    SignalDocumentOut,
    SignalPage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/signals", tags=["signals"])

Q_TOP = 50  # search returns the top 50, unpaged
Q_POOL = 200  # hybrid candidates per arm, before visibility and facets narrow them
RELATED_K = 5
SEARCH_CTX = RunCtx(agent_run_id=None, node="signals_search", agent="search")

# 30 searches per minute per user, a bucket separate from /search's.
limiter = SlidingWindowLimiter(30, 60.0)


def search_rate_limit(request: Request, user: Annotated[User, Depends(get_current_user)]) -> User:
    """Requests with `q` (one embedding call each) are limited per user like /search;
    plain listing is not."""
    if request.query_params.get("q") is not None:
        limiter.hit(user.id)
    return user


# ---- visibility, cursor, facets --------------------------------------------------------


def encode_cursor(importance: int, signal_id: int) -> str:
    payload = {"imp": importance, "id": signal_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


# Cursor bounds: what signals.importance (smallint, >= 0) and signals.id (integer
# serial) can hold; a real cursor is ~40 chars. Anything else is a 422, never a DB error.
MAX_CURSOR_CHARS = 200
IMPORTANCE_RANGE = range(0, 2**15)
ID_RANGE = range(1, 2**31)


def decode_cursor(cursor: str) -> tuple[int, int]:
    invalid = HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid cursor")
    if len(cursor) > MAX_CURSOR_CHARS:
        raise invalid
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        importance, signal_id = data["imp"], data["id"]
    except (binascii.Error, ValueError, KeyError, TypeError, IndexError, UnicodeError):
        raise invalid from None
    for value, valid in ((importance, IMPORTANCE_RANGE), (signal_id, ID_RANGE)):
        if type(value) is not int or value not in valid:  # bool is not an int here
            raise invalid
    return importance, signal_id


def _linked(link, column, model, slugs: list[str]):
    """Signal ids linked (through `link`.`column`) to any of the `model` rows with `slugs`."""
    return (
        select(link.signal_id)
        .join(model, model.id == getattr(link, column))
        .where(model.slug.in_(slugs))
    )


@dataclass(frozen=True)
class SignalFacets:
    statuses: list[str]
    competitors: list[str] | None = None
    types: list[str] | None = None
    scope: str | None = None
    stance: str | None = None
    capabilities: list[str] | None = None
    kits: list[str] | None = None
    min_importance: int | None = None
    since: datetime | None = None

    @property
    def narrows(self) -> bool:
        """Any facet besides the statuses (those reach the retrieval SQL on their own)."""
        return any(
            v is not None
            for v in (
                self.competitors,
                self.types,
                self.scope,
                self.stance,
                self.capabilities,
                self.kits,
                self.min_importance,
                self.since,
            )
        )

    def apply(self, stmt: Select) -> Select:
        stmt = stmt.where(Signal.status.in_(self.statuses), Signal.scope.not_in(HIDDEN_SCOPES))
        if self.competitors:
            stmt = stmt.where(
                Signal.id.in_(
                    _linked(SignalCompetitor, "competitor_id", Competitor, self.competitors)
                )
            )
        if self.types:
            stmt = stmt.where(Signal.signal_type.in_(self.types))
        if self.scope is not None:
            stmt = stmt.where(Signal.scope == self.scope)
        if self.stance is not None:
            stmt = stmt.where(Signal.stance == self.stance)
        if self.capabilities:
            stmt = stmt.where(
                Signal.id.in_(
                    _linked(SignalCapability, "capability_id", Capability, self.capabilities)
                )
            )
        if self.kits:
            stmt = stmt.where(Signal.id.in_(_linked(SignalKit, "kit_id", Kit, self.kits)))
        if self.min_importance is not None:
            stmt = stmt.where(Signal.importance >= self.min_importance)
        if self.since is not None:
            stmt = stmt.where(Signal.last_updated_at >= self.since)
        return stmt


# ---- cards -------------------------------------------------------------------------------


def _card(
    sig: Signal,
    competitors: list[str],
    primary: tuple[str, str] | None,
    analysis: CardAnalysis,
) -> SignalCard:
    grade = (
        f"{sig.reliability}{sig.credibility}"
        if sig.reliability and sig.credibility is not None
        else None
    )
    return SignalCard(
        id=sig.id,
        title=sig.title,
        scope=sig.scope,
        status=sig.status,
        signal_type=sig.signal_type,
        stance=sig.stance,
        importance=sig.importance,
        reliability=sig.reliability,
        credibility=sig.credibility,
        grade=grade,
        competitors=competitors,
        fact=sig.fact,
        impact=sig.impact,
        role_actions=RoleActions.model_validate(sig.role_actions or {}),
        first_seen_at=sig.first_seen_at,
        primary_url=primary[0] if primary else None,
        primary_source=primary[1] if primary else None,
        summary=analysis.summary,
        analysis_state=analysis.state,
    )


async def signal_cards(session: AsyncSession, signals: Sequence[Signal]) -> list[SignalCard]:
    """Cards for `signals`, in the given order (competitor slugs + primary source attached)."""
    ids = [s.id for s in signals]
    if not ids:
        return []
    slugs: dict[int, list[str]] = {}
    for signal_id, slug in await session.execute(
        select(SignalCompetitor.signal_id, Competitor.slug)
        .join(Competitor, Competitor.id == SignalCompetitor.competitor_id)
        .where(SignalCompetitor.signal_id.in_(ids), Competitor.is_self.is_(False))
        .order_by(Competitor.slug)
    ):
        slugs.setdefault(signal_id, []).append(slug)
    # Link to the item's original URL; canonical_url is a dedup key (as /documents does).
    primary = {
        signal_id: (raw_url or canonical_url, source_label(kind, name, payload))
        for signal_id, raw_url, canonical_url, kind, name, payload in await session.execute(
            select(
                SignalDocument.signal_id,
                RawItem.url,
                Document.canonical_url,
                Source.kind,
                Source.name,
                RawItem.payload,
            )
            .join(Document, Document.id == SignalDocument.document_id)
            .join(RawItem, RawItem.id == Document.raw_item_id)
            .join(Source, Source.id == Document.source_id)
            .where(SignalDocument.signal_id.in_(ids))
            .ext(distinct_on(SignalDocument.signal_id))
            .order_by(
                SignalDocument.signal_id,
                SignalDocument.is_primary.desc(),
                SignalDocument.document_id,
            )
        )
    }
    analyses = await card_analyses(session, signals)
    return [_card(s, slugs.get(s.id, []), primary.get(s.id), analyses[s.id]) for s in signals]


# ---- search ------------------------------------------------------------------------------


async def _embed_query(
    llm: AccountedLLM | None, q: str
) -> tuple[list[float] | None, Literal["hybrid", "keyword"]]:
    """The query vector, or None (keyword-only) without a key or when embedding fails."""
    if llm is None:
        return None, "keyword"
    try:
        [vector] = await llm.embed([q], ctx=SEARCH_CTX, task="search_embed")
    except (LLMError, BudgetExceeded) as exc:
        logger.warning("signal search: no query embedding (%s); keyword-only", type(exc).__name__)
        return None, "keyword"
    return vector, "hybrid"


async def _search(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM | None,
    q: str,
    facets: SignalFacets,
    limit: int,
) -> SignalPage:
    vector, mode = await _embed_query(llm, q)
    async with session_factory() as session:
        # narrow in SQL first, so the pool cap and the vector floor see only the
        # signals this reader asked for and may see.
        allowed = None
        if facets.narrows:
            allowed = list(await session.scalars(facets.apply(select(Signal.id))))
            if not allowed:
                return SignalPage(items=[], next_cursor=None, search_mode=mode)
        hits = await hybrid_search(
            session,
            query=q,
            query_embedding=vector,
            kinds=[RetrievalKind.signal],
            k=Q_POOL,
            candidates=Q_POOL,
            vector_floor=True,  # no unrelated vector-only tail in the Explorer
            ref_ids=allowed,
            signal_statuses=facets.statuses,
        )
        order = list(dict.fromkeys(h.ref_id for h in hits))
        found = {
            s.id: s
            for s in await session.scalars(facets.apply(select(Signal).where(Signal.id.in_(order))))
        }
        top = [found[i] for i in order if i in found][: min(limit, Q_TOP)]
        items = await signal_cards(session, top)
    return SignalPage(items=items, next_cursor=None, search_mode=mode)


# ---- routes ------------------------------------------------------------------------------


@router.get("", response_model=SignalPage)
async def list_signals(
    user: Annotated[User, Depends(search_rate_limit)],
    session: Annotated[AsyncSession, Depends(get_session)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    llm: Annotated[AccountedLLM | None, Depends(get_optional_llm)],
    competitor: Annotated[
        list[str] | None, Query(description="Competitor slug; repeatable (any of).")
    ] = None,
    signal_type: Annotated[
        list[SignalType] | None, Query(alias="type", description="Repeatable (any of).")
    ] = None,
    scope: Scope | None = None,
    signal_status: Annotated[
        list[SignalStatus] | None,
        Query(
            alias="status",
            description="Repeatable. Non-analysts only ever get auto|approved.",
        ),
    ] = None,
    stance: Stance | None = None,
    capability: Annotated[
        list[str] | None, Query(description="Capability slug; repeatable (any of).")
    ] = None,
    kit: Annotated[list[str] | None, Query(description="KIT slug; repeatable (any of).")] = None,
    min_importance: Annotated[int | None, Query(ge=0, le=100)] = None,
    since: Annotated[
        datetime | None, Query(description="Only signals updated at or after this time.")
    ] = None,
    q: Annotated[
        str | None,
        Query(
            min_length=2,
            max_length=300,
            description="Hybrid search (keyword-only without an OpenAI key): top 50, no paging.",
        ),
    ] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SignalPage:
    facets = SignalFacets(
        statuses=visible_statuses(user, signal_status),
        competitors=competitor,
        types=[str(t) for t in signal_type] if signal_type else None,
        scope=str(scope) if scope else None,
        stance=str(stance) if stance else None,
        capabilities=capability,
        kits=kit,
        min_importance=min_importance,
        since=since,
    )
    if q is not None:
        # end the auth transaction so no connection idles in it across the embed await.
        await session.rollback()
        return await _search(session_factory, llm, q, facets, limit)

    stmt = facets.apply(select(Signal))
    if cursor:
        importance, signal_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Signal.importance, Signal.id) < tuple_(importance, signal_id))
    rows = list(
        await session.scalars(
            stmt.order_by(Signal.importance.desc(), Signal.id.desc()).limit(limit + 1)
        )
    )
    page, extra = rows[:limit], rows[limit:]
    last = page[-1] if extra else None
    return SignalPage(
        items=await signal_cards(session, page),
        next_cursor=encode_cursor(last.importance, last.id) if last else None,
        search_mode=None,
    )


@router.get("/{signal_id}", response_model=SignalDetail)
async def get_signal(
    signal_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SignalDetail:
    sig = await session.get(Signal, signal_id)
    if (
        sig is None
        or sig.scope in HIDDEN_SCOPES
        or not (is_analyst(user) or sig.status in PUBLIC_STATUSES)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signal not found")
    [card] = await signal_cards(session, [sig])

    evidence = [
        EvidenceOut(
            quote=e.quote,
            verified=e.verified,
            document_id=e.document_id,
            url=raw_url or canonical_url,
            source_name=source_label(kind, name, payload),
        )
        for e, raw_url, canonical_url, kind, name, payload in await session.execute(
            select(
                Evidence,
                RawItem.url,
                Document.canonical_url,
                Source.kind,
                Source.name,
                RawItem.payload,
            )
            .join(Document, Document.id == Evidence.document_id)
            .join(RawItem, RawItem.id == Document.raw_item_id)
            .join(Source, Source.id == Document.source_id)
            .where(Evidence.signal_id == signal_id)
            .order_by(Evidence.verified.desc(), Evidence.id)
        )
    ]
    documents = [
        SignalDocumentOut(
            id=d.id,
            title=d.title,
            url=raw_url or d.canonical_url,
            source_name=source_label(kind, name, payload),
            published_at=d.published_at,
            is_primary=is_primary,
        )
        for d, is_primary, raw_url, kind, name, payload in await session.execute(
            select(
                Document,
                SignalDocument.is_primary,
                RawItem.url,
                Source.kind,
                Source.name,
                RawItem.payload,
            )
            .join(SignalDocument, SignalDocument.document_id == Document.id)
            .join(RawItem, RawItem.id == Document.raw_item_id)
            .join(Source, Source.id == Document.source_id)
            .where(SignalDocument.signal_id == signal_id)
            .order_by(
                SignalDocument.is_primary.desc(),
                Document.published_at.desc().nulls_last(),
                Document.id,
            )
        )
    ]
    capabilities = [
        CapabilityRef(slug=slug, name=name)
        for slug, name in await session.execute(
            select(Capability.slug, Capability.name)
            .join(SignalCapability, SignalCapability.capability_id == Capability.id)
            .where(SignalCapability.signal_id == signal_id)
            .order_by(Capability.name)
        )
    ]
    kits = [
        KitRef(slug=slug, title=title)
        for slug, title in await session.execute(
            select(Kit.slug, Kit.title)
            .join(SignalKit, SignalKit.kit_id == Kit.id)
            .where(SignalKit.signal_id == signal_id)
            .order_by(Kit.priority, Kit.slug)
        )
    ]

    related: list[SignalCard] = []
    if sig.centroid is not None:
        # Nearest stored centroids (pgvector cosine), as get_similar_signals; no LLM call.
        target = select(Signal.centroid).where(Signal.id == signal_id).scalar_subquery()
        similar = await session.scalars(
            select(Signal)
            .where(
                Signal.id != signal_id,
                Signal.centroid.is_not(None),
                visible_signal_clause(user),
            )
            .order_by(Signal.centroid.cosine_distance(target), Signal.id)
            .limit(RELATED_K)
        )
        related = await signal_cards(session, list(similar))

    content = (await card_analyses(session, [sig]))[signal_id].content
    sources, analysis = await build_sources_and_analysis(session, user, documents, content)

    return SignalDetail(
        **card.model_dump(),
        analysis=analysis,
        sources=sources,
        score_breakdown=sig.score_breakdown or {},
        evidence=evidence,
        documents=documents,
        capabilities=capabilities,
        kits=kits,
        related=related,
    )
