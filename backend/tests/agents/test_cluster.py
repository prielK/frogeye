import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.agents.nodes.cluster import ClusterResult, StoryJudgeOut, cluster_documents
from app.agents.runtime.accounted import BudgetExceeded
from app.agents.runtime.context import RunCtx
from app.models import Competitor, DocumentCompetitor
from app.models.agents import AgentStep
from app.models.gold import Signal, SignalCompetitor, SignalDocument
from tests.agents.fakes import Refusal, fake_embedding

CTX = RunCtx(agent_run_id=None, node="cluster", agent="cluster")
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
HOUR = timedelta(hours=1)


def vec_at(cos: float) -> list[float]:
    """A unit vector whose cosine with `base()` is `cos`."""
    v = [0.0] * 512
    v[0], v[1] = cos, math.sqrt(1 - cos * cos)
    return v


def base() -> list[float]:
    v = [0.0] * 512
    v[0] = 1.0
    return v


@pytest.fixture
async def comp(seeded):
    rows = (await seeded.execute(select(Competitor))).scalars().all()
    return {c.slug: c.id for c in rows}


@pytest.fixture
def make_doc(session, make_document, make_source, comp):
    async def _make(
        cluster="evt",
        *,
        slugs=("sonatype",),
        scope=None,
        rel="B",
        embedding=None,
        published_at=NOW,
        **kw,
    ):
        src = await make_source(reliability=rel)
        text = kw.pop("clean_text", f"Body about {cluster} #cluster:{cluster}")
        doc = await make_document(
            source_id=src.id,
            status=kw.pop("status", "relevant"),
            title=kw.pop("title", f"Title {cluster}"),
            clean_text=text,
            published_at=published_at,
            triage={"scope": scope or ("competitor" if slugs else "industry")},
            embedding=embedding if embedding is not None else fake_embedding(text, 512),
            **kw,
        )
        for s in slugs:
            session.add(DocumentCompetitor(document_id=doc.id, competitor_id=comp[s]))
        await session.flush()
        return doc

    return _make


async def run(session_factory, llm, docs):
    return await cluster_documents(session_factory, llm, [d.id for d in docs], ctx=CTX)


async def members(session, signal_id):
    stmt = select(SignalDocument).where(SignalDocument.signal_id == signal_id)
    return list((await session.execute(stmt)).scalars())


async def test_same_event_two_sources_joins_and_corroborates(
    session, session_factory, make_doc, accounted_llm
):
    a = await make_doc(canonical_url="https://one.example.com/x")
    b = await make_doc(canonical_url="https://two.example.com/y", published_at=NOW + HOUR)
    llm = accounted_llm({})
    res = await run(session_factory, llm, [a, b])
    assert isinstance(res, ClusterResult)
    assert len(res.new_signal_ids) == 1
    assert res.grown_signal_ids == res.new_signal_ids
    sig = await session.get(Signal, res.new_signal_ids[0])
    await session.refresh(sig)
    assert sig.status == "pending"
    assert sig.corroboration_count == 2
    assert len(await members(session, sig.id)) == 2
    await session.refresh(a)
    await session.refresh(b)
    assert a.story_id == b.story_id == sig.id
    assert llm.backend.calls == []  # no embed, no judge


async def test_different_competitor_not_joined(session_factory, make_doc, accounted_llm):
    a = await make_doc(slugs=("sonatype",))
    b = await make_doc(slugs=("gitlab",))
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert len(res.new_signal_ids) == 2
    assert res.grown_signal_ids == []


async def test_borderline_uses_judge(session_factory, make_doc, accounted_llm):
    a = await make_doc(embedding=base())
    b = await make_doc(embedding=vec_at(0.82), published_at=NOW + HOUR)
    llm = accounted_llm({"story_judge": [{"same_event": True, "reason": "same release"}]})
    res = await run(session_factory, llm, [a, b])
    assert len(res.new_signal_ids) == 1
    assert len(res.grown_signal_ids) == 1
    assert llm.backend.calls == [("story_judge", "structured")]
    user = llm.backend.requests[0]["user"]
    assert f'<document id="{a.id}">' in user
    assert f'<document id="{b.id}">' in user


