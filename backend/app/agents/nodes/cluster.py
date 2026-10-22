"""Story clustering: relevant documents join an existing signal or open a new pending one.

Reads `documents.embedding` (written earlier by indexing); never embeds. Vector ranking is
done in SQL (`<=>`); centroids are plain-Python means (numpy is not installed). Sessions are
short: the candidate lookup and the write each get their own, and none is open across
the story-judge await.
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, exists, func, not_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt, untrusted
from app.models import Competitor, Document, DocumentCompetitor, RawItem, Source
from app.models.enums import DocStatus, Scope, SignalStatus, StepKind
from app.models.gold import Signal, SignalCompetitor, SignalDocument

logger = logging.getLogger(__name__)

TASK = "story_judge"
JOIN_COSINE = 0.85
JUDGE_COSINE = 0.78
EPSILON = 1e-9  # thresholds are inclusive; float error in 1 - distance must not flip them
CLUSTER_WINDOW_HOURS = 72
LEAD_WORDS = 80


class StoryJudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    same_event: bool
    reason: str


@dataclass
class ClusterResult:
    new_signal_ids: list[int] = field(default_factory=list)
    grown_signal_ids: list[int] = field(default_factory=list)
    reanalyze_signal_ids: list[int] = field(default_factory=list)


@dataclass
class _Candidate:
    signal_id: int
    cosine: float


@dataclass
class _Lookup:
    when: datetime
    candidate: _Candidate | None
    doc_view: str | None = None
    signal_view: str | None = None


def _lead(text: str | None) -> str:
    return " ".join((text or "").split()[:LEAD_WORDS])


def _view(doc_id: int, title: str | None, text: str | None) -> str:
    return untrusted(doc_id, f"Title: {title or '(untitled)'}\n\n{_lead(text)}")


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


# Aggregators keep their own link (no per-item resolve); the payload names the outlet.
AGGREGATOR_HOSTS = frozenset({"news.google.com"})


def _outlet_host(url: str, payload: dict | None) -> str:
    """The host that published the story: the outlet behind an aggregator link."""
    host = _host(url)
    if host in AGGREGATOR_HOSTS:
        return _host((payload or {}).get("outlet_url") or "") or host
    return host


def _when(doc: Document) -> datetime:
    return doc.published_at or doc.created_at


async def _doc_competitors(session: AsyncSession, doc_id: int) -> set[int]:
    """The document's companies, never JFrog itself: a legacy self link is neither
    copied onto a signal nor used to pick the signal to join."""
    stmt = (
        select(DocumentCompetitor.competitor_id)
        .join(Competitor, Competitor.id == DocumentCompetitor.competitor_id)
        .where(DocumentCompetitor.document_id == doc_id, ~Competitor.is_self)
    )
    return set((await session.execute(stmt)).scalars().all())


async def _lookup(session_factory: async_sessionmaker[AsyncSession], doc_id: int) -> _Lookup | None:
    """One short session: eligibility, best candidate + cosine, and judge views if borderline."""
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        if doc is None:
            logger.warning("cluster: document %s not found", doc_id)
            return None
        if doc.status != DocStatus.relevant:
            logger.warning("cluster: document %s is %s, not relevant; skipped", doc_id, doc.status)
            return None
        if (doc.triage or {}).get("scope") == Scope.jfrog:
            # triaged earlier as news about JFrog itself; it never opens or joins a
            # signal (migration 0006 marks such waiting documents irrelevant).
            logger.warning("cluster: document %s has legacy scope jfrog; skipped", doc_id)
            return None
        if doc.embedding is None:
            logger.warning("cluster: document %s has no embedding; skipped", doc_id)
            return None
        if doc.story_id is not None:
            return None  # already clustered: re-runs are idempotent

        when = _when(doc)
        competitors = await _doc_competitors(session, doc_id)
        scope = (doc.triage or {}).get("scope")
        window = timedelta(hours=CLUSTER_WINDOW_HOURS)
        has_comp = exists().where(SignalCompetitor.signal_id == Signal.id)
        if competitors:
            shares = exists().where(
                SignalCompetitor.signal_id == Signal.id,
                SignalCompetitor.competitor_id.in_(competitors),
            )
        elif scope is not None:
            # competitor-less docs match competitor-less signals of the same scope.
            shares = and_(Signal.scope == scope, not_(has_comp))
        else:
            return _Lookup(when, None)

        distance = Signal.centroid.cosine_distance(doc.embedding)
        stmt = (
            select(Signal.id, distance)
            .where(
                Signal.centroid.is_not(None),
                Signal.first_seen_at.between(when - window, when + window),
                shares,
            )
            .order_by(distance, Signal.id)
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return _Lookup(when, None)
        cand = _Candidate(row[0], 1.0 - float(row[1]))
        out = _Lookup(when, cand)
        if JUDGE_COSINE <= cand.cosine + EPSILON and cand.cosine + EPSILON < JOIN_COSINE:
            primary = (
                await session.execute(
                    select(Document)
                    .join(SignalDocument, SignalDocument.document_id == Document.id)
                    .where(SignalDocument.signal_id == cand.signal_id, SignalDocument.is_primary)
                )
            ).scalar_one_or_none()
            signal = await session.get(Signal, cand.signal_id)
            if primary is not None:
                out.signal_view = _view(
                    primary.id, primary.title, primary.clean_text or primary.summary
                )
            else:
                out.signal_view = untrusted(f"signal-{cand.signal_id}", f"Title: {signal.title}")
            out.doc_view = _view(doc.id, doc.title, doc.clean_text or doc.summary)
        return out


async def _same_event(llm: AccountedLLM, lookup: _Lookup, ctx: RunCtx, doc_id: int) -> bool:
    """Ask the fast story judge; a refusal or error counts as 'not the same event'."""
    judge_ctx = replace(ctx, agent=TASK)
    try:
        out = await llm.structured(
            TASK,
            "fast",
            StoryJudgeOut,
            prompt=load_prompt(TASK),
            variables={"existing": lookup.signal_view, "new": lookup.doc_view},
            ctx=judge_ctx,
        )
    except (LLMRefusal, LLMError) as exc:
        logger.warning("story judge failed for document %s: %s", doc_id, exc)
        await llm.log_step(
            judge_ctx, kind=StepKind.node, args={"doc_id": doc_id}, result_summary=f"error: {exc}"
        )
        return False
    return out.same_event


async def _refresh_signal(session: AsyncSession, signal: Signal) -> None:
    """Recompute centroid, corroboration count and the single primary row from members."""
    found = (
        await session.execute(
            select(Document, Source.reliability, RawItem.payload)
            .join(SignalDocument, SignalDocument.document_id == Document.id)
            .join(Source, Source.id == Document.source_id)
            .outerjoin(RawItem, RawItem.id == Document.raw_item_id)
            .where(SignalDocument.signal_id == signal.id)
        )
    ).all()
    rows = [(d, rel) for d, rel, _ in found]
    vectors = [[float(x) for x in d.embedding] for d, _ in rows if d.embedding is not None]
    if vectors:
        signal.centroid = [sum(col) / len(vectors) for col in zip(*vectors, strict=True)]
    hosts = {h for d, _, payload in found if (h := _outlet_host(d.canonical_url, payload))}
    signal.corroboration_count = max(1, len(hosts))

    def rank(item: tuple[Document, str]) -> tuple:
        d, rel = item
        return (rel, d.published_at is None, d.published_at or d.created_at, d.id)

    best = min(rows, key=rank)[0]
    await session.execute(
        SignalDocument.__table__.update()
        .where(SignalDocument.signal_id == signal.id)
        .values(is_primary=SignalDocument.document_id == best.id)
    )


async def _apply(
    session_factory: async_sessionmaker[AsyncSession],
    doc_id: int,
    candidate_id: int | None,
    result: ClusterResult,
) -> None:
    """One short session: join `candidate_id` or create a new signal, then commit."""
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        if doc is None or doc.story_id is not None:
            return
        source = await session.get(Source, doc.source_id)
        reliability = source.reliability if source else None
        competitors = await _doc_competitors(session, doc_id)

        if candidate_id is None:
            signal = Signal(
                title=doc.title or "(untitled)",
                scope=(doc.triage or {}).get("scope", Scope.industry),
                first_seen_at=_when(doc),
                status=SignalStatus.pending,
                reliability=reliability,
                centroid=doc.embedding,
            )
            session.add(signal)
            await session.flush()
            result.new_signal_ids.append(signal.id)
        else:
            signal = await session.get(Signal, candidate_id)
            if signal.id not in result.grown_signal_ids:
                result.grown_signal_ids.append(signal.id)
            # A/B/.../F: a smaller letter is a better source.
            if reliability and (signal.reliability is None or reliability < signal.reliability):
                if signal.reliability is not None and signal.id not in result.reanalyze_signal_ids:
                    result.reanalyze_signal_ids.append(signal.id)
                signal.reliability = reliability
            signal.last_updated_at = func.now()

        if competitors:
            await session.execute(
                insert(SignalCompetitor)
                .values([{"signal_id": signal.id, "competitor_id": c} for c in sorted(competitors)])
                .on_conflict_do_nothing()
            )
        session.add(SignalDocument(signal_id=signal.id, document_id=doc_id, is_primary=False))
        doc.story_id = signal.id
        await session.flush()
        await _refresh_signal(session, signal)
        await session.commit()


async def _ordered(
    session_factory: async_sessionmaker[AsyncSession], doc_ids: list[int]
) -> list[int]:
    async with session_factory() as session:
        stmt = (
            select(Document.id)
            .where(Document.id.in_(doc_ids))
            .order_by(
                Document.published_at.is_(None),
                func.coalesce(Document.published_at, Document.created_at),
                Document.id,
            )
        )
        return list((await session.execute(stmt)).scalars().all())


async def cluster_documents(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    doc_ids: list[int],
    *,
    ctx: RunCtx,
) -> ClusterResult:
    result = ClusterResult()
    for doc_id in await _ordered(session_factory, list(dict.fromkeys(doc_ids))):
        lookup = await _lookup(session_factory, doc_id)
        if lookup is None:
            continue
        join_id: int | None = None
        cand = lookup.candidate
        if cand is not None:
            if cand.cosine + EPSILON >= JOIN_COSINE:
                join_id = cand.signal_id
            elif cand.cosine + EPSILON >= JUDGE_COSINE and await _same_event(
                llm, lookup, ctx, doc_id
            ):
                join_id = cand.signal_id
        await _apply(session_factory, doc_id, join_id, result)
    return result
