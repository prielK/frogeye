import math
from datetime import UTC, datetime

import pytest

from app.agents.runtime.context import RunCtx
from app.models import RetrievalItem
from app.rag.search import SearchFilters, build_query_plan_schema, hybrid_search, search
from tests.agents.fakes import fake_embedding

CTX = RunCtx(agent_run_id=None, node="search")
DIMS = 512
EMPTY_PLAN = {
    "rewritten": "",
    "variants": [],
    "competitor_slugs": [],
    "date_from": None,
    "date_to": None,
    "kinds": [],
}


@pytest.fixture
def add_item(session):
    n = 0

    async def _add(text, *, kind="article_chunk", embed=None, **kw):
        nonlocal n
        n += 1
        item = RetrievalItem(
            kind=kind,
            ref_id=kw.pop("ref_id", n),
            chunk_no=0,
            text=text,
            embedding=fake_embedding(embed if embed is not None else text, DIMS),
            **kw,
        )
        session.add(item)
        await session.flush()
        return item

    return _add


def _q(text):
    return fake_embedding(text, DIMS)


async def test_keyword_finds_identifier(session, add_item):
    hit_item = await add_item("Sonatype Nexus 3.97 changelog: new proxy repos")
    await add_item("Artifactory release notes for the week")
    hits = await hybrid_search(session, query="Nexus 3.97", query_embedding=_q("unrelated zzz"))
    top = hits[0]
    assert top.item_id == hit_item.id
    assert top.rank_keyword == 1


async def test_keyword_simple_config_matches_stopword_only_query(session, add_item):
    # Every word is an English stop-word, so the english tsquery is empty and only the
    # `simple` arm can match.
    target = await add_item("To be or not to be, that is the question")
    await add_item("Artifactory release notes for the week")
    hits = await hybrid_search(
        session, query="to be or not to be", query_embedding=_q("unrelated zzz")
    )
    assert hits[0].item_id == target.id
    assert hits[0].rank_keyword == 1


async def test_vector_finds_paraphrase(session, add_item):
    near = await add_item("A registry for machine learning models #cluster:x")
    await add_item("Quarterly earnings call transcript #cluster:y")
    hits = await hybrid_search(
        session, query="qqqq", query_embedding=_q("model hub launch #cluster:x")
    )
    assert hits[0].item_id == near.id
    assert hits[0].rank_vector == 1
    assert hits[0].rank_keyword is None


async def test_keyword_only_without_embedding(session, add_item):
    """Query_embedding=None skips the vector arm; only FTS matches come back."""
    kw = await add_item("Sonatype Nexus 3.97 changelog", kind="signal", embed="#cluster:k")
    await add_item("Unrelated text near in vector space #cluster:k", kind="signal")
    await add_item("Nexus article chunk", embed="#cluster:k")
    hits = await hybrid_search(session, query="Nexus", query_embedding=None, kinds=["signal"])
    assert [h.item_id for h in hits] == [kw.id]
    assert hits[0].rank_vector is None and hits[0].rank_keyword == 1
    assert hits[0].score == pytest.approx(1 / 61)


async def test_vector_only_skips_the_keyword_arm(session, add_item):
    """keyword=False ranks by the vector arm alone: a keyword match far in vector space is
    not boosted, and the call needs a query embedding."""
    near = await add_item("model registry launch #cluster:v")
    await add_item("alpha beta exact words", embed="far away text")
    hits = await hybrid_search(
        session, query="alpha beta", query_embedding=_q("#cluster:v"), keyword=False, k=10
    )
    assert hits[0].item_id == near.id
    assert all(h.rank_keyword is None for h in hits)
    assert hits[0].score == pytest.approx(1 / 61)
    with pytest.raises(ValueError):
        await hybrid_search(session, query="alpha", query_embedding=None, keyword=False)


async def test_rrf_fuses_both_lists(session, add_item):
    both = await add_item("alpha beta guide #cluster:q")
    vec_only = await add_item("something else entirely #cluster:q")
    kw_only = await add_item("alpha beta mention", embed="far away text")
    hits = await hybrid_search(session, query="alpha beta", query_embedding=_q("#cluster:q"), k=10)
    by_id = {h.item_id: h for h in hits}
    assert hits[0].item_id == both.id
    assert by_id[both.id].rank_vector is not None and by_id[both.id].rank_keyword is not None
    assert by_id[vec_only.id].rank_keyword is None
    assert by_id[kw_only.id].rank_keyword is not None
    assert by_id[both.id].score > by_id[vec_only.id].score
    assert by_id[both.id].score > by_id[kw_only.id].score
    assert by_id[both.id].score == pytest.approx(
        1 / (60 + by_id[both.id].rank_vector) + 1 / (60 + by_id[both.id].rank_keyword)
    )