async def test_borderline_judge_says_different(session_factory, make_doc, accounted_llm):
    a = await make_doc(embedding=base())
    b = await make_doc(embedding=vec_at(0.82), published_at=NOW + HOUR)
    llm = accounted_llm({"story_judge": [StoryJudgeOut(same_event=False, reason="topic only")]})
    res = await run(session_factory, llm, [a, b])
    assert len(res.new_signal_ids) == 2
    assert res.grown_signal_ids == []


async def test_below_judge_floor_no_judge(session_factory, make_doc, accounted_llm):
    a = await make_doc(embedding=base())
    b = await make_doc(embedding=vec_at(0.70), published_at=NOW + HOUR)
    llm = accounted_llm({})
    res = await run(session_factory, llm, [a, b])
    assert len(res.new_signal_ids) == 2
    assert llm.backend.calls == []


async def test_judge_refusal_creates_new_signal(session, session_factory, make_doc, accounted_llm):
    a = await make_doc(embedding=base())
    b = await make_doc(embedding=vec_at(0.82), published_at=NOW + HOUR)
    llm = accounted_llm({"story_judge": [Refusal()]})
    res = await run(session_factory, llm, [a, b])
    assert len(res.new_signal_ids) == 2
    steps = (await session.execute(select(AgentStep))).scalars().all()
    assert any("error" in (s.result_summary or "") for s in steps)


async def test_budget_exceeded_propagates(session_factory, make_doc, accounted_llm, settings):
    a = await make_doc(embedding=base())
    b = await make_doc(embedding=vec_at(0.82), published_at=NOW + HOUR)
    llm = accounted_llm({"story_judge": [{"same_event": True, "reason": "x"}]})
    llm.settings = settings.model_copy(update={"llm_daily_budget_usd": 0.0})
    llm._spend = None
    with pytest.raises(BudgetExceeded):
        await run(session_factory, llm, [a, b])


async def test_outside_window_new_signal(session_factory, make_doc, accounted_llm):
    a = await make_doc()
    b = await make_doc(published_at=NOW + timedelta(hours=73))
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert len(res.new_signal_ids) == 2


