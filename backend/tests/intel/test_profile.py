"""Deterministic competitor intelligence: overlap, momentum, threat, timeline and
share of voice, from crafted signals and analyses (no LLM)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.agents.analysis_schema import CitedText, ProductImpact, SignalAnalysisContent
from app.agents.analysis_store import persist_analysis
from app.core.config import get_settings
from app.intel.hiring import board_activity
from app.intel.profile import (
    LANE_ORDER,
    LANES,
    CompetitorIntel,
    OverviewIntel,
    ProductOverlap,
    SeriesPoint,
    TimelineEvent,
    WeekPoint,
    collected_since,
    competitor_intel,
    has_baseline,
    lane_of,
    measured_weeks,
    momentum_series,
    overview_intel,
    product_overlap,
    share_of_voice,
    threat,
    timeline_events,
    week_starts,
)
from app.models import CompetitorProduct, DocumentCompetitor, JobPosting
from app.models.enums import SignalType

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)  # 12:00 in Asia/Jerusalem (UTC+3)
TODAY = date(2026, 10, 20)


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def at(day: date, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


async def new_comp(session, competitor_factory, **fields):
    comp = competitor_factory(**fields)
    session.add(comp)
    await session.flush()
    return comp


async def signal(make_signal, comp, day: date, *, hour: int = 9, minute: int = 0, **kw):
    seen = at(day, hour, minute)  # analyzed_at defaults to min(seen, the real clock)
    comps = kw.pop("competitors", [comp.id])
    return await make_signal(competitors=comps, first_seen_at=seen, **kw)


def hit(product: str, effect: str = "threat", severity: str = "high") -> ProductImpact:
    return ProductImpact(product=product, effect=effect, severity=severity, rationale="r")


async def analyse(session, sig, *impacts: ProductImpact, complete: bool = True) -> None:
    content = SignalAnalysisContent(
        summary=CitedText(text="s", cites=["doc:1"]),
        what_happened=[CitedText(text="w", cites=["doc:1"])] if complete else [],
        jfrog_impact=list(impacts),
    )
    await persist_analysis(session, sig.id, content, model="m", prompt_version="1", cost_usd=0)


def add_product(session, comp, name: str, *jfrog: str, category: str = "Cat") -> None:
    session.add(
        CompetitorProduct(
            competitor_id=comp.id,
            name=name,
            category=category,
            url=None,
            jfrog_products=list(jfrog),
        )
    )


async def board(
    session, make_source, comp, *first_seen_days_ago: int, fetched: bool = True
) -> None:
    """A job board with postings; it was listed in full when its oldest posting was first
    seen (postings come from one), unless `fetched` is False (only a board
    listed in full counts for the threat)."""
    listed = at(ago(max(first_seen_days_ago, default=0)))
    src = await make_source(
        kind="job_board",
        competitor_id=comp.id,
        config={"ats": "lever"},
        last_fetched_at=NOW if fetched else None,
        first_complete_fetch_at=listed if fetched else None,
        last_complete_fetch_at=NOW if fetched else None,  # listed in full today
    )
    for n, days in enumerate(first_seen_days_ago):
        session.add(
            JobPosting(
                source_id=src.id,
                competitor_id=comp.id,
                ats="lever",
                external_id=f"{comp.slug}-{n}",
                title=f"Role {n}",
                department="Engineering",
                seniority="Mid",
                content_hash=f"{comp.slug}-h{n}",
                first_seen_at=at(ago(days)),
            )
        )
    await session.flush()


# ---- lanes --------------------------------------------------------------------------------


def test_lane_mapping_covers_every_signal_type():
    assert set(LANES) == {t.value for t in SignalType}
    assert set(LANES.values()) == set(LANE_ORDER)
    assert LANE_ORDER == (
        "Launches",
        "Pricing",
        "Partners",
        "Money",
        "People",
        "Security",
        "Market",
    )
    expected = {
        "Launches": {"product_launch", "feature_update", "deprecation"},
        "Pricing": {"pricing_change"},
        "Partners": {"partnership", "customer_win_loss"},
        "Money": {"funding", "acquisition", "earnings_financials"},
        "People": {"leadership_change", "hiring_trend"},
        "Security": {"security_incident"},
        "Market": {
            "analyst_recognition",
            "community_buzz",
            "event_conference",
            "messaging_change",
            "industry_trend",
            "standard_spec",
            "legal_regulatory",
            "other",
        },
    }
    for lane, types in expected.items():
        assert {t for t, ln in LANES.items() if ln == lane} == types
    assert lane_of(None) == "Market"  # an unclassified signal lands with "other"


def test_week_starts_are_whole_local_weeks_ending_today():
    starts = week_starts(TODAY, 13)
    assert len(starts) == 13
    assert starts[0] == date(2026, 7, 22) and starts[-1] == date(2026, 10, 14)
    assert all(b - a == timedelta(days=7) for a, b in zip(starts, starts[1:], strict=False))
    assert starts[-1] + timedelta(days=6) == TODAY


# ---- product overlap ----------------------------------------------------------------------


async def test_overlap_levels_follow_the_thresholds(session, competitor_factory, make_signal):
    """Evidence = sum of severity weights (high 3, medium 2, low 1) of the jfrog_impact
    entries, any effect, naming one of the product's JFrog counterparts; levels high >= 6,
    medium >= 3, low >= 1, none = 0."""
    comp = await new_comp(session, competitor_factory)
    add_product(session, comp, "Alpha Repo", "Artifactory", category="Artifact repository")
    add_product(session, comp, "Beta Dist", "Distribution")
    add_product(session, comp, "Gamma Scan", "Xray", "Advanced Security")
    add_product(session, comp, "Delta Gate", "Curation")
    add_product(session, comp, "Epsilon ML", "JFrog ML")
    s1 = await signal(make_signal, comp, ago(5))
    await analyse(
        session,
        s1,
        hit("Artifactory"),  # 3
        hit("Xray", severity="medium"),  # 2
        hit("Advanced Security", effect="opportunity", severity="low"),  # 1
    )
    s2 = await signal(make_signal, comp, ago(40))
    await analyse(
        session,
        s2,
        hit("Artifactory", effect="opportunity"),  # 3: any effect counts
        hit("Distribution"),  # 3
        hit("Curation", effect="neutral", severity="medium"),  # 2
    )
    s3 = await signal(make_signal, comp, ago(179))  # the window's first day
    await analyse(session, s3, hit("Distribution", effect="opportunity", severity="medium"))
    await analyse(session, await signal(make_signal, comp, ago(10)))  # rates no product
    await signal(make_signal, comp, ago(12))  # no analysis at all
    await session.flush()

    got = await product_overlap(session, comp.id, now=NOW)
    alpha = ProductOverlap(
        "Alpha Repo", "Artifact repository", ["Artifactory"], "high", 6, [s1.id, s2.id]
    )
    assert got == [  # seed order
        alpha,
        ProductOverlap("Beta Dist", "Cat", ["Distribution"], "medium", 5, [s2.id, s3.id]),
        ProductOverlap("Gamma Scan", "Cat", ["Xray", "Advanced Security"], "medium", 3, [s1.id]),
        ProductOverlap("Delta Gate", "Cat", ["Curation"], "low", 2, [s2.id]),
        ProductOverlap("Epsilon ML", "Cat", ["JFrog ML"], "none", 0, []),
    ]

    # a 180-day-old signal is outside the window; days narrows it
    s_old = await signal(make_signal, comp, ago(180))
    await analyse(session, s_old, hit("JFrog ML"))
    assert (await product_overlap(session, comp.id, now=NOW))[-1].evidence == 0
    narrow = await product_overlap(session, comp.id, days=30, now=NOW)  # s1 only
    assert [(p.evidence, p.signal_ids) for p in narrow] == [
        (3, [s1.id]),
        (0, []),
        (3, [s1.id]),
        (0, []),
        (0, []),
    ]
    assert [p.level for p in narrow] == ["medium", "none", "medium", "none", "none"]


async def test_overlap_without_products_or_for_self_is_empty(
    session, competitor_factory, make_signal
):
    quiet = await new_comp(session, competitor_factory)
    s = await signal(make_signal, quiet, ago(3))
    await analyse(session, s, hit("Artifactory"))
    assert await product_overlap(session, quiet.id, now=NOW) == []  # nothing seeded
    me = await new_comp(session, competitor_factory, tier=0, relationship="self", is_self=True)
    add_product(session, me, "Own Repo", "Artifactory")  # never in the seed; still ignored
    s = await signal(make_signal, me, ago(3))
    await analyse(session, s, hit("Artifactory"))
    assert await product_overlap(session, me.id, now=NOW) == []
    assert await product_overlap(session, 999_999, now=NOW) == []


# ---- momentum -----------------------------------------------------------------------------


async def test_momentum_weekly_buckets_in_local_weeks(session, competitor_factory, make_signal):
    comp = await new_comp(session, competitor_factory)
    # 22:30 UTC on the 13th is 01:30 on the 14th in Jerusalem: the last week (Oct 14-20)
    await signal(make_signal, comp, date(2026, 10, 13), hour=22, minute=30, importance=10)
    # 20:00 UTC on the 13th is 23:00 on the 13th: the week before (Oct 7-13)
    await signal(make_signal, comp, date(2026, 10, 13), hour=20, importance=20)
    # a stated event date counts on that day ...
    await signal(make_signal, comp, ago(2), importance=30, event_date=date(2026, 8, 20))
    # ... but a future one counts on the day the signal was first seen
    await signal(make_signal, comp, ago(4), importance=5, event_date=date(2026, 11, 30))
    await signal(make_signal, comp, date(2026, 7, 21), importance=99)  # before the window
    await signal(make_signal, comp, date(2026, 7, 22), importance=7)  # its first day
    await signal(make_signal, comp, ago(5), importance=1000, status="needs_review")

    points = await momentum_series(session, comp.id, now=NOW)
    assert [p.week_start for p in points] == week_starts(TODAY, 13)
    assert all(isinstance(p, WeekPoint) and isinstance(p.value, float) for p in points)
    values = {p.week_start: p.value for p in points}
    assert values[date(2026, 10, 14)] == 15.0
    assert values[date(2026, 10, 7)] == 20.0
    assert values[date(2026, 8, 19)] == 30.0
    assert values[date(2026, 7, 22)] == 7.0
    assert sum(values.values()) == 72.0

    short = await momentum_series(session, comp.id, weeks=2, now=NOW)
    assert short == [WeekPoint(date(2026, 10, 7), 20.0), WeekPoint(date(2026, 10, 14), 15.0)]

    me = await new_comp(session, competitor_factory, tier=0, relationship="self", is_self=True)
    await signal(make_signal, me, ago(1))
    assert await momentum_series(session, me.id, now=NOW) == []  # no JFrog series


# ---- timeline -----------------------------------------------------------------------------


async def test_timeline_events_by_lane(session, competitor_factory, make_signal):
    comp = await new_comp(session, competitor_factory)
    launch = await signal(make_signal, comp, ago(100), signal_type="product_launch", title="L")
    price = await signal(make_signal, comp, ago(20), signal_type="pricing_change", importance=70)
    untyped = await signal(make_signal, comp, ago(20), signal_type=None, importance=12)
    money = await signal(make_signal, comp, ago(1), signal_type="funding", title="M")
    await signal(make_signal, comp, ago(180), signal_type="acquisition")  # outside 180 days
    await signal(make_signal, comp, ago(3), signal_type="funding", status="suppressed")
    await signal(make_signal, comp, ago(3), signal_type="funding", scope="jfrog")

    got = await timeline_events(session, comp.id, now=NOW)
    assert got == [
        TimelineEvent(launch.id, ago(100), "Launches", 50, "L"),
        TimelineEvent(price.id, ago(20), "Pricing", 70, price.title),
        TimelineEvent(untyped.id, ago(20), "Market", 12, untyped.title),
        TimelineEvent(money.id, ago(1), "Money", 50, "M"),
    ]
    assert [e.signal_id for e in await timeline_events(session, comp.id, days=30, now=NOW)] == [
        price.id,
        untyped.id,
        money.id,
    ]
    me = await new_comp(session, competitor_factory, tier=0, relationship="self", is_self=True)
    await signal(make_signal, me, ago(1))
    assert await timeline_events(session, me.id, now=NOW) == []


# ---- threat -------------------------------------------------------------------------------


async def test_threat_high(session, competitor_factory, make_signal, make_source):
    comp = await new_comp(session, competitor_factory)
    for days in (3, 20):  # the last 4 weeks: 60 importance -> 15 a week
        s = await signal(make_signal, comp, ago(days), importance=30)
        await analyse(
            session,
            s,
            hit("Artifactory"),
            hit("Curation"),
            hit("Xray", effect="opportunity"),  # not a threat: no overlap weight
        )
    for days in (40, 50, 60):  # the 9 weeks before: 135 -> 15 a week
        await signal(make_signal, comp, ago(days), importance=45)
    await board(session, make_source, comp, 45, 45, 45, 45, 10)  # 4 open 30 days ago, 5 now

    t = await threat(session, comp.id, now=NOW)
    assert (t.level, t.overlap, t.momentum_trend, t.hiring_trend) == ("high", 12.0, 1.0, 0.25)
    assert t.score == pytest.approx(15.0)  # 12 x 1.00 x 1.25
    for driver in ("score 15.0", "overlap 12", "momentum trend 1.00", "+25%", "5 open roles"):
        assert driver in t.explanation, (driver, t.explanation)

    # the thresholds are config: raise "high" above the score and it reads medium
    settings = get_settings()
    original = settings.threat_high_score
    try:
        settings.threat_high_score = 20.0
        assert (await threat(session, comp.id, now=NOW)).level == "medium"
    finally:
        settings.threat_high_score = original


async def test_threat_medium_and_momentum_cap(session, competitor_factory, make_signal):
    comp = await new_comp(session, competitor_factory)
    first = await signal(make_signal, comp, ago(2), importance=20)
    await analyse(session, first, hit("Xray", severity="medium"))  # overlap 2
    for days in (9, 16):
        await signal(make_signal, comp, ago(days), importance=20)
    await signal(make_signal, comp, ago(60), importance=1)  # a thin baseline: 1/9 a week
    # 15 a week now vs 0.11 before: the trend is capped at 3.0, used as x2.0
    t = await threat(session, comp.id, now=NOW)
    assert (t.level, t.overlap, t.momentum_trend, t.hiring_trend) == ("medium", 2.0, 3.0, None)
    assert t.score == pytest.approx(4.0)  # exactly the medium threshold
    assert "no public job board found" in t.explanation and "(capped)" in t.explanation


async def test_threat_without_a_momentum_baseline(session, competitor_factory, make_signal):
    """Nothing in the 9 weeks before the last 4 is no baseline, not a spike: the trend is
    None, its factor 1.0, and the explanation says so."""
    comp = await new_comp(session, competitor_factory)
    first = await signal(make_signal, comp, ago(2), importance=20)
    await analyse(session, first, hit("Xray", severity="medium"))  # overlap 2
    for days in (9, 16):
        await signal(make_signal, comp, ago(days), importance=20)
    t = await threat(session, comp.id, now=NOW)
    assert (t.level, t.overlap, t.momentum_trend, t.score) == ("low", 2.0, None, 2.0)
    assert "no baseline yet" in t.explanation and "× momentum 1.00 ×" in t.explanation


async def test_threat_low(session, competitor_factory, make_signal):
    comp = await new_comp(session, competitor_factory)
    for days in (5, 50, 90):
        s = await signal(make_signal, comp, ago(days), importance=40)
        await analyse(session, s, hit("Artifactory", effect="opportunity"))
    t = await threat(session, comp.id, now=NOW)
    assert (t.level, t.overlap, t.hiring_trend, t.score) == ("low", 0.0, None, 0.0)


async def test_threat_insufficient_without_signals_or_board(
    session, competitor_factory, make_signal, make_source
):
    """Fewer than 3 visible signals in 180 days and no board."""
    comp = await new_comp(session, competitor_factory)
    t = await threat(session, comp.id, now=NOW)
    assert t.level == "insufficient" and t.hiring_trend is None
    # nothing reads as a measured 0 or a flat 1.0
    assert (t.overlap, t.momentum_trend, t.score) == (None, None, None)
    assert "0 visible signals" in t.explanation and "3 needed" in t.explanation

    for days in (5, 60):
        s = await signal(make_signal, comp, ago(days))
        await analyse(session, s, hit("Artifactory", effect="opportunity"))
    for status in ("needs_review", "suppressed", "pending", "rejected"):  # not counted
        await signal(make_signal, comp, ago(7), status=status)
    await signal(make_signal, comp, ago(7), scope="jfrog")
    await signal(make_signal, comp, ago(181))
    t = await threat(session, comp.id, now=NOW)
    assert t.level == "insufficient" and "2 visible signals" in t.explanation
    assert (t.overlap, t.momentum_trend, t.score) == (None, None, None)

    # a job board makes it scorable; with under 30 days of history it adds no hiring factor
    await board(session, make_source, comp, 5, 5, 5)
    t = await threat(session, comp.id, now=NOW)
    assert t.level == "low" and t.overlap == 0.0 and t.hiring_trend is None
    assert "under 30 days" in t.explanation

    me = await new_comp(session, competitor_factory, tier=0, relationship="self", is_self=True)
    for days in (1, 2, 3, 4):
        s = await signal(make_signal, me, ago(days))
        await analyse(session, s, hit("Artifactory"))
    t = await threat(session, me.id, now=NOW)
    assert t.level == "insufficient"  # JFrog is never scored
    assert (t.overlap, t.momentum_trend, t.hiring_trend, t.score) == (None, None, None, None)
    unknown = await threat(session, 999_999, now=NOW)
    assert (unknown.level, unknown.overlap, unknown.momentum_trend) == ("insufficient", None, None)


# ---- share of voice -----------------------------------------------------------------------


async def test_share_of_voice_counts(session, competitor_factory, make_signal, make_document):
    alpha = await new_comp(session, competitor_factory, slug="alpha")
    bravo = await new_comp(session, competitor_factory, slug="bravo")
    me = await new_comp(session, competitor_factory, tier=0, relationship="self", is_self=True)

    async def doc(comp, day: date | None, *, created: date, **kw):
        d = await make_document(published_at=at(day) if day else None, created_at=at(created), **kw)
        session.add(DocumentCompetitor(document_id=d.id, competitor_id=comp.id))
        await session.flush()

    await make_document(created_at=at(ago(40)))  # collecting since before the 4 weeks
    await doc(alpha, ago(1), created=ago(1), status="relevant")
    await doc(alpha, None, created=ago(2), status="relevant")  # undated: the day it was filed
    await doc(alpha, ago(3), created=ago(3), status="irrelevant")
    await doc(alpha, ago(3), created=ago(3), status="duplicate")
    await doc(alpha, ago(3), created=ago(3), status="relevant", scope_hint="jfrog")  # self news
    await doc(alpha, ago(30), created=ago(1), status="relevant")  # before the 4 weeks
    await doc(bravo, ago(8), created=ago(8), status="relevant")
    await doc(me, ago(1), created=ago(1), status="relevant")
    await signal(make_signal, alpha, ago(2))
    await signal(make_signal, alpha, ago(2), status="needs_review")
    await signal(make_signal, bravo, ago(15))
    await signal(make_signal, bravo, ago(15), scope="jfrog")

    got = await share_of_voice(session, [bravo.id, alpha.id, me.id], weeks=4, now=NOW)
    weeks = week_starts(TODAY, 4)
    counts = {(p.week_start, p.competitor_slug): (p.documents, p.signals) for p in got}
    assert [(p.week_start, p.competitor_slug) for p in got] == [
        (w, slug) for w in weeks for slug in ("alpha", "bravo")
    ]  # every week for every competitor, self skipped
    assert all(isinstance(p, SeriesPoint) for p in got)
    assert counts[(date(2026, 10, 14), "alpha")] == (2, 1)
    assert counts[(date(2026, 10, 7), "bravo")] == (1, 0)
    assert counts[(date(2026, 9, 30), "bravo")] == (0, 1)
    assert sum(d for d, _ in counts.values()) == 3 and sum(s for _, s in counts.values()) == 2
    assert await share_of_voice(session, [me.id], now=NOW) == []
    assert await share_of_voice(session, [], now=NOW) == []


# ---- collected since ---------------------------------------------------------------


async def collecting_since(make_document, days: int) -> None:
    """FrogEye's first document, filed `days` ago: collection began that day."""
    await make_document(created_at=at(ago(days)))


