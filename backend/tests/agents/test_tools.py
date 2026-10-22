from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agents.runtime.context import RunCtx
from app.agents.tools import ToolDeps, get_tools
from app.models import AgentStep, Competitor, JfrogKbDoc, Kit, RetrievalItem
from tests.agents.fakes import fake_embedding

NAMES = [
    "search_intel",
    "get_jfrog_context",
    "get_similar_signals",
    "get_signal",
    "query_signals",
    "get_kit",
    "lookup_competitor",
]


@pytest.fixture
def deps(session_factory, accounted_llm, settings):
    return ToolDeps(session_factory, accounted_llm(), settings)


async def call(name, deps, **args):
    spec = get_tools([name])[0]
    out = await spec.func(spec.args_model.model_validate(args), deps)
    assert len(out.content) <= 4000
    return out.content


def test_all_tools_registered_with_strict_args():
    for spec in get_tools(NAMES):
        assert spec.args_model.model_config.get("extra") == "forbid"
        assert spec.to_def().name == spec.name


async def add_item(session, text, *, kind="article_chunk", ref_id, chunk_no=0, **kw):
    item = RetrievalItem(
        kind=kind,
        ref_id=ref_id,
        chunk_no=chunk_no,
        text=text,
        embedding=fake_embedding(text, 512),
        **kw,
    )
    session.add(item)
    await session.flush()
    return item


# ---- search_intel -----------------------------------------------------------------------


async def test_search_intel_formats_ids_and_filters(session, deps, docs_fixture, make_signal):
    s1 = docs_fixture["s1"]
    now = datetime.now(UTC)
    sonatype = (
        await session.execute(select(Competitor.id).where(Competitor.slug == "sonatype"))
    ).scalar_one()
    gitlab = (
        await session.execute(select(Competitor.id).where(Competitor.slug == "gitlab"))
    ).scalar_one()
    sig = await make_signal(title="Nexus launches model registry")
    await add_item(
        session,
        "Sonatype Nexus model registry announcement " + "x" * 600,
        ref_id=s1.id,
        chunk_no=2,
        published_at=now,
        competitor_ids=[sonatype],
    )
    await add_item(
        session,
        "Sonatype model registry signal",
        kind="signal",
        ref_id=sig.id,
        published_at=now,
        competitor_ids=[sonatype],
    )
    await add_item(
        session,
        "GitLab model registry announcement",
        ref_id=docs_fixture["g1"].id,
        published_at=now,
        competitor_ids=[gitlab],
    )
    await add_item(
        session,
        "Sonatype model registry old news",
        ref_id=docs_fixture["s2"].id,
        published_at=now - timedelta(days=80),
        competitor_ids=[sonatype],
    )

    content = await call("search_intel", deps, query="model registry", competitors=["sonatype"])
    lines = content.splitlines()
    assert any(line.startswith(f"[A{s1.id}#2] {now.date()} ") for line in lines)
    assert any(line.startswith(f"[S{sig.id}] ") for line in lines)
    assert all(len(line) < 450 for line in lines)
    assert "GitLab" not in content

    recent = await call("search_intel", deps, query="model registry", days=30)
    assert "old news" not in recent
    only_sig = await call("search_intel", deps, query="model registry", kinds=["signal"])
    assert only_sig.startswith(f"[S{sig.id}]") and len(only_sig.splitlines()) == 1


async def test_search_intel_recency_counts_back_from_its_clock(monkeypatch, deps):
    from app.agents.tools import search_intel as tool
    from app.rag.search import SearchResult

    pinned = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    seen = []

    async def fake_search(*args, filters, **kw):
        seen.append(filters)
        return SearchResult(plan=None, hits=[])

    monkeypatch.setattr(tool, "utcnow", lambda: pinned)
    monkeypatch.setattr(tool, "search", fake_search)
    await call("search_intel", deps, query="model registry", days=30)
    assert seen[0].date_from == pinned - timedelta(days=30)


async def test_search_intel_rejects_bad_args():
    spec = get_tools(["search_intel"])[0]
    for bad in (
        {"query": "x", "kinds": ["bogus"]},
        {"query": "x", "days": 91},
        {"query": "x", "extra": 1},
    ):
        with pytest.raises(ValidationError):
            spec.args_model.model_validate(bad)


async def test_search_intel_records_embed_step_with_tool_ctx(session, deps):
    await call("search_intel", deps, query="anything")
    steps = (await session.execute(select(AgentStep))).scalars().all()
    assert any(s.agent == "embed" for s in steps)


# ---- get_jfrog_context ------------------------------------------------------------------


