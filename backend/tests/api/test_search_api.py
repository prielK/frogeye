from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.agents.runtime.context import RunCtx
from app.api import deps
from app.api.deps import get_optional_llm, get_session_factory
from app.api.v1 import search as search_api
from app.core.config import get_settings
from app.main import app
from app.models import Competitor, DocumentCompetitor, JfrogKbDoc, Signal
from app.rag.indexing import index_documents, index_kb_doc, index_signal

CTX = RunCtx(agent_run_id=None, node="index")
EMPTY_PLAN = {
    "rewritten": "nexus",
    "variants": [],
    "competitor_slugs": [],
    "date_from": None,
    "date_to": None,
    "kinds": [],
}


@pytest.fixture(autouse=True)
def _reset_search_state():
    search_api.limiter.reset()
    yield
    search_api.limiter.reset()
    app.dependency_overrides.pop(get_optional_llm, None)
    app.dependency_overrides.pop(get_session_factory, None)


@pytest.fixture
def wire(session_factory, accounted_llm):
    def _wire(script=None, **kw):
        llm = accounted_llm(script, **kw)
        app.dependency_overrides[get_optional_llm] = lambda: llm
        app.dependency_overrides[get_session_factory] = lambda: session_factory
        return llm

    return _wire


async def test_search_requires_auth(client, seeded):
    r = await client.get("/api/v1/search", params={"q": "nexus"})
    assert r.status_code == 401


async def _nexus_corpus(session, session_factory, make_document, accounted_llm):
    """A relevant article and an auto signal that both say "Nexus", indexed."""
    doc = await make_document(
        title="Nexus 3.97 release notes",
        clean_text="The Nexus 3.97 release adds a firewall. " * 4,
        published_at=datetime(2026, 10, 2, tzinfo=UTC),
        status="relevant",
    )
    sig = Signal(title="Nexus 3.97 ships", scope="competitor", fact="Nexus fact", status="auto")
    session.add(sig)
    await session.flush()
    llm = accounted_llm()
    await index_documents(session_factory, llm, [doc.id], ctx=CTX)
    await index_signal(session_factory, llm, sig.id, ctx=CTX)
    return doc, sig


