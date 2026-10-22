import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from app.agents import scoring_config as cfg
from app.agents.nodes import route as route_mod
from app.agents.nodes.route import route_signals
from app.agents.nodes.score import score_signal
from app.core.logging import configure_logging
from app.models import Kit
from app.models.enums import SignalType
from app.models.gold import Signal

NOW = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
HOOK = "https://hooks.slack.test/services/T000/B000/SECRETTOKEN"
FULL = "A full article body that gives the analyst real evidence to work with. " * 4
HEADLINE = "GitHub Advanced Security AI Autofix is reportedly live - Example News"


async def _sig(make_signal, make_source, make_document, competitor_factory, session, **kw):
    """Signal with a tier-2 competitor, a priority-2 kit and B-rated source docs."""
    comp = competitor_factory(tier=kw.pop("tier", 2))
    session.add(comp)
    kit = Kit(
        slug=f"kit-{comp.slug}", title="k", question="q", priority=2, owner_roles=[], keywords=[]
    )
    session.add(kit)
    await session.flush()
    docs = []
    rels = kw.pop("rels", ["B"])
    for r, body in zip(rels, kw.pop("bodies", [FULL] * len(rels)), strict=True):
        src = await make_source(reliability=r)
        docs.append(await make_document(source_id=src.id, clean_text=body))
    data = {
        "competitors": [comp],
        "kits": [kit],
        "documents": docs,
        "impact_score": 4,
        "novelty_score": 3,
        "corroboration_count": 2,
        "event_date": date(2026, 10, 10),
    }
    data.update(kw)
    return await make_signal(**data)


@pytest.fixture
def mk(session, make_signal, make_source, make_document, competitor_factory):
    async def _make(**kw):
        return await _sig(
            make_signal, make_source, make_document, competitor_factory, session, **kw
        )

    return _make


async def test_importance_formula_matches_hand_computation(session, mk):
    sig = await mk()
    # .30*.8 + .15*.6 + .15*.7 + .15*.9 + .10*.7 + .10*1.0 + .05*(1/3) = .756667
    assert await score_signal(session, sig.id, now=NOW) == 76
    await session.refresh(sig)
    assert sig.importance == 76
    bd = sig.score_breakdown
    assert bd["importance"] == 76 and bd["decay"] == 1.0 and bd["age_days"] == 0.0
    assert bd["age_from"] == "event_date"  # the freshness line names what the age counts from
    assert bd["components"]["impact"] == pytest.approx(
        {"value": 0.8, "weight": 0.30, "contribution": 0.24}
    )
    assert set(bd["components"]) == set(cfg.WEIGHTS)
    assert sig.reliability == "B" and sig.credibility == 1


async def test_credibility_rules(session, mk):
    async def cred(**kw):
        s = await mk(**kw)
        await score_signal(session, s.id, now=NOW)
        await session.refresh(s)
        return s.credibility, s.reliability

    assert await cred(corroboration_count=2, rels=["B", "A"]) == (1, "A")
    assert await cred(corroboration_count=1, rels=["B"]) == (2, "B")
    assert await cred(corroboration_count=1, rels=["C"]) == (3, "C")
    assert await cred(corroboration_count=3, rels=["C", "D"]) == (3, "C")
    assert await cred(corroboration_count=3, rels=["A"], hedged=True) == (4, "A")


async def test_decay_half_life(session, mk):
    sig = await mk()
    fresh = await score_signal(session, sig.id, now=NOW)
    old = await score_signal(session, sig.id, now=NOW + timedelta(days=3))
    await session.refresh(sig)
    assert sig.score_breakdown["decay"] == 0.5
    assert abs(old - fresh / 2) <= 1


async def test_age_counts_from_first_seen_without_an_event_date(session, mk):
    sig = await mk(event_date=None, first_seen_at=NOW - timedelta(days=2))
    await score_signal(session, sig.id, now=NOW)
    await session.refresh(sig)
    assert sig.score_breakdown["age_from"] == "first_seen"
    assert sig.score_breakdown["age_days"] == pytest.approx(2.0)


async def test_future_event_date_not_boosted(session, mk):
    sig = await mk(event_date=date(2026, 10, 20))
    imp = await score_signal(session, sig.id, now=NOW)
    await session.refresh(sig)
    assert imp == 76
    assert sig.score_breakdown["age_days"] == 0.0 and sig.score_breakdown["decay"] == 1.0


async def test_merge_preserves_existing_keys(session, mk):
    sig = await mk(score_breakdown={"error": "boom", "verification": {"ok": True}})
    await score_signal(session, sig.id, now=NOW)
    await session.refresh(sig)
    assert sig.score_breakdown["error"] == "boom"
    assert sig.score_breakdown["verification"] == {"ok": True}
    assert "components" in sig.score_breakdown