async def test_get_jfrog_context_only_kb(session, deps, docs_fixture):
    kb = JfrogKbDoc(url="https://jfrog.com/x", title="X", text="t", content_hash="h")
    session.add(kb)
    await session.flush()
    await add_item(
        session, "Artifactory supports model registry via ML", kind="jfrog_kb", ref_id=kb.id
    )
    await add_item(session, "Competitor model registry article", ref_id=docs_fixture["s1"].id)
    content = await call("get_jfrog_context", deps, topic="model registry")
    assert content.startswith(f"[K{kb.id}#0] ")
    assert len(content.splitlines()) == 1


# ---- get_similar_signals ----------------------------------------------------------------


async def test_get_similar_signals_by_id_and_text(session, deps, make_signal):
    a = await make_signal(title="Alpha", centroid="alpha story #cluster:one", importance=70)
    b = await make_signal(title="Beta", centroid="beta story #cluster:one")
    c = await make_signal(title="Gamma", centroid="other #cluster:two")
    old = await make_signal(
        title="Old",
        centroid="old #cluster:one",
        first_seen_at=datetime.now(UTC) - timedelta(days=200),
    )
    by_id = await call("get_similar_signals", deps, signal_id=a.id, k=2)
    first = by_id.splitlines()[0]
    assert first.startswith(f"[S{b.id}] ") and "product_launch Beta (importance 50)" in first
    assert f"[S{a.id}]" not in by_id
    assert f"[S{old.id}]" not in by_id

    by_text = await call("get_similar_signals", deps, text="x #cluster:two", k=1)
    assert by_text.startswith(f"[S{c.id}] ")


async def test_agent_tools_skip_suppressed(session, deps, make_signal):
    # agents never see rejected or suppressed signals as prior context, even when one
    # is the closest match.
    now = datetime.now(UTC)
    anchor = await make_signal(title="Anchor", centroid="anchor #cluster:near")
    hidden = [
        await make_signal(title=f"Hidden {st}", status=st, centroid=f"{st} #cluster:near")
        for st in ("suppressed", "rejected")
    ]
    shown = [
        await make_signal(title=f"Shown {st}", status=st, centroid=f"{st} #cluster:near")
        for st in ("pending", "auto", "needs_review", "approved")
    ]
    far = await make_signal(title="Far", centroid="far #cluster:far")

    by_id = await call("get_similar_signals", deps, signal_id=anchor.id, k=10)
    by_text = await call("get_similar_signals", deps, text="x #cluster:near", k=10)
    for content in (by_id, by_text):
        assert not any(f"[S{s.id}]" in content for s in hidden)
        assert all(f"[S{s.id}]" in content for s in shown)
    assert by_id.splitlines()[-1].startswith(f"[S{far.id}] ")  # nearest first, far last

    for s in (*hidden, *shown):  # the hidden ones match the query best
        exact = "registry outage postmortem" if s in hidden else "registry outage"
        await add_item(session, exact, kind="signal", ref_id=s.id, published_at=now)
    found = await call("search_intel", deps, query="registry outage postmortem", kinds=["signal"])
    assert not any(f"[S{s.id}]" in found for s in hidden)
    assert all(f"[S{s.id}]" in found for s in shown)


async def test_signal_listing_tools_skip_suppressed(session, deps, seeded, make_signal):
    # The same rule for the other tools that list signals: the editor's query_signals, the analyst's
    # get_kit.
    kit = (await session.execute(select(Kit).limit(1))).scalar_one()
    for st in ("suppressed", "rejected", "approved"):
        await make_signal(title=f"Sig {st}", status=st, competitors=["sonatype"], kits=[kit.slug])
    flat = await call("query_signals", deps)
    by_comp = await call("query_signals", deps, group_by="competitor")
    kit_view = await call("get_kit", deps, slug=kit.slug)
    for content in (flat, by_comp, kit_view):
        assert "Sig approved" in content
        assert "Sig suppressed" not in content and "Sig rejected" not in content
    assert "1 signals in the last 30 days" in flat and "sonatype: 1 signals" in by_comp


async def test_get_similar_signals_requires_exactly_one():
    spec = get_tools(["get_similar_signals"])[0]
    for bad in ({}, {"signal_id": 1, "text": "x"}):
        with pytest.raises(ValidationError):
            spec.args_model.model_validate(bad)


# ---- get_signal -------------------------------------------------------------------------