@pytest.fixture
async def young_deployment(session, competitor_factory, make_signal, make_document):
    """Collection began 20 days ago (30 Sep). Alpha has a signal in each of the last three
    weeks and an older one dated 21 Aug, before FrogEye collected anything."""
    await collecting_since(make_document, 20)
    alpha = await new_comp(session, competitor_factory, slug="alpha")
    bravo = await new_comp(session, competitor_factory, slug="bravo")
    first = await signal(make_signal, alpha, ago(2), importance=20)
    await analyse(session, first, hit("Xray"))  # overlap 3
    for days in (9, 16, 60):
        await signal(make_signal, alpha, ago(days), importance=20)
    await signal(make_signal, bravo, ago(3), importance=10)
    return {"alpha": alpha, "bravo": bravo}


async def test_weeks_before_collection_began_are_never_served(session, young_deployment):
    """A weekly series starts at the week holding the first document's day. The weeks
    before it were never collected, so they are not served: no quiet zero weeks, and an
    event dated before collection (21 Aug) is in no served week. The momentum trend needs
    the 9 weeks it compares with all collected, so here it has no baseline yet (with the
    unserved weeks read as zeros it would be a capped 3.0 spike)."""
    alpha = young_deployment["alpha"]
    served = [date(2026, 9, 30), date(2026, 10, 7), date(2026, 10, 14)]
    momentum = await momentum_series(session, alpha.id, now=NOW)
    assert momentum == [WeekPoint(w, 20.0) for w in served]

    t = await threat(session, alpha.id, now=NOW)
    assert (t.level, t.overlap, t.momentum_trend, t.score) == ("low", 3.0, None, 3.0)
    assert "no baseline yet" in t.explanation and "since 30 Sep 2026" in t.explanation
    assert "× momentum 1.00 ×" in t.explanation

    bundle = await competitor_intel(session, alpha.id, now=NOW)
    assert (bundle.momentum, bundle.threat, bundle.collected_since) == (
        momentum,
        t,
        date(2026, 9, 30),
    )
    assert len(bundle.timeline) == 4  # the swimlane is events, not weeks: all 180 days
    row = (await overview_intel(session, [alpha.id], now=NOW))[alpha.id]
    assert (row.momentum, row.threat, row.collected_since) == (momentum, t, date(2026, 9, 30))

    voice = await share_of_voice(session, [alpha.id, young_deployment["bravo"].id], now=NOW)
    assert sorted({p.week_start for p in voice}) == served
    assert {(p.week_start, p.competitor_slug): p.signals for p in voice}[
        (date(2026, 10, 14), "bravo")
    ] == 1


