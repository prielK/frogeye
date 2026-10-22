from datetime import UTC, datetime

from sqlalchemy import func, select

from app.agents.analysis_schema import CitedText, SignalAnalysisContent
from app.agents.analysis_store import persist_analysis
from app.agents.runtime.context import RunCtx
from app.models import Document, DocumentCompetitor, JfrogKbDoc, RetrievalItem, Signal
from app.models.agents import LlmCall
from app.models.gold import SignalAnalysis, SignalCompetitor
from app.rag.indexing import index_documents, index_kb_doc, index_signal

CTX = RunCtx(agent_run_id=None, node="index")


def _words(n: int) -> str:
    return " ".join(f"word{i % 97}." if i % 12 == 11 else f"word{i % 97}" for i in range(n))


async def _items(session, kind="article_chunk", ref_id=None):
    q = (
        select(RetrievalItem)
        .where(RetrievalItem.kind == kind)
        .order_by(RetrievalItem.ref_id, RetrievalItem.chunk_no)
    )
    if ref_id is not None:
        q = q.where(RetrievalItem.ref_id == ref_id)
    return list(await session.scalars(q))


async def test_index_documents_writes_chunks_and_embedding(
    session, session_factory, make_document, make_source, competitor_factory, accounted_llm
):
    comp = competitor_factory()
    session.add(comp)
    await session.flush()
    source = await make_source(reliability="A")
    doc = await make_document(
        source_id=source.id,
        title="Big release",
        clean_text=_words(3000),
        scope_hint="competitor",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        status="relevant",
    )
    session.add(DocumentCompetitor(document_id=doc.id, competitor_id=comp.id, salience=1.0))
    await session.flush()

    n = await index_documents(session_factory, accounted_llm(), [doc.id], ctx=CTX)

    items = await _items(session, ref_id=doc.id)
    assert n == len(items) >= 2
    assert [i.chunk_no for i in items] == list(range(len(items)))
    assert all(i.embedding is not None for i in items)
    assert all(i.competitor_ids == [comp.id] for i in items)
    assert items[0].scope == "competitor"
    assert items[0].reliability == "A"
    assert items[0].published_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert items[0].text.startswith("Big release")
    await session.refresh(doc)
    assert doc.embedding is not None


async def test_published_at_falls_back_to_created_at(
    session, session_factory, make_document, accounted_llm
):
    doc = await make_document(title="t", clean_text="body text.", published_at=None)
    await index_documents(session_factory, accounted_llm(), [doc.id], ctx=CTX)
    (item,) = await _items(session, ref_id=doc.id)
    assert item.published_at == doc.created_at


async def test_reindex_replaces_chunks(session, session_factory, make_document, accounted_llm):
    doc = await make_document(title="t", clean_text=_words(3000))
    llm = accounted_llm()
    await index_documents(session_factory, llm, [doc.id], ctx=CTX)
    before = len(await _items(session, ref_id=doc.id))
    assert before >= 3

    doc.clean_text = "now short."
    await session.flush()
    n = await index_documents(session_factory, llm, [doc.id], ctx=CTX)
    items = await _items(session, ref_id=doc.id)
    assert n == 1 and len(items) == 1
    assert "short" in items[0].text


async def test_index_skips_irrelevant_and_duplicates(
    session, session_factory, make_document, accounted_llm
):
    ok = await make_document(title="ok", clean_text="fine.", status="relevant")
    irr = await make_document(title="irr", clean_text="x.", status="irrelevant")
    dup = await make_document(title="dup", clean_text="y.", status="duplicate")
    n = await index_documents(session_factory, accounted_llm(), [ok.id, irr.id, dup.id], ctx=CTX)
    assert n == 1
    assert [i.ref_id for i in await _items(session)] == [ok.id]
    await session.refresh(irr)
    assert irr.embedding is None


async def test_embed_calls_batched_and_accounted(
    session, session_factory, make_document, accounted_llm
):
    docs = [await make_document(title=f"d{i}", clean_text=f"body {i}.") for i in range(125)]
    llm = accounted_llm()
    n = await index_documents(session_factory, llm, [d.id for d in docs], ctx=CTX)
    assert n == 125  # 125 chunks + 125 document embeddings = 250 texts
    embeds = [r for r in llm.backend.requests if r["kind"] == "embed"]
    assert [len(r["texts"]) for r in embeds] == [100, 100, 50]
    assert await session.scalar(select(func.count()).select_from(LlmCall)) == 3