async def test_no_competitor_uses_industry_weight(session, make_signal):
    sig = await make_signal(
        scope="industry", impact_score=5, novelty_score=5, event_date=NOW.date()
    )
    await score_signal(session, sig.id, now=NOW)
    await session.refresh(sig)
    comps = sig.score_breakdown["components"]
    assert comps["competitor_tier"]["value"] == cfg.INDUSTRY_W
    assert comps["kit_priority"]["value"] == 0
    # no member documents: nothing says the evidence is thin
    assert sig.score_breakdown["evidence_depth"] == {"thin": False, "multiplier": 1.0}


def test_role_score():
    assert cfg.role_score(80, "pricing_change", "sales") == 80
    assert cfg.role_score(80, "hiring_trend", "sales") == round(80 * (0.25 + 0.75 * 0.4))
    assert cfg.role_score(80, "funding", "exec") == 80
    assert cfg.ROLE_AFFINITY["pm"]["feature_update"] == 1.0


def test_role_score_stronger_affinity():
    """Role editions: importance x (0.25 + 0.75 x affinity), so a story outside a role's
    types (the default 0.4 gives 0.55) falls well behind one inside them."""
    assert cfg.DEFAULT_AFFINITY == 0.4
    assert cfg.role_score(80, "funding", "exec") == 80
    assert cfg.role_score(80, "product_launch", "exec") == 44
    assert cfg.role_score(80, "pricing_change", "sales") == 80
    assert cfg.role_score(80, None, "pm") == 44  # unclassified: the default affinity
    assert cfg.role_score(100, "messaging_change", "sales") == 92  # 0.9 -> 0.925


def test_edition_rank_weighs_exec_risk():
    """Exec's edition ranking scales role_score by the analysis's exec risk level
    (high x1.25, low x0.8, medium or none unchanged); the other roles ignore it."""
    assert cfg.EXEC_RISK_MULTIPLIER == {"high": 1.25, "low": 0.8}
    assert cfg.edition_rank(80, "funding", "exec", "high") == pytest.approx(100)
    assert cfg.edition_rank(80, "funding", "exec", "low") == pytest.approx(64)
    assert cfg.edition_rank(80, "funding", "exec", "medium") == 80
    assert cfg.edition_rank(80, "funding", "exec", None) == 80
    assert cfg.edition_rank(80, "product_launch", "exec", "high") == pytest.approx(55)  # 44
    assert cfg.edition_rank(80, "pricing_change", "sales", "high") == 80  # Exec only


def _patch_publish(monkeypatch):
    calls = []

    async def fake_publish(t, p):
        calls.append((t, p))
        return True

    monkeypatch.setattr(route_mod, "publish", fake_publish)
    return calls


async def test_breaking_routed_once(session, make_signal, settings, monkeypatch):
    calls = _patch_publish(monkeypatch)
    s = settings.model_copy(update={"slack_webhook_url": None})
    hi = await make_signal(importance=90, status="auto")
    lo = await make_signal(importance=60, status="auto")
    await route_signals(session, [hi.id, lo.id], s)
    await route_signals(session, [hi.id, lo.id], s)
    assert len(calls) == 1
    assert calls[0][0] == "signal.breaking" and calls[0][1]["signal_id"] == hi.id
    await session.refresh(hi)
    assert "alerted_at" in hi.score_breakdown


async def test_needs_review_not_routed(session, make_signal, settings, monkeypatch):
    calls = _patch_publish(monkeypatch)
    sig = await make_signal(importance=95, status="needs_review")
    await route_signals(session, [sig.id], settings)
    assert calls == []
    await session.refresh(sig)
    assert "alerted_at" not in sig.score_breakdown


async def test_slack_failure_but_bus_ok_sets_alerted_at_and_hides_url(
    session, make_signal, settings, monkeypatch, caplog
):
    _patch_publish(monkeypatch)
    s = settings.model_copy(update={"slack_webhook_url": HOOK})
    sig = await make_signal(importance=90, status="approved")
    with respx.mock:
        respx.post(HOOK).mock(side_effect=httpx.ConnectError("down"))
        with caplog.at_level(logging.DEBUG):
            await route_signals(session, [sig.id], s)
    await session.refresh(sig)
    assert "alerted_at" in sig.score_breakdown
    assert "SECRETTOKEN" not in caplog.text