async def test_a_full_baseline_after_collection_began_keeps_the_trend(
    session, competitor_factory, make_signal, make_document
):
    """Once the 13 weeks a trend spans were all collected (the first week starts on or
    after the collected-since day) the series and the trend are as before."""
    await collecting_since(make_document, 90)  # 22 Jul: the first day of the 13 weeks
    comp = await new_comp(session, competitor_factory)
    first = await signal(make_signal, comp, ago(2), importance=20)
    await analyse(session, first, hit("Xray", severity="medium"))
    for days in (9, 16):
        await signal(make_signal, comp, ago(days), importance=20)
    await signal(make_signal, comp, ago(60), importance=1)
    t = await threat(session, comp.id, now=NOW)
    assert t.momentum_trend == 3.0  # capped, as in test_threat_medium_and_momentum_cap
    assert len(await momentum_series(session, comp.id, now=NOW)) == 13


def test_collected_weeks_and_the_baseline_rule():
    starts = week_starts(TODAY, 13)
    assert measured_weeks(starts, None) == starts  # no document yet: nothing bounds them
    assert measured_weeks(starts, starts[-1] + timedelta(days=6)) == starts[-1:]
    assert measured_weeks(starts, starts[3] + timedelta(days=1)) == starts[3:]
    assert has_baseline(starts, None) and has_baseline(starts, starts[0])
    assert not has_baseline(starts, starts[0] + timedelta(days=1))