async def test_filters_competitor_kind_date(session, add_item):
    old = datetime(2026, 1, 1, tzinfo=UTC)
    new = datetime(2026, 10, 1, tzinfo=UTC)
    a = await add_item("shared topic #cluster:f", competitor_ids=[1], published_at=new)
    b = await add_item("shared topic two #cluster:f", competitor_ids=[2], published_at=new)
    c = await add_item(
        "shared topic three #cluster:f", kind="signal", competitor_ids=[1], published_at=new
    )
    d = await add_item("shared topic four #cluster:f", competitor_ids=[1], published_at=old)
    e = await add_item(
        "shared topic five #cluster:f", competitor_ids=[1], published_at=new, approved=True
    )
    qv = _q("#cluster:f")

    async def ids(**kw):
        hits = await hybrid_search(session, query="shared topic", query_embedding=qv, k=20, **kw)
        return {h.item_id for h in hits}

    assert await ids() == {a.id, b.id, c.id, d.id, e.id}
    assert await ids(competitor_ids=[2]) == {b.id}
    assert await ids(competitor_ids=[1, 2], kinds=["signal"]) == {c.id}
    assert await ids(date_from=datetime(2026, 6, 1, tzinfo=UTC)) == {a.id, b.id, c.id, e.id}
    assert await ids(date_to=datetime(2026, 6, 1, tzinfo=UTC)) == {d.id}
    assert await ids(approved_only=True) == {e.id}


async def _two_competitors(session, competitor_factory, add_item):
    c1, c2 = competitor_factory(), competitor_factory()
    session.add_all([c1, c2])
    await session.flush()
    i1 = await add_item("first vendor news #cluster:s", competitor_ids=[c1.id])
    i2 = await add_item("second vendor news #cluster:s", competitor_ids=[c2.id])
    return c1, c2, i1, i2


async def test_query_understanding_applies_extracted_competitor(
    session, session_factory, competitor_factory, add_item, accounted_llm
):
    c1, c2, i1, i2 = await _two_competitors(session, competitor_factory, add_item)
    plan = {**EMPTY_PLAN, "rewritten": "vendor news #cluster:s", "competitor_slugs": [c1.slug]}
    llm = accounted_llm({"query_understanding": [plan]})
    result = await search(session_factory, llm, "news about vendor one", ctx=CTX)
    assert result.plan is not None and result.plan.competitor_slugs == [c1.slug]
    assert [h.item_id for h in result.hits] == [i1.id]


async def test_explicit_filters_override_plan(
    session, session_factory, competitor_factory, add_item, accounted_llm
):
    c1, c2, i1, i2 = await _two_competitors(session, competitor_factory, add_item)
    plan = {**EMPTY_PLAN, "rewritten": "vendor news #cluster:s", "competitor_slugs": [c1.slug]}
    llm = accounted_llm({"query_understanding": [plan]})
    result = await search(
        session_factory,
        llm,
        "news about vendor",
        filters=SearchFilters(competitor_slugs=[c2.slug]),
        ctx=CTX,
    )
    assert [h.item_id for h in result.hits] == [i2.id]


async def test_rerank_reorders(session, session_factory, add_item, accounted_llm):
    for i in range(3):
        await add_item(f"candidate number {i} #cluster:r")
    base = await search(
        session_factory, accounted_llm(), "candidate", understand=False, rerank=False, ctx=CTX
    )
    assert len(base.hits) == 3
    llm = accounted_llm({"rerank": [{"order": [2, 0, 1, 7, 2]}]})  # junk indices are ignored
    out = await search(session_factory, llm, "candidate", understand=False, rerank=True, ctx=CTX)
    assert [h.item_id for h in out.hits] == [base.hits[i].item_id for i in (2, 0, 1)]
    assert out.plan is None


async def test_rerank_failure_keeps_fused_order(session, session_factory, add_item, accounted_llm):
    for i in range(2):
        await add_item(f"candidate number {i} #cluster:r")
    base = await search(session_factory, accounted_llm(), "candidate", understand=False, ctx=CTX)
    llm = accounted_llm({"rerank": [RuntimeError("boom")]})
    out = await search(session_factory, llm, "candidate", understand=False, rerank=True, ctx=CTX)
    assert [h.item_id for h in out.hits] == [h.item_id for h in base.hits]