async def test_slack_posts_short_text(session, make_signal, settings, monkeypatch):
    _patch_publish(monkeypatch)
    s = settings.model_copy(update={"slack_webhook_url": HOOK})
    sig = await make_signal(importance=90, status="auto", title="Big move")
    with respx.mock:
        hook = respx.post(HOOK).mock(return_value=httpx.Response(200))
        await route_signals(session, [sig.id], s)
    assert hook.call_count == 1
    assert "Big move" in hook.calls[0].request.content.decode()


async def test_all_channels_fail_leaves_alerted_at_unset(
    session, make_signal, settings, monkeypatch
):
    async def down(t, p):
        return False

    monkeypatch.setattr(route_mod, "publish", down)
    s = settings.model_copy(update={"slack_webhook_url": HOOK})
    sig = await make_signal(importance=90, status="auto")
    with respx.mock:
        respx.post(HOOK).mock(return_value=httpx.Response(500))
        await route_signals(session, [sig.id], s)
    await session.refresh(sig)
    assert "alerted_at" not in sig.score_breakdown


async def test_slack_ok_bus_down_sets_alerted_at(session, make_signal, settings, monkeypatch):
    async def down(t, p):
        return False

    monkeypatch.setattr(route_mod, "publish", down)
    s = settings.model_copy(update={"slack_webhook_url": HOOK})
    sig = await make_signal(importance=90, status="auto")
    with respx.mock:
        respx.post(HOOK).mock(return_value=httpx.Response(200))
        await route_signals(session, [sig.id], s)
    await session.refresh(sig)
    assert "alerted_at" in sig.score_breakdown


async def test_slack_success_path_does_not_log_webhook_url(
    session, make_signal, settings, monkeypatch, caplog
):
    _patch_publish(monkeypatch)
    configure_logging()  # production logging config (root INFO)
    logging.getLogger().addHandler(caplog.handler)
    s = settings.model_copy(update={"slack_webhook_url": HOOK})
    sig = await make_signal(importance=90, status="auto")
    with respx.mock:
        respx.post(HOOK).mock(return_value=httpx.Response(200))
        with caplog.at_level(logging.DEBUG):
            await route_signals(session, [sig.id], s)
    assert "SECRETTOKEN" not in caplog.text and "hooks.slack.test" not in caplog.text


async def test_slack_text_escapes_markup(session, make_signal, settings, monkeypatch):
    _patch_publish(monkeypatch)
    s = settings.model_copy(update={"slack_webhook_url": HOOK})
    sig = await make_signal(importance=90, status="auto", title="<!channel> a & b")
    with respx.mock:
        hook = respx.post(HOOK).mock(return_value=httpx.Response(200))
        await route_signals(session, [sig.id], s)
    body = hook.calls[0].request.content.decode()
    assert "<!channel>" not in body and "&lt;!channel&gt; a &amp; b" in body


@pytest.mark.parametrize(("importance", "routed"), [(84, 0), (85, 1)])
async def test_breaking_threshold_boundary(
    session, make_signal, settings, monkeypatch, importance, routed
):
    calls = _patch_publish(monkeypatch)
    sig = await make_signal(importance=importance, status="auto")
    await route_signals(session, [sig.id], settings.model_copy(update={"slack_webhook_url": None}))
    assert len(calls) == routed


async def test_alerted_at_survives_rescore(session, mk):
    sig = await mk(score_breakdown={"alerted_at": "2026-10-10T00:00:00+00:00"})
    await score_signal(session, sig.id, now=NOW)
    await session.refresh(sig)
    assert sig.score_breakdown["alerted_at"] == "2026-10-10T00:00:00+00:00"


async def test_max_components_give_exactly_100_and_corroboration_caps(
    session, make_signal, make_source, make_document, competitor_factory
):
    comp = competitor_factory(tier=1)
    session.add(comp)
    kit = Kit(slug="k-top", title="k", question="q", priority=1, owner_roles=[], keywords=[])
    session.add(kit)
    await session.flush()
    src = await make_source(reliability="A")
    doc = await make_document(source_id=src.id, clean_text=FULL)
    sig = await make_signal(
        signal_type="acquisition",
        competitors=[comp],
        kits=[kit],
        documents=[doc],
        impact_score=5,
        novelty_score=5,
        corroboration_count=4,
        event_date=NOW.date(),
    )
    assert await score_signal(session, sig.id, now=NOW) == 100
    sig.corroboration_count = 9
    assert await score_signal(session, sig.id, now=NOW) == 100