async def test_documents_filed_after_now_do_not_move_the_collected_since_day(
    session, make_document
):
    """The day is as of `now`: a document filed later (a clock pinned in the past, as the
    tests' NOW is) does not exist yet."""
    assert await collected_since(session, now=NOW) is None
    await make_document(created_at=NOW + timedelta(hours=1))
    assert await collected_since(session, now=NOW) is None
    await make_document(created_at=at(ago(3), hour=22, minute=30))  # 01:30 the next local day
    assert await collected_since(session, now=NOW) == ago(2)


# ---- visibility everywhere ----------------------------------------------------------------


async def test_hidden_signals_never_move_an_aggregate(
    session, competitor_factory, make_signal, make_document
):
    """Only status auto|approved outside the JFrog scope counts, for every persona;
    a stale or summary-only analysis is no analysis."""
    comp = await new_comp(session, competitor_factory)
    add_product(session, comp, "Alpha Repo", "Artifactory")
    base = await signal(make_signal, comp, ago(3), importance=10, status="auto")
    await analyse(session, base, hit("Artifactory", severity="low"))
    for status in ("needs_review", "suppressed", "rejected", "pending"):
        s = await signal(make_signal, comp, ago(3), importance=100, status=status)
        await analyse(session, s, hit("Artifactory"))
    jf = await signal(make_signal, comp, ago(3), importance=100, scope="jfrog")
    await analyse(session, jf, hit("Artifactory"))
    # visible, but without a current analysis: they count as signals, never as overlap
    stale = await signal(
        make_signal, comp, ago(3), importance=1, analyzed_at=datetime.now(UTC) + timedelta(hours=1)
    )
    await analyse(session, stale, hit("Artifactory"))
    gutted = await signal(make_signal, comp, ago(3), importance=1)
    await analyse(session, gutted, hit("Artifactory"), complete=False)

    [overlap] = await product_overlap(session, comp.id, now=NOW)
    assert (overlap.evidence, overlap.level, overlap.signal_ids) == (1, "low", [base.id])
    momentum = await momentum_series(session, comp.id, now=NOW)
    assert sum(p.value for p in momentum) == 12.0  # base + stale + gutted
    assert sorted(e.signal_id for e in await timeline_events(session, comp.id, now=NOW)) == sorted(
        [base.id, stale.id, gutted.id]
    )
    t = await threat(session, comp.id, now=NOW)  # 3 visible signals: scored, not insufficient
    assert (t.level, t.overlap) == ("low", 1.0)  # base's low threat only
    assert "from 1 current analysis" in t.explanation
    voice = await share_of_voice(session, [comp.id], weeks=1, now=NOW)
    assert [(p.documents, p.signals) for p in voice] == [(0, 3)]