async def test_search_without_key_is_keyword_only(
    auth_client, seeded, session, session_factory, make_document, accounted_llm, monkeypatch
):
    """No OpenAI key -> keyword-only results, labelled, never a 503."""
    doc, sig = await _nexus_corpus(session, session_factory, make_document, accounted_llm)
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    monkeypatch.setattr(deps, "_llm_cache", None)
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    r = await (await auth_client("pm")).get("/api/v1/search", params={"q": "nexus"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["search_mode"] == "keyword" and body["plan"] is None
    assert {(h["kind"], h["ref_id"]) for h in body["hits"]} == {
        ("article_chunk", doc.id),
        ("signal", sig.id),
    }


@pytest.mark.parametrize("failure", ["budget", "llm_error"])
async def test_search_falls_back_to_keywords_when_ai_fails(
    auth_client,
    seeded,
    session,
    session_factory,
    make_document,
    accounted_llm,
    wire,
    test_settings_override,
    failure,
):
    """A spent budget or a failing embedding call degrades /search to
    keyword-only results (as /signals?q does), never a 500."""
    doc, sig = await _nexus_corpus(session, session_factory, make_document, accounted_llm)
    if failure == "budget":
        wire(settings=test_settings_override(llm_daily_budget_usd=0.0))
    else:
        wire({"query_understanding": [EMPTY_PLAN], "search_embed": [RuntimeError("down")]})
    r = await (await auth_client("pm")).get("/api/v1/search", params={"q": "nexus"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["search_mode"] == "keyword"
    assert {h["ref_id"] for h in body["hits"]} == {doc.id, sig.id}


async def test_search_mode_is_hybrid_with_embeddings(
    auth_client, seeded, session, session_factory, make_document, accounted_llm, wire
):
    await _nexus_corpus(session, session_factory, make_document, accounted_llm)
    wire({"query_understanding": [EMPTY_PLAN]})
    r = await (await auth_client("pm")).get("/api/v1/search", params={"q": "nexus"})
    assert r.status_code == 200 and r.json()["search_mode"] == "hybrid"


async def test_search_returns_only_relevant_articles(
    auth_client, seeded, session, session_factory, make_document, accounted_llm, wire
):
    """Articles show only once triage judged them relevant. Documents are
    embedded before triage, so the index holds rejected, duplicate and never-triaged ones
    too (the agents keep them); "harness" must not surface the wiring-harness story."""
    llm = accounted_llm()
    docs = {}
    for status in ("relevant", "irrelevant", "duplicate", "new"):
        docs[status] = await make_document(
            title=f"Harness {status}",
            clean_text=f"Harness story {status}. The harness news continues here. " * 3,
            status="new",  # embedded before triage (index_new), judged afterwards
        )
    await index_documents(session_factory, llm, [d.id for d in docs.values()], ctx=CTX)
    for status, d in docs.items():
        d.status = status
    await session.flush()

    wire({"query_understanding": lambda **_: {**EMPTY_PLAN, "rewritten": "harness"}})
    for role in ("pm", "analyst"):
        r = await (await auth_client(role)).get("/api/v1/search", params={"q": "harness"})
        assert r.status_code == 200, r.text
        hits = r.json()["hits"]
        assert [h["ref_id"] for h in hits if h["kind"] == "article_chunk"] == [docs["relevant"].id]


async def test_search_returns_titles_and_urls(
    auth_client, seeded, session, session_factory, make_document, accounted_llm, wire
):
    gitlab = await session.scalar(select(Competitor.id).where(Competitor.slug == "gitlab"))
    doc = await make_document(
        title="GitLab ships a compatible registry",
        clean_text="Intro text. " * 40 + "The Nexus 3.97 integration lands today. " + "Tail. " * 40,
        published_at=datetime(2026, 10, 2, tzinfo=UTC),
        status="relevant",
    )
    session.add(DocumentCompetitor(document_id=doc.id, competitor_id=gitlab, salience=1.0))
    sig = Signal(
        title="GitLab adds registry", scope="competitor", fact="Nexus 3.97 fact", status="approved"
    )
    kb = JfrogKbDoc(
        url="https://jfrog.com/help/nexus-migration",
        title="Nexus migration",
        text="Nexus 3.97 guide",
        content_hash="h",
    )
    session.add_all([sig, kb])
    await session.flush()
    llm = accounted_llm()
    await index_documents(session_factory, llm, [doc.id], ctx=CTX)
    await index_signal(session_factory, llm, sig.id, ctx=CTX)
    await index_kb_doc(session_factory, llm, kb.id, ctx=CTX)

    wire({"query_understanding": [EMPTY_PLAN]})
    r = await (await auth_client("pm")).get("/api/v1/search", params={"q": "Nexus 3.97"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"]["rewritten"] == "nexus"
    by_kind = {h["kind"]: h for h in body["hits"]}
    art = by_kind["article_chunk"]
    assert art["title"] == "GitLab ships a compatible registry"
    assert art["url"].startswith("https://example.com/a-")
    assert "Nexus 3.97" in art["snippet"] and len(art["snippet"]) <= 300
    assert art["competitors"] == ["gitlab"]
    assert by_kind["signal"]["title"] == "GitLab adds registry"
    assert by_kind["signal"]["url"] == f"/signals/{sig.id}"
    assert by_kind["jfrog_kb"]["title"] == "Nexus migration"
    assert by_kind["jfrog_kb"]["url"] == "https://jfrog.com/help/nexus-migration"


async def test_search_kb_hit_titled_by_its_url_shows_the_product(
    auth_client, seeded, session, session_factory, accounted_llm, monkeypatch
):
    """The crawl stored each KB page's URL as its title; Search everything
    shows the product name from jfrog_kb.yaml instead."""
    kb = JfrogKbDoc(
        url="https://jfrog.com/xray/",
        title="https://jfrog.com/xray/",
        text="Xray scans every build for Nexus migrations. " * 3,
        content_hash="h",
    )
    session.add(kb)
    await session.flush()
    await index_kb_doc(session_factory, accounted_llm(), kb.id, ctx=CTX)
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    monkeypatch.setattr(deps, "_llm_cache", None)
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    r = await (await auth_client("pm")).get("/api/v1/search", params={"q": "xray scans"})
    (hit,) = [h for h in r.json()["hits"] if h["kind"] == "jfrog_kb"]
    assert (hit["title"], hit["url"]) == ("Xray", "https://jfrog.com/xray/")


async def test_search_drops_weak_vector_only_hits(
    auth_client, seeded, session, session_factory, accounted_llm, wire
):
    """/search keeps keyword hits and drops the unrelated vector-only tail."""
    hit = Signal(title="Nexus 3.97 ships", scope="competitor", fact="Nexus fact", status="auto")
    tail = Signal(title="Quarterly earnings", scope="competitor", fact="Revenue", status="auto")
    session.add_all([hit, tail])
    await session.flush()
    llm = accounted_llm()
    for sig in (hit, tail):
        await index_signal(session_factory, llm, sig.id, ctx=CTX)

    wire({"query_understanding": [EMPTY_PLAN]})
    r = await (await auth_client("pm")).get("/api/v1/search", params={"q": "nexus"})
    assert r.status_code == 200, r.text
    assert [h["ref_id"] for h in r.json()["hits"]] == [hit.id]


async def test_search_validates_query_length(auth_client, seeded, wire):
    wire()
    c = await auth_client("pm")
    assert (await c.get("/api/v1/search", params={"q": "a"})).status_code == 422
    assert (await c.get("/api/v1/search", params={"q": "a" * 301})).status_code == 422


async def test_search_rate_limited(auth_client, seeded, wire, monkeypatch):
    wire({"query_understanding": lambda **_: EMPTY_PLAN})
    monkeypatch.setattr(search_api.limiter, "limit", 2)
    c = await auth_client("pm")
    codes = [(await c.get("/api/v1/search", params={"q": "nexus"})).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_search_holds_no_transaction_across_llm_calls(
    auth_client, seeded, session_factory, wire
):
    from app.core.db import get_session

    sessions = []
    seen = []

    async def _override():
        async with session_factory() as s:
            sessions.append(s)
            yield s

    def _plan(**_):
        seen.append([s.in_transaction() for s in sessions])
        return EMPTY_PLAN

    wire({"query_understanding": _plan})
    app.dependency_overrides[get_session] = _override
    c = await auth_client("pm")
    sessions.clear()
    r = await c.get("/api/v1/search", params={"q": "nexus"})
    assert r.status_code == 200, r.text
    assert seen and sessions and seen == [[False] * len(sessions)]


async def test_search_applies_signal_visibility(
    auth_client, seeded, session, session_factory, make_document, accounted_llm, wire
):
    """A signal hit follows the signal's CURRENT status. Readers get
    auto|approved; analysts also needs_review; rejected and suppressed reach no one.
    Article and JFrog KB hits are unaffected."""
    doc = await make_document(
        title="Nexus 3.97 release notes",
        clean_text="The Nexus 3.97 release adds a firewall. " * 4,
        published_at=datetime(2026, 10, 2, tzinfo=UTC),
        status="relevant",
    )
    statuses = ("approved", "auto", "needs_review", "suppressed")
    signals = {
        s: Signal(title=f"Nexus 3.97 {s}", scope="competitor", fact="Nexus 3.97 fact", status=s)
        for s in statuses
    }
    # Indexed while approved, rejected afterwards: the index's own metadata is stale.
    signals["rejected"] = Signal(
        title="Nexus 3.97 withdrawn", scope="competitor", fact="Nexus 3.97 fact", status="approved"
    )
    kb = JfrogKbDoc(
        url="https://jfrog.com/help/nexus-397",
        title="Nexus 3.97 guide",
        text="Nexus 3.97 guide",
        content_hash="h-397",
    )
    session.add_all([*signals.values(), kb])
    await session.flush()
    llm = accounted_llm()
    await index_documents(session_factory, llm, [doc.id], ctx=CTX)
    for sig in signals.values():
        await index_signal(session_factory, llm, sig.id, ctx=CTX)
    await index_kb_doc(session_factory, llm, kb.id, ctx=CTX)
    signals["rejected"].status = "rejected"
    await session.flush()

    wire({"query_understanding": lambda **_: EMPTY_PLAN})
    status_of = {sig.id: s for s, sig in signals.items()}

    async def found(role: str) -> tuple[list[str], list[str]]:
        r = await (await auth_client(role)).get("/api/v1/search", params={"q": "Nexus 3.97"})
        assert r.status_code == 200, r.text
        hits = r.json()["hits"]
        shown = sorted(status_of[h["ref_id"]] for h in hits if h["kind"] == "signal")
        return shown, sorted(h["kind"] for h in hits if h["kind"] != "signal")

    assert await found("pm") == (["approved", "auto"], ["article_chunk", "jfrog_kb"])
    assert await found("analyst") == (
        ["approved", "auto", "needs_review"],
        ["article_chunk", "jfrog_kb"],
    )


async def test_search_hides_jfrog_self_articles_but_keeps_jfrog_docs(
    auth_client,
    seeded,
    session,
    session_factory,
    make_document,
    make_source,
    accounted_llm,
    monkeypatch,
):
    """Article hits on JFrog-self news (the legacy stock-price stories still
    `relevant` as members of suppressed signals) never show, for any role, and are dropped
    in the retrieval SQL, before the candidate cap. JFrog's own docs (kind jfrog_kb, also
    indexed with scope jfrog) still show."""
    jfrog = await seeded.scalar(select(Competitor).where(Competitor.is_self))
    own_feed = await make_source(name="JFrog Blog", competitor_id=jfrog.id)
    body = "JFrog shares fell after the JFrog results call. " * 4
    kept = await make_document(
        title="Sonatype answers JFrog", clean_text=body, scope_hint="competitor", status="relevant"
    )
    self_news = [
        await make_document(title="JFrog shares fall", clean_text=body, scope_hint="jfrog"),
        await make_document(
            title="UBS targets JFrog", clean_text=body, triage={"scope": "jfrog", "relevant": True}
        ),
        await make_document(
            title="JFrog date",
            clean_text=body,
            prefilter={"scope_hint": None, "reason": "jfrog_only"},
        ),
        await make_document(title="JFrog CLI 2.127", clean_text=body, source_id=own_feed.id),
    ]
    for d in self_news:
        d.status = "relevant"
    kb = JfrogKbDoc(
        url="https://jfrog.com/curation/", title="JFrog Curation", text=body, content_hash="h"
    )
    session.add(kb)
    await session.flush()
    llm = accounted_llm()
    await index_documents(session_factory, llm, [kept.id, *(d.id for d in self_news)], ctx=CTX)
    await index_kb_doc(session_factory, llm, kb.id, ctx=CTX)
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    monkeypatch.setattr(deps, "_llm_cache", None)
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    for role in ("analyst", "pm"):
        r = await (await auth_client(role)).get("/api/v1/search", params={"q": "jfrog shares"})
        assert r.status_code == 200, r.text
        hits = r.json()["hits"]
        assert {h["ref_id"] for h in hits if h["kind"] == "article_chunk"} == {kept.id}
        assert {h["ref_id"] for h in hits if h["kind"] == "jfrog_kb"} == {kb.id}
        search_api.limiter.reset()


async def test_search_hides_jfrog_scope_for_every_role(
    auth_client, seeded, session, session_factory, accounted_llm, monkeypatch
):
    """/search never returns a scope=jfrog signal, not even to analysts."""
    llm = accounted_llm()
    ids = {}
    for scope in ("competitor", "jfrog"):
        sig = Signal(title=f"Nexus {scope}", scope=scope, fact="Nexus fact", status="approved")
        session.add(sig)
        await session.flush()
        await index_signal(session_factory, llm, sig.id, ctx=CTX)
        ids[scope] = sig.id
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    monkeypatch.setattr(deps, "_llm_cache", None)
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    for role in ("analyst", "pm"):
        r = await (await auth_client(role)).get("/api/v1/search", params={"q": "nexus"})
        assert r.status_code == 200, r.text
        found = {h["ref_id"] for h in r.json()["hits"] if h["kind"] == "signal"}
        assert found == {ids["competitor"]}
        search_api.limiter.reset()