def test_tables_pinned():
    assert set(cfg.TYPE_W) == {t.value for t in SignalType}
    assert cfg.TIER_W == {0: 0.5, 1: 1.0, 2: 0.7, 3: 0.4}
    assert cfg.KIT_PRIORITY_W == {1: 1.0, 2: 0.7, 3: 0.4}
    assert cfg.CREDIBILITY_W == {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.3, 5: 0.1, 6: 0.0}
    assert cfg.INDUSTRY_W == 0.6 and cfg.HALF_LIFE_DAYS == 3 and cfg.BREAKING_THRESHOLD == 85
    assert cfg.WEIGHTS == {
        "impact": 0.30,
        "novelty": 0.15,
        "competitor_tier": 0.15,
        "type": 0.15,
        "kit_priority": 0.10,
        "credibility": 0.10,
        "corroboration": 0.05,
    }
    assert cfg.TYPE_W["acquisition"] == 1.0 and cfg.TYPE_W["funding"] == 0.8
    assert cfg.TYPE_W["hiring_trend"] == 0.4 and cfg.TYPE_W["other"] == 0.2
    assert cfg.ROLE_AFFINITY["pm"]["deprecation"] == 0.9
    assert cfg.ROLE_AFFINITY["pmm_devrel"]["event_conference"] == 0.9
    assert cfg.ROLE_AFFINITY["exec"]["legal_regulatory"] == 0.7
    assert cfg.ROLE_AFFINITY["sales"]["partnership"] == 0.6
    assert cfg.role_score(100, "event_conference", "pmm_devrel") == 92


# ---- evidence depth ---------------------------------------------------------------


async def test_headline_only_signal_gets_the_thin_evidence_multiplier(session, mk):
    sig = await mk(rels=["B", "C"], bodies=[HEADLINE, "  " + HEADLINE[:40] + "\n "])
    # the same components as the hand computation above (.756667), times 0.6
    assert await score_signal(session, sig.id, now=NOW) == 45
    await session.refresh(sig)
    bd = sig.score_breakdown
    assert bd["evidence_depth"] == {"thin": True, "multiplier": cfg.THIN_EVIDENCE_MULTIPLIER}
    assert bd["importance"] == sig.importance == 45
    assert bd["components"]["impact"]["contribution"] == pytest.approx(0.24)  # unchanged
    # role_score re-ranks the stored (penalised) importance
    assert cfg.role_score(sig.importance, sig.signal_type, "pm") == 45


async def test_one_full_document_means_not_thin(session, mk):
    sig = await mk(rels=["B", "C"], bodies=[HEADLINE, FULL])
    assert await score_signal(session, sig.id, now=NOW) == 76
    await session.refresh(sig)
    assert sig.score_breakdown["evidence_depth"] == {"thin": False, "multiplier": 1.0}


@pytest.mark.parametrize(("chars", "thin"), [(0, True), (199, True), (200, False)])
async def test_thin_threshold_is_body_length(session, mk, chars, thin):
    sig = await mk(bodies=["x" * chars or None])
    await score_signal(session, sig.id, now=NOW)
    await session.refresh(sig)
    assert sig.score_breakdown["evidence_depth"]["thin"] is thin


async def test_summary_counts_when_there_is_no_clean_text(
    session, make_signal, make_source, make_document
):
    src = await make_source(reliability="B")
    doc = await make_document(source_id=src.id, clean_text=None, summary=FULL)
    sig = await make_signal(documents=[doc], event_date=NOW.date())
    await score_signal(session, sig.id, now=NOW)
    await session.refresh(sig)
    assert sig.score_breakdown["evidence_depth"]["thin"] is False


def test_evidence_depth_config_pinned():
    assert cfg.THIN_BODY_CHARS == 200 and cfg.THIN_EVIDENCE_MULTIPLIER == 0.6
    assert cfg.FIRST_PARTY_RELIABILITY == "A"
    assert cfg.NO_PRODUCT_IMPACT_MULTIPLIER == 0.7


async def _analyse(session, sig, *, impact: bool, stale: bool = False):
    from app.agents.analysis_schema import CitedText, ProductImpact, SignalAnalysisContent
    from app.agents.analysis_store import persist_analysis

    content = SignalAnalysisContent(
        summary=CitedText(text="S.", cites=["doc:1"]),
        what_happened=[CitedText(text="W.", cites=["doc:1"])],
        jfrog_impact=[ProductImpact(product="Xray", rationale="R.")] if impact else [],
    )
    await persist_analysis(session, sig.id, content, model="m", prompt_version="3", cost_usd=0)
    if stale:  # stage A re-ran since
        sig.analyzed_at = datetime.now(UTC) + timedelta(minutes=5)
    await session.flush()