# ---- query budget -------------------------------------------------------------------------


async def test_statement_budget_does_not_grow_with_history(
    session, competitor_factory, make_signal, make_source, sql_counter
):
    """A fixed number of statements, however many signals (no N+1)."""
    from app.intel.products import product_competitors, product_pressure, product_signals

    comp = await new_comp(session, competitor_factory)
    add_product(session, comp, "Alpha Repo", "Artifactory")
    add_product(session, comp, "Alpha Scan", "Xray")
    for days in range(0, 180, 3):  # 60 analysed signals over 180 days
        s = await signal(make_signal, comp, ago(days), importance=days % 50)
        await analyse(session, s, hit("Artifactory"), hit("Xray", effect="opportunity"))
    await board(session, make_source, comp, 60, 40, 5)
    await session.flush()

    budget = {
        "overlap": (lambda: product_overlap(session, comp.id, now=NOW), 2),
        # every weekly series also reads the collected-since day (+1)
        "momentum": (lambda: momentum_series(session, comp.id, now=NOW), 3),
        "timeline": (lambda: timeline_events(session, comp.id, now=NOW), 2),
        "threat": (lambda: threat(session, comp.id, now=NOW), 4),  # self, signals, board, day
        "bundle": (lambda: competitor_intel(session, comp.id, now=NOW), 5),  # + products
        "overview": (lambda: overview_intel(session, [comp.id], now=NOW), 4),
        "voice": (lambda: share_of_voice(session, [comp.id], now=NOW), 4),
        "competitors": (lambda: product_competitors(session, "Artifactory", now=NOW), 2),
        "pressure": (lambda: product_pressure(session, "Artifactory", now=NOW), 2),
        "signals": (lambda: product_signals(session, "Artifactory", now=NOW), 1),
    }
    for name, (call, limit) in budget.items():
        sql_counter.clear()
        result = await call()
        assert result, name
        assert len(sql_counter) == limit, (name, len(sql_counter))
    assert len(await product_signals(session, "Artifactory", now=NOW)) == 60


