from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agents.nodes.disambiguate import disambiguate_one
from app.agents.nodes.triage import (
    TriageDecision,
    apply_triage,
    revive_triage_out,
    select_for_triage,
    triage_one,
)
from app.agents.runtime.accounted import BudgetExceeded
from app.agents.runtime.context import RunCtx
from app.agents.runtime.types import ToolCall, ToolTurn, Usage
from app.agents.schemas import build_enums, make_models
from app.agents.tools.base import ToolDeps
from app.models import Competitor, Document, DocumentCompetitor
from app.models.agents import AgentStep
from app.models.enums import DocStatus
from tests.agents.fakes import Refusal

CTX = RunCtx(agent_run_id=None, node="triage", agent="triage")


def verdict(**kw):
    base = {
        "relevant": True,
        "scope": "competitor",
        "competitor_slugs": ["sonatype"],
        "signal_type": "product_launch",
        "confidence": 0.9,
        "reason": "Sonatype ships a product.",
    }
    base.update(kw)
    return base


@pytest.fixture
async def enums(seeded):
    return await build_enums(seeded)


async def doc_with_prefilter(make_document, terms, **kw):
    return await make_document(
        title=kw.pop("title", "Sonatype launches thing"),
        clean_text=kw.pop("clean_text", "Sonatype launched a new product today."),
        prefilter={"matched_terms": terms, "scope_hint": "competitor"},
        **kw,
    )


async def test_select_for_triage_window_and_cap(session, make_document, settings):
    now = datetime.now(UTC)
    pf = {"matched_terms": [], "scope_hint": "industry"}
    new = await make_document(published_at=now - timedelta(days=1), prefilter=pf)
    newer = await make_document(published_at=now - timedelta(hours=1), prefilter=pf)
    await make_document(published_at=now - timedelta(days=60), prefilter=pf)  # outside window
    await make_document(published_at=now)  # not prefiltered
    await make_document(published_at=now, prefilter=pf, status="irrelevant")
    ids = await select_for_triage(session, settings)
    assert ids == [newer.id, new.id]
    capped = settings.model_copy(update={"triage_cap": 1})
    assert await select_for_triage(session, capped) == [newer.id]


async def test_triage_marks_relevant_and_links_competitors(
    seeded, session, session_factory, make_document, accounted_llm, enums
):
    doc = await doc_with_prefilter(make_document, ["Sonatype"])
    llm = accounted_llm({"triage": [verdict()]})
    doc_id = doc.id
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert isinstance(dec, TriageDecision)
    assert not dec.needs_disambiguation
    await apply_triage(session, doc_id, dec.out)
    session.expire_all()
    got = await session.get(Document, doc_id)
    assert got.status == DocStatus.relevant
    assert got.triage["competitor_slugs"] == ["sonatype"]
    links = (
        (
            await session.execute(
                select(Competitor.slug)
                .join(DocumentCompetitor, DocumentCompetitor.competitor_id == Competitor.id)
                .where(DocumentCompetitor.document_id == doc_id)
            )
        )
        .scalars()
        .all()
    )
    assert links == ["sonatype"]
    user = llm.backend.requests[0]["user"]
    assert f'<document id="{doc_id}">' in user
    assert "Sonatype" in user


async def test_apply_keeps_own_feed_competitor(
    seeded, session, make_source, make_document, accounted_llm, enums
):
    gitlab = (
        await session.execute(select(Competitor).where(Competitor.slug == "gitlab"))
    ).scalar_one()
    src = await make_source(competitor_id=gitlab.id)
    doc = await doc_with_prefilter(make_document, ["x"], source_id=src.id)
    out = make_models(enums).TriageOut(**verdict(competitor_slugs=["sonatype"]))
    await apply_triage(session, doc.id, out)
    ids = set(
        (
            await session.execute(
                select(DocumentCompetitor.competitor_id).where(
                    DocumentCompetitor.document_id == doc.id
                )
            )
        ).scalars()
    )
    assert gitlab.id in ids and len(ids) == 2


async def test_triage_irrelevant(
    seeded, session, session_factory, make_document, accounted_llm, enums
):
    doc = await doc_with_prefilter(make_document, ["Harness"])
    out = verdict(relevant=False, scope="industry", competitor_slugs=[], signal_type="other")
    llm = accounted_llm({"triage": [out]})
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert not dec.needs_disambiguation
    await apply_triage(session, doc.id, dec.out)
    assert (await session.get(Document, doc.id)).status == DocStatus.irrelevant


@pytest.mark.parametrize(
    "terms,confidence,expected",
    [(["Sonatype"], 0.5, True), (["Harness"], 0.95, True), (["Sonatype"], 0.95, False)],
)
async def test_low_confidence_routes_to_disambiguation(
    seeded, session_factory, make_document, accounted_llm, enums, terms, confidence, expected
):
    doc = await doc_with_prefilter(make_document, terms)
    llm = accounted_llm({"triage": [verdict(confidence=confidence)]})
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert dec.needs_disambiguation is expected