async def test_industry_docs_cluster_without_competitors(session_factory, make_doc, accounted_llm):
    a = await make_doc("ind", slugs=())
    b = await make_doc("ind", slugs=(), published_at=NOW + 2 * HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert len(res.new_signal_ids) == 1
    assert len(res.grown_signal_ids) == 1


async def test_competitor_doc_does_not_join_industry_signal(
    session_factory, make_doc, accounted_llm
):
    a = await make_doc("evt", slugs=())
    b = await make_doc("evt", slugs=("sonatype",), published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert len(res.new_signal_ids) == 2


async def test_new_signal_carries_competitors_and_reliability(
    session, session_factory, make_doc, accounted_llm, comp
):
    a = await make_doc(slugs=("sonatype", "gitlab"), rel="C")
    res = await run(session_factory, accounted_llm({}), [a])
    sig = await session.get(Signal, res.new_signal_ids[0])
    assert sig.reliability == "C"
    assert sig.title == a.title
    assert sig.scope == "competitor"
    assert sig.first_seen_at == NOW
    stmt = select(SignalCompetitor.competitor_id).where(SignalCompetitor.signal_id == sig.id)
    ids = (await session.execute(stmt)).scalars().all()
    assert set(ids) == {comp["sonatype"], comp["gitlab"]}


async def test_primary_is_most_reliable(session, session_factory, make_doc, accounted_llm):
    a = await make_doc(rel="C", published_at=NOW)
    b = await make_doc(rel="A", published_at=NOW + HOUR)
    c = await make_doc(rel="A", published_at=NOW + 2 * HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b, c])
    rows = await members(session, res.new_signal_ids[0])
    assert [r.document_id for r in rows if r.is_primary] == [b.id]


async def test_better_source_marks_reanalysis(session, session_factory, make_doc, accounted_llm):
    a = await make_doc(rel="C")
    b = await make_doc(rel="A", published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    sid = res.new_signal_ids[0]
    assert res.reanalyze_signal_ids == [sid]
    sig = await session.get(Signal, sid)
    await session.refresh(sig)
    assert sig.reliability == "A"


async def test_equal_or_worse_source_no_reanalysis(session_factory, make_doc, accounted_llm):
    a = await make_doc(rel="B")
    b = await make_doc(rel="D", published_at=NOW + HOUR)
    c = await make_doc(rel="B", published_at=NOW + 2 * HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b, c])
    assert res.reanalyze_signal_ids == []
    assert len(res.grown_signal_ids) == 1


async def test_rerun_skips_docs_with_story(session, session_factory, make_doc, accounted_llm):
    a = await make_doc()
    b = await make_doc(published_at=NOW + HOUR)
    first = await run(session_factory, accounted_llm({}), [a, b])
    again = await run(session_factory, accounted_llm({}), [a, b])
    assert again == ClusterResult([], [], [])
    assert len(await members(session, first.new_signal_ids[0])) == 2


async def test_null_embedding_and_non_relevant_skipped(
    session, session_factory, make_doc, accounted_llm
):
    a = await make_doc()
    a.embedding = None
    b = await make_doc(status="irrelevant")
    await session.flush()
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert res == ClusterResult([], [], [])
    assert (await session.execute(select(Signal))).first() is None


async def test_www_host_counts_once(session, session_factory, make_doc, accounted_llm):
    a = await make_doc(canonical_url="https://www.Example.com/a")
    b = await make_doc(canonical_url="https://example.com/b", published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    sig = await session.get(Signal, res.new_signal_ids[0])
    await session.refresh(sig)
    assert sig.corroboration_count == 1


async def test_centroid_is_mean_of_members(session, session_factory, make_doc, accounted_llm):
    a = await make_doc(embedding=base())
    b = await make_doc(embedding=vec_at(0.9), published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    sig = await session.get(Signal, res.new_signal_ids[0])
    await session.refresh(sig)
    assert sig.centroid[0] == pytest.approx((1 + 0.9) / 2, abs=1e-5)
    assert sig.centroid[1] == pytest.approx(math.sqrt(1 - 0.81) / 2, abs=1e-5)


async def test_processed_in_published_order(session, session_factory, make_doc, accounted_llm):
    late = await make_doc(published_at=NOW + 5 * HOUR)  # lower id, passed first
    early = await make_doc(published_at=NOW)
    res = await run(session_factory, accounted_llm({}), [late, early])
    assert len(res.new_signal_ids) == 1
    sig = await session.get(Signal, res.new_signal_ids[0])
    assert sig.first_seen_at == NOW  # the earliest doc seeded the signal
    assert sig.title == early.title


async def test_competitorless_same_scope_joins(session_factory, make_doc, accounted_llm):
    # a competitor-scope doc whose triage named no tracked slug is competitor-less too
    a = await make_doc("c", slugs=(), scope="competitor")
    b = await make_doc("c", slugs=(), scope="competitor", published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert len(res.new_signal_ids) == 1
    assert len(res.grown_signal_ids) == 1


async def test_competitorless_doc_skips_signal_with_competitors(
    session_factory, make_doc, accounted_llm
):
    a = await make_doc("evt", slugs=("sonatype",), scope="industry")
    b = await make_doc("evt", slugs=(), scope="industry", published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert len(res.new_signal_ids) == 2


async def test_competitorless_different_scope_not_joined(session_factory, make_doc, accounted_llm):
    a = await make_doc("evt", slugs=(), scope="competitor")
    b = await make_doc("evt", slugs=(), scope="industry", published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert len(res.new_signal_ids) == 2


async def test_legacy_jfrog_scope_doc_never_creates_signal(
    session, session_factory, make_doc, accounted_llm
):
    # a document triaged earlier as JFrog-self news (scope "jfrog") and never clustered neither
    # opens a signal nor joins one, with or without a (stale) self link.
    ok = await make_doc("j", slugs=(), scope="industry")
    linked = await make_doc("j", slugs=("jfrog",), scope="jfrog", published_at=NOW + HOUR)
    loose = await make_doc("j", slugs=(), scope="jfrog", published_at=NOW + 2 * HOUR)
    res = await run(session_factory, accounted_llm({}), [ok, linked, loose])
    assert len(res.new_signal_ids) == 1 and res.grown_signal_ids == []
    assert [m.document_id for m in await members(session, res.new_signal_ids[0])] == [ok.id]
    assert (await session.execute(select(Signal.scope))).scalars().all() == ["industry"]
    for d in (linked, loose):
        await session.refresh(d)
        assert d.story_id is None


async def test_legacy_self_link_never_reaches_a_signal(
    session, session_factory, make_doc, accounted_llm, comp
):
    # a waiting legacy document can still carry a JFrog link (old prefilter/triage).
    # It is neither copied onto the signal nor used to pick the signal to join.
    versus = await make_doc("v", slugs=("sonatype", "jfrog"), scope="competitor")
    industry = await make_doc("n", slugs=(), scope="industry")
    research = await make_doc("n", slugs=("jfrog",), scope="industry", published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [versus, industry, research])
    assert len(res.new_signal_ids) == 2 and len(res.grown_signal_ids) == 1
    links = await session.scalars(select(SignalCompetitor.competitor_id))
    assert set(links) == {comp["sonatype"]}
    for d in (industry, research):
        await session.refresh(d)
    assert industry.story_id is not None
    assert research.story_id == industry.story_id  # joined the competitor-less industry story


async def test_empty_host_not_counted(session, session_factory, make_doc, accounted_llm):
    a = await make_doc(canonical_url="")
    b = await make_doc(canonical_url="https://x.example.com/b", published_at=NOW + HOUR)
    res = await run(session_factory, accounted_llm({}), [a, b])
    sig = await session.get(Signal, res.new_signal_ids[0])
    await session.refresh(sig)
    assert sig.corroboration_count == 1


async def test_exact_join_boundary_no_judge(session_factory, make_doc, accounted_llm):
    a = await make_doc(embedding=base())
    b = await make_doc(embedding=vec_at(0.85), published_at=NOW + HOUR)
    llm = accounted_llm({})
    res = await run(session_factory, llm, [a, b])
    assert len(res.new_signal_ids) == 1
    assert llm.backend.calls == []


async def test_exact_judge_boundary_calls_judge(session_factory, make_doc, accounted_llm):
    a = await make_doc(embedding=base())
    b = await make_doc(embedding=vec_at(0.78), published_at=NOW + HOUR)
    llm = accounted_llm({"story_judge": [{"same_event": False, "reason": "x"}]})
    res = await run(session_factory, llm, [a, b])
    assert llm.backend.calls == [("story_judge", "structured")]
    assert len(res.new_signal_ids) == 2


async def test_window_edge(session_factory, make_doc, accounted_llm):
    a = await make_doc()
    b = await make_doc(published_at=NOW + timedelta(hours=71))
    res = await run(session_factory, accounted_llm({}), [a, b])
    assert len(res.new_signal_ids) == 1


async def test_second_run_grows_existing_signal(session_factory, make_doc, accounted_llm):
    a = await make_doc()
    first = await run(session_factory, accounted_llm({}), [a])
    b = await make_doc(published_at=NOW + HOUR)
    second = await run(session_factory, accounted_llm({}), [b])
    assert second.new_signal_ids == []
    assert second.grown_signal_ids == first.new_signal_ids


async def test_google_news_items_corroborate_by_their_outlet(
    session, session_factory, make_doc, accounted_llm
):
    """Google News items keep the news.google.com link but name the outlet in
    the payload, so two outlets through Google News are two corroborating hosts."""
    from app.models import RawItem

    async def via_google(outlet: str | None, n: int):
        doc = await make_doc(
            canonical_url=f"https://news.google.com/rss/articles/{n}",
            published_at=NOW + n * HOUR,
        )
        raw = await session.get(RawItem, doc.raw_item_id)
        raw.payload = {"outlet": outlet, "outlet_url": f"https://www.{outlet}" if outlet else None}
        await session.flush()
        return doc

    docs = [await via_google("etnow.in", 0), await via_google("reuters.com", 1)]
    docs.append(await via_google("etnow.in", 2))  # the same outlet twice counts once
    docs.append(await via_google(None, 3))  # no outlet named: the aggregator's host
    res = await run(session_factory, accounted_llm({}), docs)
    [sid] = res.new_signal_ids
    sig = await session.get(Signal, sid)
    await session.refresh(sig)
    assert sig.corroboration_count == 3  # etnow.in, reuters.com, news.google.com