# ---- bundle and batch ---------------------------------------------------


def scans(statements: list[str]) -> int:
    """Statements that read the signals table (one signal scan each)."""
    return sum(1 for st in statements if "FROM signals" in st)


async def test_competitor_intel_bundles_one_signal_scan(
    session, competitor_factory, make_signal, make_source, sql_counter
):
    comp = await new_comp(session, competitor_factory)
    add_product(session, comp, "Alpha Repo", "Artifactory")
    add_product(session, comp, "Alpha Scan", "Xray")
    for days in (2, 9, 40, 70, 120, 170):
        s = await signal(make_signal, comp, ago(days), importance=days % 40 + 10)
        await analyse(session, s, hit("Artifactory"), hit("Xray", effect="opportunity"))
    await board(session, make_source, comp, 45, 45, 10)
    await session.flush()

    sql_counter.clear()
    bundle = await competitor_intel(session, comp.id, now=NOW)
    assert len(sql_counter) == 5 and scans(sql_counter) == 1  # + the collected day
    assert isinstance(bundle, CompetitorIntel)
    # the single functions are thin wrappers over the same builders
    assert bundle.overlap == await product_overlap(session, comp.id, now=NOW)
    assert bundle.momentum == await momentum_series(session, comp.id, now=NOW)
    assert bundle.timeline == await timeline_events(session, comp.id, now=NOW)
    assert bundle.threat == await threat(session, comp.id, now=NOW)
    assert [p.evidence for p in bundle.overlap] == [18, 18]
    assert bundle.threat.level == "high" and len(bundle.momentum) == 13

    me = await new_comp(session, competitor_factory, tier=0, relationship="self", is_self=True)
    empty = await competitor_intel(session, me.id, now=NOW)
    assert (empty.overlap, empty.momentum, empty.timeline) == ([], [], [])
    assert empty.threat.level == "insufficient" and empty.threat.overlap is None
    assert empty.activity is None

    # the bundle carries its job-board activity, and a caller that has loaded the
    # competitor already says whether it is JFrog, saving that statement
    [activity] = (await board_activity(session, [comp.id], now=NOW)).values()
    assert bundle.activity == activity and activity.has_board
    sql_counter.clear()
    known = await competitor_intel(session, comp.id, now=NOW, is_self=False)
    assert len(sql_counter) == 4 and known == bundle
    sql_counter.clear()
    told = await competitor_intel(session, me.id, now=NOW, is_self=True)
    assert len(sql_counter) == 0 and told == empty


