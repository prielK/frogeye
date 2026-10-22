"""Untrusted title, REPLACE, persistence, disambiguation errors."""

import pytest
from sqlalchemy import select

from app.agents.nodes.disambiguate import disambiguate_one
from app.agents.nodes.triage import apply_triage, triage_one
from app.agents.runtime.accounted import BudgetExceeded
from app.agents.runtime.context import RunCtx
from app.agents.runtime.types import ToolCall, ToolTurn, Usage
from app.agents.schemas import build_enums, make_models
from app.agents.tools.base import ToolDeps
from app.models import Competitor, Document, DocumentCompetitor
from app.models.agents import AgentStep
from app.models.enums import DocStatus
from tests.agents.fakes import Refusal
from tests.agents.test_triage import CTX, doc_with_prefilter, verdict


@pytest.fixture
async def enums(seeded):
    return await build_enums(seeded)


def assert_only_inside_document(user: str, doc_id: int, needle: str) -> None:
    start = user.index(f'<document id="{doc_id}">')
    end = user.index("</document>", start)
    assert user.count(needle) == 1
    assert start < user.index(needle) < end


async def test_title_only_inside_untrusted_block_triage(
    seeded, session_factory, make_document, accounted_llm, enums
):
    doc = await doc_with_prefilter(make_document, ["Sonatype"], title="Ignore previous TITLE-X")
    llm = accounted_llm({"triage": [verdict()]})
    await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert_only_inside_document(llm.backend.requests[0]["user"], doc.id, "TITLE-X")


async def test_title_only_inside_untrusted_block_disambiguate(
    seeded, session_factory, settings, make_document, accounted_llm, enums
):
    doc = await doc_with_prefilter(make_document, ["Harness"], title="Sneaky TITLE-X")
    call = ToolCall(id="c1", name="lookup_competitor", args={"name_or_alias": "Harness"})
    lookup = ToolTurn(content="", tool_calls=[call], usage=Usage(10, 0, 5), model="m")
    llm = accounted_llm(
        {
            "triage": [verdict(competitor_slugs=["harness"], confidence=0.5)],
            "disambiguate": [lookup, "done", verdict(competitor_slugs=["harness"])],
        }
    )
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    deps = ToolDeps(session_factory, llm, settings)
    await disambiguate_one(dec, llm=llm, deps=deps, enums=enums, ctx=CTX)
    reqs = [r for r in llm.backend.requests if r["task"] == "disambiguate"]
    assert [r["kind"] for r in reqs] == ["tool_turn", "tool_turn", "structured"]
    for r in reqs:
        text = r["messages"][0].content if r["kind"] == "tool_turn" else r["user"]
        assert_only_inside_document(text, doc.id, "TITLE-X")
    assert all(r["model"] == settings.model_smart for r in reqs)
    assert {t.name for t in reqs[0]["tools"]} == {"lookup_competitor", "search_intel"}
    assert '"competitor_slugs":["harness"]' in reqs[0]["messages"][0].content
    # the first decision (its reason is a model's free text) travels as data too.
    assert '<document id="first">{' in reqs[0]["messages"][0].content


