"""Per-JFrog-product views: who competes with a product, the weekly pressure on it
and its signals, from the jfrog_impact of the same visible signals the competitor overlap
reads (competitor and industry scope, attributed via signal_competitors; no LLM)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.intel.products import (
    ProductCompetitor,
    ProductIntel,
    ThreatEvidence,
    WeekPressure,
    pressure_trend,
    product_competitors,
    product_pressure,
    product_signals,
    products_intel,
)
from app.intel.profile import TimelineEvent, product_overlap, week_starts
from tests.intel.test_profile import NOW, TODAY, add_product, ago, analyse, hit, new_comp, signal


@pytest.fixture
async def landscape(session, competitor_factory, make_signal):
    alpha = await new_comp(session, competitor_factory, slug="alpha", tier=1)
    bravo = await new_comp(session, competitor_factory, slug="bravo", tier=2)
    charlie = await new_comp(session, competitor_factory, slug="charlie", tier=1)
    delta = await new_comp(session, competitor_factory, slug="delta", tier=2)
    me = await new_comp(
        session, competitor_factory, slug="self", tier=0, relationship="self", is_self=True
    )
    add_product(session, alpha, "Alpha Scan", "Xray")
    add_product(session, alpha, "Alpha Repo", "Artifactory")
    add_product(session, bravo, "Bravo Scan", "Xray", "Advanced Security")
    add_product(session, charlie, "Charlie Repo", "Artifactory")
    add_product(session, delta, "Delta Scan", "Xray")
    add_product(session, me, "Self Scan", "Xray")  # never seeded; still ignored

    s = {"comps": {c.slug: c.id for c in (alpha, bravo, charlie, delta)}}
    s["a1"] = await signal(make_signal, alpha, ago(3), signal_type="product_launch", title="A1")
    await analyse(session, s["a1"], hit("Xray"), hit("Artifactory"))
    s["a2"] = await signal(make_signal, alpha, ago(30), signal_type="pricing_change")
    await analyse(session, s["a2"], hit("Xray", effect="opportunity", severity="medium"))
    s["c1"] = await signal(make_signal, charlie, ago(10), signal_type="partnership", title="C1")
    await analyse(session, s["c1"], hit("Xray", severity="low"))
    # one signal about two competitors: each gets its evidence, the pressure counts it once
    s["ab"] = await signal(
        make_signal,
        alpha,
        ago(60),
        signal_type="funding",
        title="AB",
        importance=80,
        competitors=[alpha.id, bravo.id],
    )
    await analyse(session, s["ab"], hit("Xray", effect="opportunity", severity="low"))
    s["n1"] = await signal(make_signal, charlie, ago(11))
    await analyse(session, s["n1"], hit("Xray", effect="neutral", severity="medium"))
    # an industry-scope signal linked to a competitor counts, as it does for overlap
    s["ind"] = await signal(
        make_signal, alpha, ago(2), scope="industry", signal_type="industry_trend", title="IND"
    )
    await analyse(session, s["ind"], hit("Xray"))
    s["bi"] = await signal(make_signal, bravo, ago(15), scope="industry", title="BI")
    await analyse(session, s["bi"], hit("Advanced Security", severity="medium"))

    # never counted
    hidden = [
        await signal(make_signal, me, ago(2)),  # about JFrog only
        await signal(make_signal, alpha, ago(2), scope="industry", competitors=[]),  # nobody's
        await signal(make_signal, alpha, ago(2), scope="jfrog"),
        await signal(make_signal, alpha, ago(2), status="needs_review"),
        await signal(make_signal, alpha, ago(2), status="suppressed"),
        await signal(make_signal, alpha, ago(200)),  # outside 180 days
        await signal(
            make_signal, alpha, ago(2), analyzed_at=datetime.now(UTC) + timedelta(hours=1)
        ),  # stale analysis
    ]
    for h in hidden:
        await analyse(session, h, hit("Xray"))
    gutted = await signal(make_signal, alpha, ago(2))
    await analyse(session, gutted, hit("Xray"), complete=False)  # summary-only
    return s


async def test_product_competitors_from_seed_and_evidence(session, landscape):
    s = landscape
    got = await product_competitors(session, "Xray", now=NOW)
    assert got == [
        # a1 3 + a2 2 + ab 1 + ind 3 (any effect); threat weight 6 -> high; newest move ind
        ProductCompetitor(
            "alpha",
            ["Alpha Scan"],
            "high",
            TimelineEvent(s["ind"].id, ago(2), "Market", 50, "IND"),
            9,
            6,
            (  # each threat signal with its weight and severity, newest first
                ThreatEvidence(TimelineEvent(s["ind"].id, ago(2), "Market", 50, "IND"), 3, "high"),
                ThreatEvidence(TimelineEvent(s["a1"].id, ago(3), "Launches", 50, "A1"), 3, "high"),
            ),
        ),
        # evidence only (no seeded Xray counterpart): 1 + 2 (neutral) = 3, threat 1 -> low
        ProductCompetitor(
            "charlie",
            [],
            "low",
            TimelineEvent(s["c1"].id, ago(10), "Partners", 50, "C1"),
            3,
            1,
            (ThreatEvidence(TimelineEvent(s["c1"].id, ago(10), "Partners", 50, "C1"), 1, "low"),),
        ),
        ProductCompetitor(
            "bravo",
            ["Bravo Scan"],
            "none",  # an opportunity is no threat
            TimelineEvent(s["ab"].id, ago(60), "Money", 80, "AB"),
            1,
        ),
        ProductCompetitor("delta", ["Delta Scan"], "none", None, 0),  # seeded, no evidence
    ]

    narrow = await product_competitors(session, "Xray", days=20, now=NOW)
    assert [(c.competitor_slug, c.evidence, c.threat_level) for c in narrow] == [
        ("alpha", 6, "high"),
        ("charlie", 3, "low"),
        ("bravo", 0, "none"),
        ("delta", 0, "none"),
    ]
    assert narrow[2].last_move is None

    [bravo] = await product_competitors(session, "Advanced Security", now=NOW)
    assert (bravo.competitor_slug, bravo.evidence, bravo.threat_level) == ("bravo", 2, "low")
    assert bravo.last_move is not None and bravo.last_move.signal_id == s["bi"].id

    nobody = await product_competitors(session, "Connect", now=NOW)
    assert nobody == []


async def test_product_evidence_reconciles_with_competitor_overlap(session, landscape):
    """One signal set. A seeded product's overlap evidence is the sum, over its JFrog
    counterparts, of that competitor's evidence in the product views."""
    by_product = {
        p: {c.competitor_slug: c.evidence for c in await product_competitors(session, p, now=NOW)}
        for p in JFROG_PRODUCTS
    }
    checked = 0
    for slug, cid in landscape["comps"].items():
        for row in await product_overlap(session, cid, now=NOW):
            assert row.evidence == sum(by_product[q].get(slug, 0) for q in row.jfrog_products), (
                slug,
                row.product,
            )
            checked += 1
    assert checked == 5
    alpha_scan = (await product_overlap(session, landscape["comps"]["alpha"], now=NOW))[0]
    assert (alpha_scan.product, alpha_scan.evidence) == ("Alpha Scan", 9)
    assert landscape["ind"].id in alpha_scan.signal_ids  # the industry signal, in both views
    bravo_scan = (await product_overlap(session, landscape["comps"]["bravo"], now=NOW))[0]
    assert bravo_scan.evidence == 3  # Xray 1 (ab) + Advanced Security 2 (bi)