async def test_overview_intel_batch_statements_do_not_grow(
    session, competitor_factory, make_signal, make_source, sql_counter
):
    comps = []
    for n in range(10):
        comp = await new_comp(session, competitor_factory, slug=f"peer-{n:02d}")
        comps.append(comp)
        for days in range(1 + n, 120, 17):  # different histories per competitor
            s = await signal(make_signal, comp, ago(days), importance=10 + n)
            if n % 2 == 0:
                await analyse(session, s, hit("Curation", severity="medium"))
        if n % 3 == 0:
            await board(session, make_source, comp, 45, 45, 10 + n)
    quiet = await new_comp(session, competitor_factory, slug="quiet")  # no data at all
    me = await new_comp(session, competitor_factory, tier=0, relationship="self", is_self=True)
    await signal(make_signal, me, ago(1))
    await session.flush()

    sql_counter.clear()
    one = await overview_intel(session, [comps[0].id], now=NOW)
    single = len(sql_counter)
    ids = [c.id for c in comps] + [quiet.id, me.id]
    sql_counter.clear()
    many = await overview_intel(session, ids, now=NOW)
    assert len(sql_counter) == single == 4  # independent of the competitors (+1)
    assert scans(sql_counter) == 1

    assert set(many) == {c.id for c in comps} | {quiet.id}  # self absent
    assert many[comps[0].id] == one[comps[0].id]
    for comp in comps:
        got = many[comp.id]
        assert isinstance(got, OverviewIntel)
        assert got.threat == await threat(session, comp.id, now=NOW)
        assert got.momentum == await momentum_series(session, comp.id, now=NOW)
        assert len(got.momentum) == 13
        assert got.overlap == got.threat.overlap
        timeline = await timeline_events(session, comp.id, now=NOW)
        assert got.latest_move == timeline[-1]
    q = many[quiet.id]
    assert q.threat.level == "insufficient" and q.overlap is None and q.latest_move is None
    assert [p.value for p in q.momentum] == [0.0] * 13
    assert await overview_intel(session, [], now=NOW) == {}
    # each row carries the board activity its threat read (no second call)
    activity = await board_activity(session, ids, now=NOW)
    assert {cid: v.activity for cid, v in many.items()} == {cid: activity[cid] for cid in many}


# ---- a job board counts only after its first completed fetch -----------------------


async def test_a_never_fetched_board_is_no_board_for_the_threat(
    session, competitor_factory, make_signal, make_source
):
    """A board found but never fetched gives no hiring data, so it neither lifts the
    "insufficient" rule nor feeds hiring_trend; with fewer than 3 signals the threat is
    insufficient (no overlap, no trend), never "low 0.0" with zero momentum."""
    lone = await new_comp(session, competitor_factory, slug="lone")
    await signal(make_signal, lone, ago(5))
    await make_source(kind="job_board", competitor_id=lone.id, config={"ats": "lever"})
    t = await threat(session, lone.id, now=NOW)
    assert (t.level, t.overlap, t.momentum_trend, t.hiring_trend, t.score) == (
        "insufficient",
        None,
        None,
        None,
        None,
    )
    assert "has not been listed in full yet" in t.explanation

    # a pending board with postings (none should exist) still never feeds hiring_trend
    busy = await new_comp(session, competitor_factory, slug="busy")
    for days in (2, 9, 40):
        await analyse(session, await signal(make_signal, busy, ago(days)), hit("Xray"))
    await board(session, make_source, busy, 60, 50, 5, fetched=False)
    t = await threat(session, busy.id, now=NOW)
    assert t.level != "insufficient" and t.hiring_trend is None  # 3 signals: scored
    assert "Hiring is not counted: the job board has not been listed in full yet." in t.explanation

    fetched = await new_comp(session, competitor_factory, slug="fetched")
    for days in (2, 9, 40):
        await analyse(session, await signal(make_signal, fetched, ago(days)), hit("Xray"))
    await board(session, make_source, fetched, 60, 50, 5)
    assert (await threat(session, fetched.id, now=NOW)).hiring_trend == 0.5  # 3 open vs 2

    view = (await overview_intel(session, [lone.id], now=NOW))[lone.id]
    assert view.threat.level == "insufficient" and view.overlap is None