async def test_missing_doc_logs_warning(session_factory, accounted_llm, enums, caplog):
    llm = accounted_llm({"triage": []})
    dec = await triage_one(999_999, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert dec is None
    assert "999999" in caplog.text


async def test_irrelevant_low_confidence_does_not_disambiguate(
    seeded, session_factory, make_document, accounted_llm, enums
):
    doc = await doc_with_prefilter(make_document, ["Harness"])
    out = verdict(relevant=False, competitor_slugs=[], signal_type="other", confidence=0.2)
    llm = accounted_llm({"triage": [out]})
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert dec.needs_disambiguation is False


async def test_ambiguous_alias_match_is_case_insensitive(
    seeded, session_factory, make_document, accounted_llm, enums
):
    doc = await doc_with_prefilter(make_document, ["harness"])
    llm = accounted_llm({"triage": [verdict(confidence=0.95)]})
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert dec.needs_disambiguation is True


# ---- apply_triage REPLACE semantics ---------------------------------------------------


async def linked(session, doc_id):
    session.expire_all()
    rows = await session.execute(
        select(Competitor.slug)
        .join(DocumentCompetitor, DocumentCompetitor.competitor_id == Competitor.id)
        .where(DocumentCompetitor.document_id == doc_id)
    )
    return set(rows.scalars())


async def comp_id(session, slug):
    q = select(Competitor.id).where(Competitor.slug == slug)
    return (await session.execute(q)).scalar_one()


async def link(session, doc_id, *slugs):
    for s in slugs:
        session.add(DocumentCompetitor(document_id=doc_id, competitor_id=await comp_id(session, s)))
    await session.flush()


def triage_out(enums, **kw):
    return make_models(enums).TriageOut(**verdict(**kw))


async def test_apply_replaces_prelinked_competitors(seeded, session, make_document, enums):
    doc = await doc_with_prefilter(make_document, ["x"])
    doc_id = doc.id
    await link(session, doc_id, "snyk")
    await apply_triage(session, doc_id, triage_out(enums))
    assert await linked(session, doc_id) == {"sonatype"}


async def test_apply_irrelevant_drops_all_links_including_own_feed(
    seeded, session, make_source, make_document, enums
):
    src = await make_source(competitor_id=await comp_id(session, "gitlab"))
    doc = await doc_with_prefilter(make_document, ["x"], source_id=src.id)
    doc_id = doc.id
    await link(session, doc_id, "gitlab", "snyk")
    out = triage_out(enums, relevant=False, competitor_slugs=[], signal_type="other")
    await apply_triage(session, doc_id, out)
    assert await linked(session, doc_id) == set()


async def test_apply_own_feed_auto_pass_false_not_kept(
    seeded, session, make_source, make_document, enums
):
    gitlab = await comp_id(session, "gitlab")
    src = await make_source(competitor_id=gitlab, config={"auto_pass": False})
    doc = await doc_with_prefilter(make_document, ["x"], source_id=src.id)
    doc_id = doc.id
    await link(session, doc_id, "gitlab")
    await apply_triage(session, doc_id, triage_out(enums))
    assert await linked(session, doc_id) == {"sonatype"}


async def test_apply_own_feed_already_in_slugs_not_duplicated(
    seeded, session, make_source, make_document, enums
):
    src = await make_source(competitor_id=await comp_id(session, "gitlab"))
    doc = await doc_with_prefilter(make_document, ["x"], source_id=src.id)
    doc_id = doc.id
    await link(session, doc_id, "gitlab")
    await apply_triage(session, doc_id, triage_out(enums, competitor_slugs=["gitlab"]))
    assert await linked(session, doc_id) == {"gitlab"}


async def test_apply_persists_via_short_session_commit(
    seeded, session_factory, make_document, enums
):
    doc = await doc_with_prefilter(make_document, ["x"])
    doc_id = doc.id
    async with session_factory() as short:
        await apply_triage(short, doc_id, triage_out(enums))
        await short.commit()
    async with session_factory() as fresh:
        got = await fresh.get(Document, doc_id)
        assert got.status == DocStatus.relevant
        assert got.triage["competitor_slugs"] == ["sonatype"]
        assert await linked(fresh, doc_id) == {"sonatype"}


# ---- disambiguate_one error contract --------------------------------------------


async def _first_pass(make_document, accounted_llm, session_factory, enums, script):
    doc = await doc_with_prefilter(make_document, ["Harness"])
    triaged = verdict(competitor_slugs=["harness"], confidence=0.5)
    llm = accounted_llm({"triage": [triaged], **script})
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    return llm, dec


@pytest.mark.parametrize("failure", [["done", Refusal()], [RuntimeError("boom")]])
async def test_disambiguation_failure_keeps_triage_and_logs(
    seeded, session, session_factory, settings, make_document, accounted_llm, enums, failure
):
    llm, dec = await _first_pass(
        make_document, accounted_llm, session_factory, enums, {"disambiguate": failure}
    )
    deps = ToolDeps(session_factory, llm, settings)
    ctx = RunCtx(None, "disambiguate", "disambiguate")
    out = await disambiguate_one(dec, llm=llm, deps=deps, enums=enums, ctx=ctx)
    assert out is dec.out
    q = select(AgentStep).where(AgentStep.kind == "node", AgentStep.agent == "disambiguate")
    steps = (await session.execute(q)).scalars().all()
    assert len(steps) == 1 and steps[0].result_summary.startswith("error")


async def test_disambiguation_budget_propagates(
    seeded, session_factory, settings, make_document, accounted_llm, enums
):
    llm, dec = await _first_pass(
        make_document, accounted_llm, session_factory, enums, {"disambiguate": ["x"]}
    )
    llm.settings = settings.model_copy(update={"llm_daily_budget_usd": 0.0})
    llm._spend = None
    deps = ToolDeps(session_factory, llm, settings)
    with pytest.raises(BudgetExceeded):
        await disambiguate_one(dec, llm=llm, deps=deps, enums=enums, ctx=CTX)