async def test_get_signal_details(deps, docs_fixture, make_signal):
    doc = docs_fixture["s1"]
    sig = await make_signal(
        title="Nexus change",
        fact="Nexus did X",
        impact="Matters for Y",
        documents=[doc],
        evidence=[(doc, "the exact  quote", True), (doc, "shaky", False)],
    )
    content = await call("get_signal", deps, id=sig.id)
    assert content.startswith(f"[S{sig.id}] ")
    assert "fact: Nexus did X" in content and "impact: Matters for Y" in content
    assert f"[D{doc.id}] Sonatype Blog" in content
    assert f'[D{doc.id}] (verified) "the exact quote"' in content
    assert "(unverified)" in content
    assert "not found" in await call("get_signal", deps, id=999999)


async def test_get_signal_hides_suppressed(deps, make_signal):
    # for agents a rejected or suppressed signal does not exist.
    for st in ("suppressed", "rejected"):
        sig = await make_signal(title=f"Hidden {st}", status=st)
        content = await call("get_signal", deps, id=sig.id)
        assert content == f"Signal S{sig.id} not found." and "Hidden" not in content
    for st in ("pending", "auto", "needs_review", "approved"):
        sig = await make_signal(title=f"Shown {st}", status=st)
        assert (await call("get_signal", deps, id=sig.id)).startswith(f"[S{sig.id}] ")


# ---- query_signals ----------------------------------------------------------------------


async def test_query_signals_grouping(deps, seeded, make_signal):
    for i in range(4):
        await make_signal(
            title=f"Sonatype {i}",
            competitors=["sonatype"],
            importance=10 + i,
            signal_type="pricing_change",
        )
    await make_signal(title="GitLab one", competitors=["gitlab"], signal_type="funding")
    await make_signal(
        title="Stale",
        competitors=["sonatype"],
        first_seen_at=datetime.now(UTC) - timedelta(days=90),
    )

    flat = await call("query_signals", deps)
    assert "5 signals in the last 30 days" in flat

    by_comp = await call("query_signals", deps, group_by="competitor")
    lines = by_comp.splitlines()
    assert lines[0] == "sonatype: 4 signals"
    assert len([line for line in lines if line.startswith("  ")]) == 3 + 1
    assert "Sonatype 3" in lines[1]  # highest importance first
    assert "gitlab: 1 signals" in by_comp

    by_type = await call("query_signals", deps, group_by="signal_type", competitor="sonatype")
    assert by_type.startswith("pricing_change: 4 signals")
    assert "funding" not in by_type

    typed = await call("query_signals", deps, signal_type="funding", days=365)
    assert "1 signals" in typed and "GitLab one" in typed
    assert "No signals" in await call("query_signals", deps, competitor="nope")


async def test_query_signals_rejects_unknown_enum():
    spec = get_tools(["query_signals"])[0]
    for bad in (
        {"signal_type": "made_up"},
        {"group_by": "title"},
        {"days": 0},
    ):
        with pytest.raises(ValidationError):
            spec.args_model.model_validate(bad)


async def test_query_signals_is_parameterized(deps, seeded):
    out = await call("query_signals", deps, competitor="x'; DROP TABLE signals; --")
    assert "No signals" in out
    assert "signals" in (await call("query_signals", deps, group_by="competitor"))


# ---- get_kit / lookup_competitor --------------------------------------------------------


async def test_get_kit(session, deps, seeded, make_signal):
    kit = (await session.execute(select(Kit).limit(1))).scalar_one()
    sigs = [
        await make_signal(
            title=f"K sig {i}", kits=[kit.slug], first_seen_at=datetime.now(UTC) - timedelta(days=i)
        )
        for i in range(7)
    ]
    content = await call("get_kit", deps, slug=kit.slug)
    assert content.startswith(f"KIT {kit.slug}: {kit.title}")
    assert f"question: {kit.question}" in content
    ids = [line.split("]")[0] for line in content.splitlines() if line.startswith("[S")]
    assert ids == [f"[S{s.id}" for s in sigs[:5]]
    assert "No KIT" in await call("get_kit", deps, slug="nope")


async def test_lookup_competitor(session, deps, seeded):
    comp = (
        (
            await session.execute(
                select(Competitor).where(Competitor.aliases != [], Competitor.website.is_not(None))
            )
        )
        .scalars()
        .first()
    )
    alias = comp.aliases[0].upper()
    for needle in (alias, comp.slug, comp.name):
        content = await call("lookup_competitor", deps, name_or_alias=needle)
        assert f"slug: {comp.slug}" in content
        assert f"tier: {comp.tier}" in content
        assert f"website: {comp.website}" in content
    assert "No competitor" in await call("lookup_competitor", deps, name_or_alias="zzzz-none")


async def test_tools_use_default_ctx_when_unset(deps):
    assert deps.run_ctx == RunCtx(agent_run_id=None, node="tool")