def test_query_plan_schema_is_strict_and_slug_enum():
    schema = build_query_plan_schema(["a", "b"]).model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["competitor_slugs"]["items"]["enum"] == ["a", "b"]


# ---- relative vector floor for API callers ------------------------------------------


def _axis() -> list[float]:
    v = [0.0] * DIMS
    v[0] = 1.0
    return v


def _at(cos: float) -> list[float]:
    """A unit vector whose cosine similarity with `_axis()` is `cos`."""
    v = [0.0] * DIMS
    v[0], v[1] = cos, math.sqrt(1 - cos * cos)
    return v


@pytest.fixture
def add_vec(session):
    async def _add(text: str, cos: float, *, kind: str = "signal") -> RetrievalItem:
        item = RetrievalItem(kind=kind, ref_id=0, chunk_no=0, text=text, embedding=_at(cos))
        session.add(item)
        await session.flush()
        item.ref_id = item.id
        await session.flush()
        return item

    return _add


async def _sims(session, *, floor: bool, query: str = "zzqq", **kw) -> dict[int, float | None]:
    hits = await hybrid_search(
        session, query=query, query_embedding=_axis(), k=50, vector_floor=floor, **kw
    )
    return {h.item_id: h.similarity for h in hits}


async def test_vector_floor_drops_the_tail_relative_to_the_best_hit(session, add_vec):
    """A vector-only hit stays only at cosine >= max(0.30, best - 0.12)."""
    items = {
        cos: await add_vec(f"vector only text {cos}", cos)
        for cos in (0.6, 0.5, 0.49, 0.46, 0.35, 0.25)
    }

    unfloored = await _sims(session, floor=False)  # the default: agent tools keep breadth
    assert set(unfloored) == {i.id for i in items.values()}
    assert unfloored[items[0.6].id] == pytest.approx(0.6)

    floored = await _sims(session, floor=True)
    assert set(floored) == {items[c].id for c in (0.6, 0.5, 0.49)}  # 0.60 - 0.12 = 0.48


async def test_vector_floor_absolute_minimum(session, add_vec):
    items = {cos: await add_vec(f"vector only text {cos}", cos) for cos in (0.36, 0.31, 0.29, 0.2)}
    floored = await _sims(session, floor=True)
    assert set(floored) == {items[0.36].id, items[0.31].id}  # max(0.30, 0.24) = 0.30


async def test_vector_floor_keeps_every_keyword_hit(session, add_vec):
    best = await add_vec("unrelated words here", 0.6)
    keyword_far = await add_vec("dependency firewall blocks malicious packages", 0.05)
    keyword_mid = await add_vec("a dependency firewall for npm", 0.4)
    weak = await add_vec("quarterly earnings call", 0.3)
    floored = await _sims(session, floor=True, query="dependency firewall")
    assert set(floored) == {best.id, keyword_far.id, keyword_mid.id}
    assert weak.id not in floored


async def test_vector_floor_respects_filters_and_keyword_only_mode(session, add_vec):
    article = await add_vec("article text", 0.9, kind="article_chunk")
    sig = await add_vec("signal text", 0.5)
    # the best similarity is taken within the filters, so the article does not raise the bar
    assert set(await _sims(session, floor=True, kinds=["signal"])) == {sig.id}
    assert set(await _sims(session, floor=True)) == {article.id}
    # without a query embedding there is no vector arm, so there is nothing to floor
    hits = await hybrid_search(
        session, query="signal", query_embedding=None, k=50, vector_floor=True
    )
    assert [h.item_id for h in hits] == [sig.id] and hits[0].similarity is None


async def test_search_vector_floor_is_opt_in(session, session_factory, add_vec, accounted_llm):
    near = await add_vec("near text", 0.6)
    far = await add_vec("far text", 0.1)
    llm = accounted_llm({"search_embed": lambda texts, **_: [_axis() for _ in texts]})
    wide = await search(session_factory, llm, "zzqq", understand=False, rerank=False, ctx=CTX, k=50)
    assert {h.item_id for h in wide.hits} == {near.id, far.id}
    narrow = await search(
        session_factory,
        llm,
        "zzqq",
        understand=False,
        rerank=False,
        vector_floor=True,
        ctx=CTX,
        k=50,
    )
    assert [h.item_id for h in narrow.hits] == [near.id]