async def test_product_pressure_weekly(session, landscape):
    weeks = await product_pressure(session, "Xray", now=NOW)
    assert [w.week_start for w in weeks] == week_starts(TODAY, 13)
    by_week = {w.week_start: (w.threat, w.opportunity) for w in weeks}
    assert by_week[date(2026, 10, 14)] == (6.0, 0.0)  # a1 (Oct 17) + ind (Oct 18)
    assert by_week[date(2026, 10, 7)] == (1.0, 0.0)  # c1 (Oct 10); n1 (Oct 9) is neutral
    assert by_week[date(2026, 9, 16)] == (0.0, 2.0)  # a2 (Sep 20)
    assert by_week[date(2026, 8, 19)] == (0.0, 1.0)  # ab (Aug 21)
    assert sum(t for t, _ in by_week.values()) == 7.0
    assert sum(o for _, o in by_week.values()) == 3.0  # a2 2 + ab 1, ab once for two rivals
    assert all(isinstance(w, WeekPressure) for w in weeks)

    quiet = await product_pressure(session, "Connect", weeks=2, now=NOW)
    assert quiet == [
        WeekPressure(TODAY - timedelta(days=13), 0.0, 0.0),
        WeekPressure(ago(6), 0.0, 0.0),
    ]


async def test_product_signals(session, landscape):
    s = landscape
    assert await product_signals(session, "Xray", now=NOW) == [
        s["ind"].id,
        s["a1"].id,
        s["c1"].id,
        s["n1"].id,
        s["a2"].id,
        s["ab"].id,
    ]  # newest first; each once
    assert await product_signals(session, "Artifactory", now=NOW) == [s["a1"].id]
    assert await product_signals(session, "Xray", days=20, now=NOW) == [
        s["ind"].id,
        s["a1"].id,
        s["c1"].id,
        s["n1"].id,
    ]
    assert await product_signals(session, "JFrog ML", now=NOW) == []


async def test_unknown_product_raises(session):
    for fn in (product_competitors, product_pressure, product_signals):
        with pytest.raises(ValueError, match="Nexus"):
            await fn(session, "Nexus", now=NOW)
    with pytest.raises(ValueError, match="Nexus"):
        await products_intel(session, ["Xray", "Nexus"], now=NOW)


# ---- the batch view (the products list and the product deep dive) ----------------