async def test_documents_embedded_before_a_failure_are_kept(
    session, session_factory, make_document, accounted_llm
):
    """Vectors are written batch by batch, so a budget stop or an embedding error
    at batch N keeps (and never pays again for) the documents of batches 1..N-1."""
    import pytest

    from app.agents.runtime.accounted import LLMError
    from tests.agents.fakes import fake_embedding

    docs = [await make_document(title=f"d{i}", clean_text=f"body {i}.") for i in range(125)]
    calls = []

    def embed(*, texts, dims, **_):
        calls.append(len(texts))
        if len(calls) == 2:
            raise RuntimeError("embeddings down")
        return [fake_embedding(t, dims) for t in texts]

    llm = accounted_llm({"embed": embed})
    with pytest.raises(LLMError):
        await index_documents(session_factory, llm, [d.id for d in docs], ctx=CTX)
    embedded = await session.scalar(
        select(func.count()).select_from(Document).where(Document.embedding.is_not(None))
    )
    assert calls == [100, 100] and embedded == 50  # the first batch's 50 documents
    assert len(await _items(session)) == 50


async def test_index_signal_and_reindex(
    session, session_factory, competitor_factory, accounted_llm
):
    comp = competitor_factory()
    session.add(comp)
    await session.flush()
    sig = Signal(
        title="Acme ships X",
        scope="competitor",
        fact="Acme shipped X.",
        impact="Hurts us.",
        status="approved",
        reliability="B",
        first_seen_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    session.add(sig)
    await session.flush()
    session.add(SignalCompetitor(signal_id=sig.id, competitor_id=comp.id))
    await session.flush()

    llm = accounted_llm()
    await index_signal(session_factory, llm, sig.id, ctx=CTX)
    sig.status = "pending"
    await session.flush()
    await index_signal(session_factory, llm, sig.id, ctx=CTX)

    (item,) = await _items(session, "signal", sig.id)
    assert item.chunk_no == 0
    assert item.text == "Acme ships X\nAcme shipped X.\nHurts us."
    assert item.competitor_ids == [comp.id]
    assert item.published_at == datetime(2026, 10, 2, tzinfo=UTC)
    assert item.approved is False
    assert item.embedding is not None


async def test_index_signal_includes_latest_analysis_summary(
    session, session_factory, accounted_llm, make_signal
):
    sig = await make_signal(title="Acme ships X", fact="Acme shipped X.", impact="Hurts us.")
    for text in ("Old summary.", "Acme's X undercuts Xray on price."):
        content = SignalAnalysisContent(
            summary=CitedText(text=text, cites=["doc:1"]),
            what_happened=[CitedText(text="It shipped.", cites=["doc:1"])],
        )
        await persist_analysis(
            session, sig.id, content, model="m", prompt_version="1", cost_usd=0.0
        )
    other = await make_signal(title="Beta moves", fact="Beta moved.", impact=None)
    # a row a newer prompt wrote in a shape this reader cannot parse: no summary, no error
    session.add(
        SignalAnalysis(
            signal_id=other.id,
            version=1,
            content={"summary": {"text": ["not", "text"]}},
            model="m",
            prompt_version="9",
        )
    )
    await session.flush()

    llm = accounted_llm()
    await index_signal(session_factory, llm, sig.id, ctx=CTX)
    await index_signal(session_factory, llm, other.id, ctx=CTX)

    (item,) = await _items(session, "signal", sig.id)
    assert (
        item.text == "Acme ships X\nAcme shipped X.\nHurts us.\nAcme's X undercuts Xray on price."
    )
    (item,) = await _items(session, "signal", other.id)
    assert item.text == "Beta moves\nBeta moved."


async def test_index_kb_doc_chunks_and_reindex(session, session_factory, accounted_llm):
    kb = JfrogKbDoc(url="https://jfrog.com/x", title="Xray", text=_words(3000), content_hash="h")
    session.add(kb)
    await session.flush()
    llm = accounted_llm()
    n = await index_kb_doc(session_factory, llm, kb.id, ctx=CTX)
    assert n >= 2
    items = await _items(session, "jfrog_kb", kb.id)
    assert len(items) == n and items[0].scope == "jfrog"

    kb.text = "short."
    await session.flush()
    assert await index_kb_doc(session_factory, llm, kb.id, ctx=CTX) == 1
    assert len(await _items(session, "jfrog_kb", kb.id)) == 1


async def test_caller_pending_work_is_not_committed(
    session, session_factory, make_document, accounted_llm
):
    doc = await make_document(title="pending", clean_text="body text.", status="relevant")
    await index_documents(session_factory, accounted_llm(), [doc.id], ctx=CTX)

    # the caller's transaction is still open and still owns its pending document
    assert session.in_transaction()
    await session.rollback()
    async with session_factory() as other:
        assert await other.get(Document, doc.id) is None