async def test_an_analysis_rating_no_jfrog_product_lowers_the_score(session, mk):
    """When the signal's current analysis finds no JFrog product affected,
    importance is multiplied by NO_PRODUCT_IMPACT_MULTIPLIER, recorded in the breakdown."""
    irrelevant = await mk(bodies=[FULL])
    relevant = await mk(bodies=[FULL])
    stale = await mk(bodies=[FULL])
    unanalysed = await mk(bodies=[FULL])
    await _analyse(session, irrelevant, impact=False)
    await _analyse(session, relevant, impact=True)
    await _analyse(session, stale, impact=False, stale=True)

    # the hand computation above (.756667 -> 76), times 0.7
    assert await score_signal(session, irrelevant.id, now=NOW) == 53
    await session.refresh(irrelevant)
    assert irrelevant.score_breakdown["product_impact"] == {
        "none": True,
        "multiplier": cfg.NO_PRODUCT_IMPACT_MULTIPLIER,
    }
    assert irrelevant.score_breakdown["importance"] == 53
    for sig in (relevant, stale, unanalysed):  # rated a product, not current, none yet
        assert await score_signal(session, sig.id, now=NOW) == 76
        await session.refresh(sig)
        assert sig.score_breakdown["product_impact"] == {"none": False, "multiplier": 1.0}


async def test_a_short_first_party_statement_is_not_thin(session, mk):
    """A terse note on the competitor's own channel (a release, a changelog line, an
    official blog: reliability A) is authoritative, so the multiplier skips it; the same
    words from third-party headline-only items (Google News etc.) still get it."""
    official = await mk(rels=["A"], bodies=[HEADLINE])
    mixed = await mk(rels=["C", "A"], bodies=[HEADLINE, HEADLINE[:60]])
    third_party = await mk(rels=["B", "C"], bodies=[HEADLINE, HEADLINE[:60]])
    for sig in (official, mixed):
        await score_signal(session, sig.id, now=NOW)
        await session.refresh(sig)
        assert sig.score_breakdown["evidence_depth"] == {"thin": False, "multiplier": 1.0}
    await score_signal(session, third_party.id, now=NOW)
    await session.refresh(third_party)
    assert third_party.score_breakdown["evidence_depth"]["thin"] is True


async def test_corroborating_full_document_lifts_the_penalty_on_rescore(
    session, session_factory, make_source, make_document, accounted_llm
):
    """The daily graph's score node re-scores every touched signal, grown ones
    included (new | grown | reanalyze | analyzed). A full article joining a headline-only
    signal makes it grown, so its next scoring drops the multiplier."""
    from app.agents.nodes.cluster import cluster_documents
    from app.agents.runtime.context import RunCtx
    from tests.agents.fakes import fake_embedding

    ctx = RunCtx(agent_run_id=None, node="cluster", agent="cluster")

    async def doc(body, host):
        src = await make_source(reliability="B")
        text = f"{body} #cluster:autofix"
        d = await make_document(
            source_id=src.id,
            status="relevant",
            title="GitHub AI Autofix goes live",
            clean_text=text,
            canonical_url=f"https://{host}/autofix",
            published_at=NOW,
            triage={"scope": "industry"},
            embedding=fake_embedding(text, 512),
        )
        return d

    headline = await doc("Headline only", "news.google.com")
    first = await cluster_documents(session_factory, accounted_llm({}), [headline.id], ctx=ctx)
    [sid] = first.new_signal_ids
    sig = await session.get(Signal, sid)
    sig.impact_score, sig.novelty_score, sig.signal_type = 4, 3, "product_launch"
    await session.flush()
    thin = await score_signal(session, sid, now=NOW)
    await session.refresh(sig)
    assert sig.score_breakdown["evidence_depth"]["thin"] is True

    article = await doc(FULL, "github.blog")
    second = await cluster_documents(session_factory, accounted_llm({}), [article.id], ctx=ctx)
    assert second.new_signal_ids == [] and second.grown_signal_ids == [sid]
    for grown in second.grown_signal_ids:  # what the score node does with a grown signal
        lifted = await score_signal(session, grown, now=NOW)
    session.expire_all()
    sig = await session.get(Signal, sid)
    assert sig.score_breakdown["evidence_depth"] == {"thin": False, "multiplier": 1.0}
    assert lifted > thin and sig.importance == lifted


async def test_breaking_alert_never_names_the_self_competitor(
    session, seeded, make_signal, settings, monkeypatch
):
    """A stray self link is left out of the alert's competitor names."""
    calls = _patch_publish(monkeypatch)
    s = settings.model_copy(update={"slack_webhook_url": None})
    sig = await make_signal(importance=90, status="auto", competitors=["jfrog", "sonatype"])
    await route_signals(session, [sig.id], s)
    assert calls[0][1]["competitors"] == ["Sonatype"]