async def test_products_intel_batch_matches_the_single_views(session, landscape, sql_counter):
    """Every product's competitors, pressure and signals from ONE signal scan
    (three statements, however many products; the collected-since day is one), equal to
    the single views."""
    sql_counter.clear()
    every = await products_intel(session, JFROG_PRODUCTS, now=NOW)
    assert len(sql_counter) == 3
    assert list(every) == list(JFROG_PRODUCTS)
    for product in JFROG_PRODUCTS:
        got = every[product]
        assert isinstance(got, ProductIntel) and got.product == product
        assert got.competitors == await product_competitors(session, product, now=NOW)
        assert got.pressure == await product_pressure(session, product, now=NOW)
        assert got.signal_ids == await product_signals(session, product, now=NOW)
        assert got.trend == pressure_trend(got.pressure)
    sql_counter.clear()
    assert (await products_intel(session, ["Xray"], now=NOW))["Xray"] == every["Xray"]
    assert len(sql_counter) == 3

    s = landscape
    events = [(e.competitor_slug, e.event.signal_id) for e in every["Xray"].events]
    assert events == [  # oldest first; a signal about two competitors is in both lanes
        ("alpha", s["ab"].id),
        ("bravo", s["ab"].id),
        ("alpha", s["a2"].id),
        ("charlie", s["n1"].id),
        ("charlie", s["c1"].id),
        ("alpha", s["a1"].id),
        ("alpha", s["ind"].id),
    ]
    assert every["Xray"].events[-1].event == TimelineEvent(s["ind"].id, ago(2), "Market", 50, "IND")
    assert every["Connect"].events == [] and every["Connect"].trend == "none"
    assert await products_intel(session, [], now=NOW) == {}


async def test_pressure_waits_for_a_baseline_after_collection_began(
    session, landscape, make_document
):
    """The pressure weeks start at the week holding the first document's day, and the
    pressure word is no trend before the 9 weeks it compares with were all collected:
    `baseline` is False, and threat in the collected weeks reads "rising" only as new
    pressure (the readers say "no baseline yet")."""
    await make_document(created_at=datetime(2026, 9, 30, 9, tzinfo=UTC))  # 20 days ago
    view = await products_intel(session, ["Xray", "Connect"], now=NOW)
    xray = view["Xray"]
    served = [date(2026, 9, 30), date(2026, 10, 7), date(2026, 10, 14)]
    assert [w.week_start for w in xray.pressure] == served
    assert (xray.baseline, xray.trend, xray.collected_since) == (False, "rising", served[0])
    assert xray.pressure == await product_pressure(session, "Xray", now=NOW)
    assert (view["Connect"].trend, view["Connect"].baseline) == ("none", False)
    assert pressure_trend(xray.pressure, baseline=False) == "rising"


async def test_a_full_collected_baseline_keeps_the_pressure_word(session, landscape, make_signal):
    """No document filed (or one before the 13 weeks), and threat in the 9 weeks compared
    with: the rule is as before."""
    from types import SimpleNamespace

    alpha = SimpleNamespace(id=landscape["comps"]["alpha"])
    before = await signal(make_signal, alpha, ago(50), title="An older threat")
    await analyse(session, before, hit("Xray", severity="low"))
    xray = (await products_intel(session, ["Xray"], now=NOW))["Xray"]
    assert len(xray.pressure) == 13 and xray.baseline is True and xray.trend == "rising"


async def test_an_empty_baseline_window_is_no_baseline_never_a_rise(session, landscape):
    """Everything was collected, but the 9 weeks the pressure compares with name no
    threat. As for the competitor momentum, that is no baseline yet (`baseline` False: the
    readers say "no baseline yet"), never a "rising" trend read off nothing. A product with
    no threat at all has nothing to compare: "none", its baseline kept."""
    view = await products_intel(session, ["Xray", "Connect"], now=NOW)
    xray = view["Xray"]
    assert len(xray.pressure) == 13
    assert not any(w.threat for w in xray.pressure[:9])
    assert any(w.threat for w in xray.pressure[9:])
    assert xray.baseline is False
    assert (view["Connect"].trend, view["Connect"].baseline) == ("none", True)


def test_pressure_trend_compares_the_last_four_weeks_with_the_nine_before():
    starts = week_starts(TODAY, 13)

    def weeks(threat: list[float], opportunity: float = 0.0) -> list[WeekPressure]:
        return [WeekPressure(s, t, opportunity) for s, t in zip(starts, threat, strict=True)]

    assert pressure_trend(weeks([0.0] * 13)) == "none"
    assert pressure_trend(weeks([0.0] * 13, opportunity=3.0)) == "none"  # no threat at all
    assert pressure_trend(weeks([0.0] * 9 + [3.0, 0.0, 0.0, 0.0])) == "rising"  # new pressure
    assert pressure_trend(weeks([1.0] * 13)) == "steady"
    assert pressure_trend(weeks([1.0] * 9 + [1.25] * 4)) == "rising"  # from 1.25x
    assert pressure_trend(weeks([1.0] * 9 + [1.2] * 4)) == "steady"
    assert pressure_trend(weeks([1.0] * 9 + [0.8] * 4)) == "easing"  # at or under 0.8x
    assert pressure_trend(weeks([3.0] * 9 + [0.0] * 4)) == "easing"