async def test_disambiguation_corrects_harness_word_sense(
    seeded, session, session_factory, settings, make_document, accounted_llm, enums
):
    doc = await doc_with_prefilter(
        make_document,
        ["Harness"],
        title="Teams harness AI",
        clean_text="Teams harness AI to speed up work.",
    )
    call = ToolCall(id="c1", name="lookup_competitor", args={"name_or_alias": "Harness"})
    lookup = ToolTurn(content="", tool_calls=[call], usage=Usage(10, 0, 5), model="m")
    corrected = verdict(
        relevant=False, scope="industry", competitor_slugs=[], signal_type="other", confidence=0.9
    )
    llm = accounted_llm(
        {
            "triage": [verdict(competitor_slugs=["harness"], confidence=0.8)],
            "disambiguate": [lookup, "done", corrected],
        }
    )
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert dec.needs_disambiguation
    deps = ToolDeps(session_factory, llm, settings)
    out = await disambiguate_one(dec, llm=llm, deps=deps, enums=enums, ctx=CTX)
    assert out.relevant is False
    sys_prompt = next(r for r in llm.backend.requests if r["task"] == "disambiguate")["system"]
    assert "Harness the DevOps company" in sys_prompt
    tools = (
        (await session.execute(select(AgentStep.tool_name).where(AgentStep.kind == "tool")))
        .scalars()
        .all()
    )
    assert tools == ["lookup_competitor"]


@pytest.mark.parametrize("failure", [Refusal(), RuntimeError("boom")])
async def test_llm_error_leaves_doc_new(
    seeded, session, session_factory, make_document, accounted_llm, enums, failure
):
    doc = await doc_with_prefilter(make_document, ["Sonatype"])
    llm = accounted_llm({"triage": [failure]})
    dec = await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)
    assert dec is None
    assert (await session.get(Document, doc.id)).status == DocStatus.new
    steps = (
        (
            await session.execute(
                select(AgentStep).where(AgentStep.kind == "node", AgentStep.agent == "triage")
            )
        )
        .scalars()
        .all()
    )
    assert len(steps) == 1 and steps[0].result_summary.startswith("error")


async def test_budget_exceeded_propagates(
    seeded, session_factory, make_document, accounted_llm, enums, settings
):
    doc = await doc_with_prefilter(make_document, ["Sonatype"])
    llm = accounted_llm(
        {"triage": [verdict()]},
        settings=settings.model_copy(update={"llm_daily_budget_usd": 0.0}),
    )
    with pytest.raises(BudgetExceeded):
        await triage_one(doc.id, llm=llm, session_factory=session_factory, enums=enums, ctx=CTX)


async def test_unknown_slug_rejected_by_schema(enums):
    models = make_models(enums)
    models.TriageOut(**verdict(competitor_slugs=["sonatype"]))
    with pytest.raises(ValidationError):
        models.TriageOut(**verdict(competitor_slugs=["acme"]))
    props = models.TriageOut.model_json_schema()
    assert props["additionalProperties"] is False
    assert set(props["required"]) == set(props["properties"])


# ---- JFrog itself is never a triage scope or company ---------------------------------


async def test_triage_schema_has_no_jfrog_scope(seeded, session, make_document, enums):
    model = make_models(enums).TriageOut
    assert "jfrog" not in get_args(model.model_fields["scope"].annotation)
    assert set(get_args(model.model_fields["scope"].annotation)) == {"competitor", "industry"}
    (slug_literal,) = get_args(model.model_fields["competitor_slugs"].annotation)
    assert "jfrog" not in get_args(slug_literal) and "sonatype" in get_args(slug_literal)
    assert "jfrog" not in enums.competitor_slugs
    for bad in (verdict(scope="jfrog"), verdict(competitor_slugs=["jfrog"])):
        with pytest.raises(ValidationError):
            model(**bad)

    # A run checkpointed by an older version can hold scope "jfrog" (and the self slug). The resume
    # path (disambiguate / apply_triage) revives it as not relevant instead of raising.
    legacy = verdict(scope="jfrog", competitor_slugs=["jfrog"], reason="JFrog earnings.")
    out = revive_triage_out(enums, legacy)
    assert isinstance(out, model)
    assert out.relevant is False and out.scope != "jfrog" and out.competitor_slugs == []
    mixed = revive_triage_out(enums, verdict(competitor_slugs=["sonatype", "jfrog"]))
    assert mixed.relevant is True and mixed.competitor_slugs == ["sonatype"]
    assert revive_triage_out(enums, verdict()) == model(**verdict())  # current JSON unchanged

    doc = await doc_with_prefilter(make_document, ["JFrog"], title="JFrog Q3 results")
    await apply_triage(session, doc.id, out)
    assert (await session.get(Document, doc.id)).status == DocStatus.irrelevant


async def test_apply_triage_never_links_self(
    seeded, session, make_source, make_document, accounted_llm, enums
):
    comps = {c.slug: c for c in (await session.execute(select(Competitor))).scalars()}
    jf, sonatype = comps["jfrog"], comps["sonatype"]
    src = await make_source(competitor_id=jf.id, config={"auto_pass": True})
    doc = await doc_with_prefilter(make_document, ["JFrog", "Sonatype"], source_id=src.id)
    # a self link left by an older prefilter run is dropped too (links are replaced)
    session.add(DocumentCompetitor(document_id=doc.id, competitor_id=jf.id))
    await session.flush()
    out = make_models(enums).TriageOut(**verdict(competitor_slugs=["sonatype"]))
    await apply_triage(session, doc.id, out)
    ids = set(
        (
            await session.execute(
                select(DocumentCompetitor.competitor_id).where(
                    DocumentCompetitor.document_id == doc.id
                )
            )
        ).scalars()
    )
    assert ids == {sonatype.id}
