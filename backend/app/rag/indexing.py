"""RAG indexing: chunk, embed (through AccountedLLM) and upsert `retrieval_items`."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.analysis_store import stored_analyses
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.models import Document, DocumentCompetitor, Source
from app.models.enums import DocStatus, RetrievalKind
from app.models.gold import Signal, SignalCompetitor
from app.models.rag import JfrogKbDoc, RetrievalItem
from app.rag.chunking import chunk_text

EMBED_BATCH = 100
DOC_EMBED_CHARS = 1500
SKIPPED_STATUSES = (DocStatus.duplicate, DocStatus.irrelevant)
_UPDATED = (
    "text",
    "embedding",
    "competitor_ids",
    "scope",
    "published_at",
    "reliability",
    "approved",
)


async def embed_batched(
    llm: AccountedLLM, texts: Sequence[str], *, ctx: RunCtx, task: str = "embed"
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        vectors.extend(await llm.embed(list(texts[i : i + EMBED_BATCH]), ctx=ctx, task=task))
    return vectors


async def _upsert_items(
    session: AsyncSession, kind: str, ref_id: int, rows: list[dict[str, Any]]
) -> None:
    """Upsert chunks 0..n-1 of (kind, ref_id) and drop stale higher chunk numbers."""
    if rows:
        stmt = insert(RetrievalItem).values(
            [{"kind": kind, "ref_id": ref_id, "chunk_no": i, **row} for i, row in enumerate(rows)]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["kind", "ref_id", "chunk_no"],
                set_={c: stmt.excluded[c] for c in _UPDATED},
            )
        )
    await session.execute(
        delete(RetrievalItem).where(
            RetrievalItem.kind == kind,
            RetrievalItem.ref_id == ref_id,
            RetrievalItem.chunk_no >= len(rows),
        )
    )


async def index_documents(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    doc_ids: list[int],
    *,
    ctx: RunCtx,
) -> int:
    """Embed documents and (re)write their article chunks. Returns retrieval items written.

    Reads in one short session, then embeds and writes batch by batch (about EMBED_BATCH
    texts each): no session is open during an embed call, and a failure at batch N (an LLM
    error, a budget stop) keeps the documents of batches 1..N-1, already paid for,
    before it propagates. Never touches a session the caller owns."""
    if not doc_ids:
        return 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Document, Source.reliability)
                .join(Source, Source.id == Document.source_id)
                .where(Document.id.in_(doc_ids), Document.status.not_in(SKIPPED_STATUSES))
                .order_by(Document.id)
            )
        ).all()
        competitors: dict[int, list[int]] = {}
        for doc_id, comp_id in await session.execute(
            select(DocumentCompetitor.document_id, DocumentCompetitor.competitor_id)
            .where(DocumentCompetitor.document_id.in_([d.id for d, _ in rows]))
            .order_by(DocumentCompetitor.competitor_id)
        ):
            competitors.setdefault(doc_id, []).append(comp_id)

        plans = []  # (doc_id, metadata, text embedded for the document, chunks)
        for doc, reliability in rows:
            body = doc.clean_text or doc.summary or ""
            plans.append(
                (
                    doc.id,
                    {
                        "competitor_ids": competitors.get(doc.id, []),
                        "scope": doc.scope_hint,
                        "published_at": doc.published_at or doc.created_at,
                        "reliability": reliability,
                        "approved": False,
                    },
                    f"{doc.title}\n{(doc.summary or body)[:DOC_EMBED_CHARS]}",
                    chunk_text(f"{doc.title}\n\n{body}"),
                )
            )

    written = 0
    for group in _batches(plans):
        texts = [t for _, _, doc_text, chunks in group for t in (doc_text, *chunks)]
        vectors = iter(await embed_batched(llm, texts, ctx=ctx))
        async with session_factory() as session:
            for doc_id, meta, _, chunks in group:
                doc_vector = next(vectors)
                items = [{"text": c, "embedding": next(vectors), **meta} for c in chunks]
                await session.execute(
                    update(Document).where(Document.id == doc_id).values(embedding=doc_vector)
                )
                await _upsert_items(session, RetrievalKind.article_chunk, doc_id, items)
                written += len(items)
            await session.commit()
    return written


def _batches[T: tuple](plans: list[T]) -> list[list[T]]:
    """Consecutive groups of document plans of at most EMBED_BATCH texts each (a document
    with more chunks than that makes a group of its own)."""
    groups: list[list[T]] = []
    size = 0
    for plan in plans:
        n = 1 + len(plan[3])  # the document text + its chunks
        if not groups or size + n > EMBED_BATCH:
            groups.append([])
            size = 0
        groups[-1].append(plan)
        size += n
    return groups


async def index_signal(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    signal_id: int,
    *,
    ctx: RunCtx,
) -> None:
    """(Re)write the signal's one retrieval item: title + fact + impact + the latest stage-B
    analysis summary, so search finds what readers see. Only an analysis readers are shown
    adds its summary (`stored_analyses`; current or stale, never an unparseable or
    summary-only row)."""
    async with session_factory() as session:
        signal = await session.get(Signal, signal_id)
        if signal is None:
            return
        comp_ids = list(
            await session.scalars(
                select(SignalCompetitor.competitor_id)
                .where(SignalCompetitor.signal_id == signal_id)
                .order_by(SignalCompetitor.competitor_id)
            )
        )
        stored = (await stored_analyses(session, {signal_id: signal.analyzed_at})).get(signal_id)
        summary = stored.content.summary.text if stored else None
        parts = (signal.title, signal.fact, signal.impact, summary)
        text = "\n".join(p for p in parts if p)
        meta = {
            "competitor_ids": comp_ids,
            "scope": signal.scope,
            "published_at": signal.first_seen_at,
            "reliability": signal.reliability,
            "approved": signal.status == "approved",
        }
    (vector,) = await embed_batched(llm, [text], ctx=ctx)
    async with session_factory() as session:
        await _upsert_items(
            session, RetrievalKind.signal, signal_id, [{"text": text, "embedding": vector, **meta}]
        )
        await session.commit()


async def index_kb_doc(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    kb_doc_id: int,
    *,
    ctx: RunCtx,
) -> int:
    async with session_factory() as session:
        kb = await session.get(JfrogKbDoc, kb_doc_id)
        if kb is None:
            return 0
        chunks = chunk_text(f"{kb.title}\n\n{kb.text}")
        meta = {
            "competitor_ids": [],
            "scope": "jfrog",
            "published_at": kb.fetched_at,
            "reliability": None,
            "approved": False,
        }
    vectors = await embed_batched(llm, chunks, ctx=ctx)
    items = [{"text": c, "embedding": v, **meta} for c, v in zip(chunks, vectors, strict=True)]
    async with session_factory() as session:
        await _upsert_items(session, RetrievalKind.jfrog_kb, kb_doc_id, items)
        await session.commit()
    return len(items)