async def test_factory_documents_never_bound_collection_at_the_pinned_clock(
    session, make_document, monkeypatch
):
    """A factory document's filing time is the wall clock but never earlier than just
    after the tests' pinned NOW, so it never moves collected_since under a pinned
    clock, whatever day the suite runs; a test that needs a collection day says created_at."""
    from tests import conftest

    monkeypatch.setattr(conftest, "_wall_clock", lambda: datetime(2026, 10, 1, tzinfo=UTC))
    doc = await make_document()
    assert doc.created_at > conftest.PINNED_NOW >= NOW
    assert await collected_since(session, now=NOW) is None
    dated = await make_document(created_at=at(ago(5)))
    assert dated.created_at == at(ago(5))
    assert await collected_since(session, now=NOW) == ago(5)


# ---- a stale job board is no board to the threat -----------------------


def test_a_stale_board_is_read_as_no_board_by_the_threat():
    """A board not listed in full for board_stale_days has no current numbers, so the
    threat takes no hiring factor from it and, with few signals, reads "insufficient"."""
    from app.intel.hiring import BoardActivity
    from app.intel.profile import SignalRow, _threat_from

    stale = BoardActivity(
        True,
        9,
        3,
        first_listed_at=at(ago(90)),
        as_of=None,
        last_listed_at=at(ago(5), 3),
    )
    fresh = BoardActivity(
        True, 9, 3, first_listed_at=at(ago(90)), as_of=at(ago(0), 3), last_listed_at=NOW
    )
    assert (stale.stale, stale.measured, fresh.measured) == (True, False, True)
    rows = [SignalRow(n, f"S{n}", None, 50, ago(n), 1, "acme", 1) for n in range(1, 4)]
    settings = get_settings()
    t = _threat_from(rows, stale, TODAY, settings)
    assert t.hiring_trend is None and t.level != "insufficient"
    assert "Hiring is not counted: the job board has not been readable since 15 Oct 2026." in (
        t.explanation
    )
    assert _threat_from(rows, fresh, TODAY, settings).hiring_trend == 2.0
    few = _threat_from(rows[:1], stale, TODAY, settings)
    assert few.level == "insufficient"
    assert "the job board has not been readable since 15 Oct 2026" in few.explanation


# ---- the signal scan reads jfrog_impact only ---------------------------


async def test_the_signal_scan_reads_only_the_impacts_with_the_same_results(
    session, competitor_factory, make_signal, sql_counter
):
    """The aggregates' scan selects each latest analysis's `jfrog_impact` and two
    usability checks as JSON expressions, never the whole analysis, and its rows equal the
    rule every reader shares (`analysis_content` + `is_current` on the full row)."""
    from sqlalchemy import select

    from app.agents.analysis_store import analysis_content, is_current
    from app.intel.profile import signal_rows
    from app.models.gold import Signal, SignalAnalysis

    comp = await new_comp(session, competitor_factory, slug="scan", name="Scan", tier=1)

    async def analysed(day: int, content: SignalAnalysisContent):
        sig = await signal(make_signal, comp, ago(day))
        await persist_analysis(session, sig.id, content, model="m", prompt_version="1", cost_usd=0)
        return sig

    def content(
        summary: str = "s", what: tuple[str, ...] = ("w",), *impacts
    ) -> SignalAnalysisContent:
        return SignalAnalysisContent(
            summary=CitedText(text=summary, cites=["doc:1"]),
            what_happened=[CitedText(text=w, cites=["doc:1"]) for w in what],
            jfrog_impact=list(impacts),
        )

    await analysed(1, content("s", ("w",), hit("Xray"), hit("Curation", "opportunity", "low")))
    await analysed(2, content("   ", ("w",), hit("Xray")))  # a blank summary: unusable
    await analysed(3, content("s", ("", " \n"), hit("Xray")))  # nothing happened: unusable
    await analysed(4, content("s", ("", "w"), hit("Artifactory")))  # one item is enough
    stale = await analysed(5, content("s", ("w",), hit("Xray")))
    await analysed(6, content("s", ("w",)))  # usable, no impact
    await signal(make_signal, comp, ago(7))  # never analysed
    stale_row = await session.get(Signal, stale.id)
    stale_row.analyzed_at = datetime.now(UTC) + timedelta(minutes=1)  # re-analysed since
    await session.flush()

    sql_counter.clear()
    rows = await signal_rows(session, start=ago(30), end=TODAY, analyses=True)
    (scan,) = sql_counter
    assert "latest.impact" in scan and "latest.usable" in scan  # JSON expressions only
    assert "latest.content" not in scan  # never the whole analysis

    reference = {}
    for r in rows:
        latest = await session.scalar(
            select(SignalAnalysis)
            .where(SignalAnalysis.signal_id == r.id)
            .order_by(SignalAnalysis.version.desc())
            .limit(1)
        )
        sig = await session.get(Signal, r.id)
        parsed = (
            analysis_content(latest)
            if latest is not None and is_current(latest, sig.analyzed_at)
            else None
        )
        reference[r.id] = (tuple(parsed.jfrog_impact), True) if parsed else ((), False)
    assert {r.id: (r.impacts, r.analysed) for r in rows} == reference
    assert sorted(analysed for _, analysed in reference.values()) == [False] * 4 + [True] * 3
