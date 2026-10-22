"""The competitor overview and deep-dive profile, section by section (the routes are
in app/api/v1/competitors.py).

The deep dive is identical for every persona:
- Every aggregate reads public signals only: threat, overlap, momentum, timeline, latest
  move, share of voice.
- So do the signal cards (latest signals, customer wins/losses, people and partners) and
  the brief's cited signals: public signals the reader may also see (`visible_signal_clause`),
  the same rule as the product page.
JFrog itself is never in the overview, and its profile is a 404, so its facts and
postings are never served.

Honest empty states:
- What FrogEye did not measure is null or empty, never 0.
- Without an enabled public job board a competitor has no hiring numbers, series or postings
  ("No public Greenhouse, Lever or Ashby board found", with the day discovery last checked
  when it recorded one).
- A board found but never listed in full shows none either ("Job board found — first
  snapshot pending"); its zero, or a partial listing's undercount, would not be measured. Only a
  competitor's current (enabled) boards count.
- A board not listed in full for `board_stale_days` is stale (app.intel.hiring): no
  current numbers ("Job board not readable since <day>"); its series end at its last
  complete listing. Current counts are as of the last complete listing (`as_of`).
- An insufficient threat has no overlap and no momentum series.
- A weekly series (momentum, share of voice, Hacker News mentions) serves only the weeks
  FrogEye collected in,
  from the week holding its first document's day (`collected_since`), and the
  momentum trend waits for a full collected baseline (app.intel.profile).
- Every empty section has a note in `sources_freshness.notes` saying why,
  in section order.

Query budget:
- The overview is 8 statements for any number of competitors: auth, competitors,
  `overview_intel` 4 (its board activity and collected-since day included),
  products, funding facts.
- The profile is at most 24, whatever the history:
  - auth 1, competitor and brief 1, `competitor_intel` 4 (the competitor already loaded;
    the collected-since day, which share of voice reuses)
  - `hiring_summary` 3, the five hiring series 1 (only with a fetched board)
  - facts 1, brief refs 1, page changes 1, signal cards 5
  - sources 1, community 1, peers 1, share of voice 3
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import (
    ColumnElement,
    Date,
    Float,
    Text,
    and_,
    cast,
    func,
    literal,
    or_,
    select,
    true,
    union,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.api.intel_out import event_out, posting_out, threat_out
from app.api.intel_sources import (
    FactGroup,
    LoadedFact,
    brief_sources,
    cited_one,
    cited_out,
    cited_refs,
    dated,
    funding_total,
    group_facts,
    headcount_points,
    latest,
    load_facts,
    points,
    undated,
)
from app.api.v1.signals import signal_cards
from app.api.visibility import public_signal_clause, visible_signal_clause
from app.core.config import get_settings
from app.core.days import local_day
from app.intel.briefs import competitor_brief_content
from app.intel.hiring import (
    BoardActivity,
    By,
    HiringSummary,
    has_history,
    hiring_summary,
    not_readable_note,
    open_roles_breakdowns,
)
from app.intel.profile import (
    LOOKBACK_DAYS,
    MOMENTUM_WEEKS,
    CompetitorIntel,
    ProductOverlap,
    Threat,
    WeekPoint,
    competitor_intel,
    document_day,
    measured_weeks,
    overview_intel,
    share_of_voice_since,
    signal_day,
    since,
    week_of,
    week_starts,
)
from app.models import (
    Competitor,
    CompetitorBrief,
    CompetitorProduct,
    Document,
    DocumentCompetitor,
    MetricPoint,
    PageSnapshot,
    Signal,
    SignalCompetitor,
    Source,
    User,
)
from app.models.enums import DocStatus, FactKind, SignalType, SourceKind
from app.schemas.intel import (
    CommunityOut,
    CompetitorBriefOut,
    CompetitorHeaderOut,
    CompetitorKpisOut,
    CompetitorProfile,
    CompetitorSummary,
    CoverageNoteOut,
    CustomersOut,
    DayCountOut,
    DayKeyCountOut,
    DayValueOut,
    HeadcountOut,
    HiringOut,
    HiringSeriesOut,
    KeyCountOut,
    MarketOut,
    MoneyOut,
    PageChangeOut,
    PeoplePartnersOut,
    ProductEdgeOut,
    ProductOverlapOut,
    ProfileSection,
    RepoSeriesOut,
    SourceFreshnessOut,
    SourcesFreshnessOut,
    TagCountOut,
    TagCoverageOut,
    VoicePointOut,
    WeekCountOut,
    WeekValueOut,
)
from app.schemas.signals import SignalCard

HIRING_DAYS = 90
CARDS = 20  # signal cards per list
PRICING_CHANGES = 30
DIFF_SERVED_CHARS = 4000  # of each page change's diff in the profile (whole lines)
PEERS = 4
COMMUNITY_DAYS = LOOKBACK_DAYS
SERIES: tuple[By, ...] = ("total", "department", "seniority", "location", "tag")
_LEVEL_RANK = {"high": 0, "medium": 1, "low": 2, "insufficient": 3}
_PEOPLE_TYPES = (SignalType.leadership_change, SignalType.acquisition, SignalType.partnership)
_OWN_NEWS = (
    SourceKind.rss,
    SourceKind.google_news,
    SourceKind.gdelt,
    SourceKind.hn,
    SourceKind.github_releases,
    SourceKind.sec_edgar,
)
_REPO_METRICS = ("github_stars:", "github_forks:")
_MENTIONS = "hn_mentions"
_CUSTOMER_KINDS = (FactKind.customer, FactKind.customer_count)
_PEOPLE_KINDS = (
    FactKind.leadership_change,
    FactKind.acquisition,
    FactKind.partnership,
    FactKind.office,
)
_MONEY_KINDS = (FactKind.funding_round, FactKind.valuation, FactKind.revenue, FactKind.arr)

# coverage notes, one or more per empty section (fixed copy where specified)
NO_BOARD = "No public Greenhouse, Lever or Ashby board found"
BOARD_PENDING = "Job board found — first snapshot pending"
NO_PRODUCTS = "No products of this competitor are mapped to JFrog products"
NO_SIGNALS = f"No public signals about it in the last {LOOKBACK_DAYS} days"
NO_HEADCOUNT = "No headcount or layoff figures have been reported in its sources yet"
NO_MONEY = "No funding, valuation or revenue figures have been reported in its sources yet"
NO_MARKET_SHARE = "No market share figure has been reported in its sources yet"
NO_CUSTOMERS = "No customers or customer wins and losses have been reported in its sources yet"
NO_PRICING_PAGE = "No pricing page is watched for this competitor"
NO_PRICING_CHANGE = "No meaningful pricing change seen on its pages yet"
NO_REPOS = "No GitHub repositories are tracked for this competitor"
NO_REPO_COUNTS = "No GitHub star or fork counts recorded yet"
NO_HN = "No Hacker News search runs for this competitor"
NO_PEOPLE = (
    "No leadership changes, acquisitions, partnerships or offices have been reported in its "
    "sources yet"
)
NO_LATEST = "No public signals about it yet"
NO_OWN_SOURCES = "No news sources of its own: it is covered through industry feeds only"
NO_BRIEF_YET = "No brief has been written yet: briefs are written weekly"


def logo_url(c: Competitor) -> str | None:
    return f"/logos/{c.slug}.svg" if c.logo_path else None


def _day(d: date) -> str:
    return f"{d.day} {d:%b} {d.year}"


def no_board_note(checked: datetime | None) -> str:
    """Discovery is an operator command and a miss is cached for 30 days, so the note
    says what was found and, when recorded, the day it was last checked."""
    if checked is None:
        return NO_BOARD
    return f"{NO_BOARD} (checked {_day(local_day(checked))})"


async def competitor_or_404(session: AsyncSession, slug: str) -> Competitor:
    comp = await session.scalar(select(Competitor).where(Competitor.slug == slug))
    if comp is None or comp.is_self:  # JFrog itself has no deep dive
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Competitor not found")
    return comp


@dataclass(frozen=True)
class _Board:
    """Whether its open roles are measured: an enabled board listed in full, and listed in
    full in the last `board_stale_days`."""

    has_board: bool
    fetched: bool
    stale: bool = False
    last_listed: date | None = None  # its newest complete listing's day
    checked: datetime | None = None  # discovery's last check that found none

    @classmethod
    def of(cls, activity: BoardActivity | None) -> "_Board":
        if activity is None:
            return cls(False, False)
        return cls(
            activity.has_board,
            activity.has_board and activity.fetched,
            activity.stale,
            activity.last_listed_day,
            activity.board_checked_at,
        )

    @property
    def measured(self) -> bool:
        return self.has_board and self.fetched and not self.stale

    @property
    def state(self) -> str:
        """measured, pending (found, never listed in full), stale or none."""
        if not self.has_board:
            return "none"
        if not self.fetched:
            return "pending"
        return "stale" if self.stale else "measured"

    @property
    def note(self) -> str | None:
        """Why there are no hiring numbers, or None when there are."""
        if not self.has_board:
            return no_board_note(self.checked)
        if not self.fetched:
            return BOARD_PENDING
        if self.stale and self.last_listed is not None:
            return not_readable_note(self.last_listed)
        return None


def _momentum(t: Threat, weeks: Sequence[WeekPoint]) -> list[WeekValueOut] | None:
    """An insufficient threat has no momentum series, never thirteen zeros."""
    if t.level == "insufficient" or not weeks:
        return None
    return [WeekValueOut(week_start=w.week_start, value=w.value) for w in weeks]


def _counterparts(jfrog: Iterable[str]) -> list[str]:
    found = set(jfrog)
    return [p for p in JFROG_PRODUCTS if p in found]


# ---- overview -----------------------------------------------------------------------------


async def build_overview(session: AsyncSession, now: datetime) -> list[CompetitorSummary]:
    """Every competitor but JFrog itself, by threat (high first, then score), then tier and
    name."""
    comps = list(await session.scalars(select(Competitor).where(Competitor.is_self.is_(False))))
    ids = [c.id for c in comps]
    intel = await overview_intel(session, ids, now=now)
    jfrog: dict[int, list[str]] = {}
    categories: dict[int, list[str]] = {}  # the list's category filter, seed order
    for cid, products, category in await session.execute(
        select(
            CompetitorProduct.competitor_id,
            CompetitorProduct.jfrog_products,
            CompetitorProduct.category,
        )
        .where(CompetitorProduct.competitor_id.in_(ids))
        .order_by(CompetitorProduct.id)
    ):
        jfrog.setdefault(cid, []).extend(products)
        if category not in categories.setdefault(cid, []):
            categories[cid].append(category)
    funding: dict[int, list[FactGroup]] = {}
    for g in group_facts(await load_facts(session, ids, kinds=[FactKind.funding_round])):
        funding.setdefault(g.competitor_id, []).append(g)

    today = local_day(now, get_settings().timezone)
    since = {  # a measured board's first snapshot day
        cid: v.activity.board_since
        for cid, v in intel.items()
        if v.activity is not None and _Board.of(v.activity).measured
    }
    rows = []
    for c in comps:
        view = intel[c.id]
        board = _Board.of(view.activity)
        activity = view.activity if board.measured else None
        rows.append(
            CompetitorSummary(
                slug=c.slug,
                name=c.name,
                tier=c.tier,
                relationship=c.relationship,
                jfrog_products=_counterparts(jfrog.get(c.id, [])),
                product_categories=categories.get(c.id, []),
                threat=threat_out(view.threat),
                momentum=_momentum(view.threat, view.momentum),
                overlap=view.overlap,
                open_roles=activity.open_now if activity else None,
                # no 30-day change before the board has 30 days of history
                delta_30d=activity.open_now - activity.open_then
                if activity and has_history(since.get(c.id), activity.as_of_day or today)
                else None,
                open_roles_as_of=activity.as_of if activity else None,
                board_since=since.get(c.id),
                has_board=board.has_board,
                board_checked_at=board.checked if not board.has_board else None,
                board_state=board.state,
                open_roles_note=board.note,
                funding_total=funding_total(funding.get(c.id, [])),
                latest_move=event_out(view.latest_move) if view.latest_move else None,
            )
        )
    rows.sort(key=lambda r: (_LEVEL_RANK[r.threat.level], -(r.threat.score or 0.0), r.tier, r.name))
    return rows


# ---- profile ------------------------------------------------------------------------------


async def build_profile(
    session: AsyncSession, user: User, slug: str, now: datetime
) -> CompetitorProfile:
    settings = get_settings()
    tz = settings.timezone
    today = local_day(now, tz)
    comp, brief_row = await _competitor_and_brief(session, slug)
    intel = await competitor_intel(session, comp.id, now=now, is_self=False)
    board = _Board.of(intel.activity)
    summary = await hiring_summary(session, comp.id, now=now)
    # from the board's first snapshot (today when it has no posting yet), never
    # zero-filled over the days before the board was read; to the day its boards were
    # last listed in full (a stale board's history ends at its last complete listing)
    until = summary.as_of_day if board.measured else summary.last_listed_day
    series = (
        await open_roles_breakdowns(
            session,
            [comp.id],
            days=HIRING_DAYS,
            by=SERIES,
            now=now,
            since=summary.board_since or today,
            until=until,
            stale=board.stale,
        )
        if (board.measured or board.stale) and until is not None
        else {}
    )
    facts = await load_facts(session, [comp.id])
    groups = group_facts(facts)
    brief, edges = await _brief(session, user, comp, brief_row, {f.id: f for f in facts})
    pricing = await _page_changes(session, comp.id, tz)
    cards = await _cards(session, user, comp.id, tz)
    sources = await _sources(session, comp.id)
    community = await _community(session, comp.id, sources, today, tz, intel.collected_since)
    peers = await _peers(session, comp)
    voice = await share_of_voice_since(
        session, [comp.id, *peers.ids], now=now, collected=intel.collected_since
    )

    headcount_group = latest(groups, FactKind.headcount)
    last_round = latest(groups, FactKind.funding_round)
    profile = CompetitorProfile(
        competitor=CompetitorHeaderOut(
            slug=comp.slug,
            name=comp.name,
            tier=comp.tier,
            relationship=comp.relationship,
            description=comp.description,
            website=comp.website,
            ticker=comp.ticker,
        ),
        kpis=CompetitorKpisOut(
            threat=threat_out(intel.threat),
            has_board=board.has_board,
            open_roles=summary.open_now if board.measured else None,
            delta_30d=summary.delta_30d if board.measured else None,
            latest_headcount=headcount_group.out() if headcount_group else None,
            funding_total=funding_total(groups),
            last_round=last_round.funding_round() if last_round else None,
        ),
        brief=brief,
        products=[_product(p, peers.product_urls.get(p.product), edges) for p in intel.overlap],
        momentum=_momentum(intel.threat, intel.momentum),
        timeline=[event_out(e) for e in intel.timeline],
        hiring=_hiring(summary, series, board),
        headcount=HeadcountOut(
            points=headcount_points(groups),
            undated=[g.out() for g in undated(groups, FactKind.headcount, FactKind.layoff)],
        ),
        money=MoneyOut(
            funding_rounds=[
                g.funding_round()
                for g in reversed(dated(groups, FactKind.funding_round))  # oldest first
            ],
            valuations=points(groups, FactKind.valuation),
            revenue=points(groups, FactKind.revenue, FactKind.arr),
            undated=[g.out() for g in undated(groups, *_MONEY_KINDS)],
        ),
        market=MarketOut(
            market_share=[g.out() for g in dated(groups, FactKind.market_share)],
            undated=[g.out() for g in undated(groups, FactKind.market_share)],
            peers=peers.slugs,
            share_of_voice=[
                VoicePointOut(
                    week_start=p.week_start,
                    competitor_slug=p.competitor_slug,
                    documents=p.documents,
                    signals=p.signals,
                )
                for p in voice
            ],
        ),
        customers=CustomersOut(
            facts=[g.out() for g in dated(groups, *_CUSTOMER_KINDS)],
            undated=[g.out() for g in undated(groups, *_CUSTOMER_KINDS)],
            signals=cards["customers"],
        ),
        pricing=pricing,
        community=community,
        people_partners=PeoplePartnersOut(
            facts=[g.out() for g in dated(groups, *_PEOPLE_KINDS)],
            undated=[g.out() for g in undated(groups, *_PEOPLE_KINDS)],
            signals=cards["people"],
        ),
        latest_signals=cards["latest"],
        collected_since=intel.collected_since,
        sources_freshness=SourcesFreshnessOut(
            sources=[
                SourceFreshnessOut(
                    kind=s.kind, name=s.name, health=s.health, last_fetched_at=s.last_fetched_at
                )
                for s in sources
            ],
            notes=[],
        ),
    )
    profile.sources_freshness.notes = _notes(profile, intel, board, sources, today)
    return profile


async def _competitor_and_brief(
    session: AsyncSession, slug: str
) -> tuple[Competitor, CompetitorBrief | None]:
    """The competitor and its current (highest-version) brief, in one statement."""
    latest_brief = (
        select(CompetitorBrief)
        .where(CompetitorBrief.competitor_id == Competitor.id)
        .order_by(CompetitorBrief.version.desc())
        .limit(1)
        .lateral("latest_brief")
    )
    brief = aliased(CompetitorBrief, latest_brief)
    row = (
        await session.execute(
            select(Competitor, brief).outerjoin(latest_brief, true()).where(Competitor.slug == slug)
        )
    ).first()
    if row is None or row[0].is_self:  # JFrog itself has no deep dive
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Competitor not found")
    return row[0], row[1]


async def _brief(
    session: AsyncSession,
    user: User,
    comp: Competitor,
    row: CompetitorBrief | None,
    facts: Mapping[int, LoadedFact],
) -> tuple[CompetitorBriefOut | None, list[ProductEdgeOut]]:
    content = competitor_brief_content(row)
    if row is None or content is None:
        return None, []
    edges_in = [e.edge for e in content.product_edges]
    refs = cited_refs(content.bottom_line, edges_in, content.watch)
    sources, numbers = await brief_sources(session, user, refs, competitor_id=comp.id, facts=facts)
    edges = [
        ProductEdgeOut(
            product=e.product, jfrog_product=e.jfrog_product, edge=cited_one(e.edge, numbers)
        )
        for e in content.product_edges
    ]
    return (
        CompetitorBriefOut(
            version=row.version,
            created_at=row.created_at,
            bottom_line=cited_out(content.bottom_line, numbers),
            product_edges=edges,
            watch=cited_out(content.watch, numbers),
            evidence=content.evidence,
            sources=sources,
        ),
        edges,
    )


def _product(p: ProductOverlap, url: str | None, edges: list[ProductEdgeOut]) -> ProductOverlapOut:
    return ProductOverlapOut(
        product=p.product,
        category=p.category,
        url=url,
        jfrog_products=p.jfrog_products,
        level=p.level,
        evidence=p.evidence,
        signal_ids=p.signal_ids,
        edges=[e for e in edges if e.product == p.product],
    )


def _hiring(summary: HiringSummary, series: dict, board: _Board) -> HiringOut:
    """Unless an enabled board has been listed in full, and lately, nothing current is
    measured: nulls and empty lists. A stale board keeps its
    series, which end at its last complete listing."""

    def keyed(pairs: Iterable[tuple[str, int]]) -> list[KeyCountOut]:
        return [KeyCountOut(key=k, count=n) for k, n in pairs] if board.measured else []

    def per_key(by: str) -> list[DayKeyCountOut]:
        return [DayKeyCountOut(day=p.day, key=p.key, count=p.count) for p in series.get(by, [])]

    measured = board.measured
    coverage = summary.coverage
    return HiringOut(
        has_board=board.has_board,
        open_now=summary.open_now if measured else None,
        delta_30d=summary.delta_30d if measured else None,
        board_since=summary.board_since if measured or board.stale else None,
        as_of=summary.as_of if measured else None,
        not_readable_since=board.last_listed if board.stale else None,
        board_checked_at=board.checked if not board.has_board else None,
        by_department=keyed(summary.by_department),
        by_seniority=keyed(summary.by_seniority),
        by_location=keyed(summary.by_location),
        tags=[TagCountOut(slug=s, open=n, delta_30d=d) for s, n, d in summary.tags]
        if measured
        else [],
        tag_coverage=TagCoverageOut(
            tagged=coverage.tagged,
            pending=coverage.pending,
            untagged=coverage.untagged,
            gave_up=coverage.gave_up,
        )
        if measured
        else None,
        recent=[posting_out(r) for r in summary.recent] if measured else [],
        series=HiringSeriesOut(
            total=[DayCountOut(day=p.day, count=p.count) for p in series.get("total", [])],
            department=per_key("department"),
            seniority=per_key("seniority"),
            location=per_key("location"),
            tag=per_key("tag"),
        ),
    )


def pricing_change() -> ColumnElement[bool]:
    """A page change the deep dive shows: a meaningful change to a watched pricing page, or
    one judged a pricing or packaging change on another watched page."""
    return and_(
        Source.kind == SourceKind.page_watch,
        PageSnapshot.meaningful.is_(True),
        or_(
            PageSnapshot.page_kind == "pricing",
            PageSnapshot.change_kind.in_(("pricing", "packaging")),
        ),
    )


def served_diff(head: str | None, longer: bool) -> tuple[str | None, bool]:
    """The first DIFF_SERVED_CHARS characters of a diff, cut at a whole line, and
    whether it was cut. `head` is the stored diff's first DIFF_SERVED_CHARS + 1 characters at
    most, and `longer` says the stored diff holds more than DIFF_SERVED_CHARS."""
    if head is None or not longer:
        return head, False
    clipped = head[:DIFF_SERVED_CHARS]
    whole, newline, _ = clipped.rpartition("\n")
    return (whole if newline else clipped), True


async def _page_changes(session: AsyncSession, competitor_id: int, tz: str) -> list[PageChangeOut]:
    """Meaningful changes of its watched pricing pages, and changes judged pricing or
    packaging on its other watched pages, newest first. Each diff is served capped at
    DIFF_SERVED_CHARS: the whole one is GET .../page-changes/{id}/diff."""
    day = func.date(func.timezone(tz, PageSnapshot.fetched_at), type_=Date)
    rows = await session.execute(
        select(
            PageSnapshot.id,
            day,
            PageSnapshot.page_kind,
            PageSnapshot.change_kind,
            PageSnapshot.change_summary,
            func.substr(PageSnapshot.diff_from_prev, 1, DIFF_SERVED_CHARS + 1),
            func.length(PageSnapshot.diff_from_prev) > DIFF_SERVED_CHARS,
            func.coalesce(Source.config["url"].astext, Source.url),
        )
        .join(Source, Source.id == PageSnapshot.source_id)
        .where(Source.competitor_id == competitor_id, pricing_change())
        .order_by(PageSnapshot.fetched_at.desc(), PageSnapshot.id.desc())
        .limit(PRICING_CHANGES)
    )
    out = []
    for sid, d, page_kind, change_kind, summary, head, longer, url in rows.all():
        diff, truncated = served_diff(head, bool(longer))
        out.append(
            PageChangeOut(
                snapshot_id=sid,
                day=d,
                page_kind=page_kind,
                change_kind=change_kind,
                summary=summary,
                diff=diff,
                truncated=truncated,
                url=url,
            )
        )
    return out


async def page_change_diff(session: AsyncSession, comp: Competitor, snapshot_id: int) -> str:
    """The whole stored diff of one of its page changes the deep dive shows; a 404
    for any other snapshot or one without a diff."""
    diff = await session.scalar(
        select(PageSnapshot.diff_from_prev)
        .join(Source, Source.id == PageSnapshot.source_id)
        .where(
            PageSnapshot.id == snapshot_id,
            Source.competitor_id == comp.id,
            pricing_change(),
        )
    )
    if diff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page change not found")
    return diff


async def _cards(
    session: AsyncSession, user: User, competitor_id: int, tz: str
) -> dict[str, list[SignalCard]]:
    """Its newest public signals the reader may also see (the product page's rule,
    the same cards for every persona): all, customer wins/losses, and people and partners,
    CARDS of each, from one statement plus the card batch."""
    day = signal_day(tz)
    mine = (
        select(Signal.id)
        .join(SignalCompetitor, SignalCompetitor.signal_id == Signal.id)
        .where(
            SignalCompetitor.competitor_id == competitor_id,
            public_signal_clause(),
            visible_signal_clause(user),
        )
    )

    def newest(stmt):
        sub = stmt.order_by(day.desc(), Signal.id.desc()).limit(CARDS).subquery()
        return select(sub.c.id)

    ids = union(
        newest(mine),
        newest(mine.where(Signal.signal_type == SignalType.customer_win_loss)),
        newest(mine.where(Signal.signal_type.in_(_PEOPLE_TYPES))),
    )
    rows = (await session.execute(select(Signal, day.label("day")).where(Signal.id.in_(ids)))).all()
    ordered = [s for s, _ in sorted(rows, key=lambda r: (r[1], r[0].id), reverse=True)]
    by_id = {c.id: c for c in await signal_cards(session, ordered)}
    cards = [by_id[s.id] for s in ordered]
    people = {str(t) for t in _PEOPLE_TYPES}
    return {
        "latest": cards[:CARDS],
        "customers": [c for c in cards if c.signal_type == SignalType.customer_win_loss][:CARDS],
        "people": [c for c in cards if c.signal_type in people][:CARDS],
    }


@dataclass(frozen=True)
class _SourceRow:
    kind: str
    name: str
    health: str
    last_fetched_at: datetime | None
    config: dict[str, Any]


async def _sources(session: AsyncSession, competitor_id: int) -> list[_SourceRow]:
    """Its enabled sources (columns only: no eager competitor join)."""
    rows = await session.execute(
        select(Source.kind, Source.name, Source.health, Source.last_fetched_at, Source.config)
        .where(Source.competitor_id == competitor_id, Source.enabled.is_(True))
        .order_by(Source.kind, Source.name)
    )
    return [_SourceRow(k, n, h, at, cfg or {}) for k, n, h, at, cfg in rows.all()]


async def _community(
    session: AsyncSession,
    competitor_id: int,
    sources: list[_SourceRow],
    today: date,
    tz: str,
    collected: date | None,
) -> CommunityOut:
    """GitHub stars/forks per day (180 days) and relevant Hacker News items per week (the
    collected weeks of 13: from the week holding `collected`, never 0-mention weeks
    before FrogEye collected anything; none without a Hacker News search), in one
    statement."""
    start = since(today, COMMUNITY_DAYS)
    starts = measured_weeks(week_starts(today, MOMENTUM_WEEKS), collected)
    has_hn = bool(starts) and any(s.kind == SourceKind.hn for s in sources)
    metrics = select(
        MetricPoint.metric.label("key"),
        MetricPoint.date.label("day"),
        cast(MetricPoint.value, Float).label("value"),
    ).where(
        MetricPoint.competitor_id == competitor_id,
        MetricPoint.date >= start,
        MetricPoint.date <= today,
        or_(*(MetricPoint.metric.startswith(prefix) for prefix in _REPO_METRICS)),
    )
    parts = [metrics]
    if has_hn:
        doc_day = document_day(tz)
        mentioned = (
            select(doc_day.label("day"))
            .join(DocumentCompetitor, DocumentCompetitor.document_id == Document.id)
            .join(Source, and_(Source.id == Document.source_id, Source.kind == SourceKind.hn))
            .where(
                DocumentCompetitor.competitor_id == competitor_id,
                Document.status == DocStatus.relevant,
                doc_day >= starts[0],
                doc_day <= today,
            )
            .subquery()
        )
        parts.append(
            select(
                cast(literal(_MENTIONS), Text), mentioned.c.day, cast(func.count(), Float)
            ).group_by(mentioned.c.day)
        )
    stmt = parts[0] if len(parts) == 1 else union_all(*parts)
    repos: dict[str, dict[str, list[DayValueOut]]] = {}
    weekly = [0] * len(starts)
    for key, day, value in (await session.execute(stmt)).all():
        if key == _MENTIONS:
            i = week_of(day, starts)
            if i is not None:
                weekly[i] += int(value)
            continue
        metric, _, repo = key.partition(":")
        series = repos.setdefault(repo, {"github_stars": [], "github_forks": []})
        series[metric].append(DayValueOut(day=day, value=value))
    return CommunityOut(
        repos=[
            RepoSeriesOut(
                repo=repo,
                stars=sorted(s["github_stars"], key=lambda p: p.day),
                forks=sorted(s["github_forks"], key=lambda p: p.day),
            )
            for repo, s in sorted(repos.items())
        ],
        mentions=(
            [WeekCountOut(week_start=w, count=n) for w, n in zip(starts, weekly, strict=True)]
            if has_hn
            else []
        ),
    )


@dataclass(frozen=True)
class _Peers:
    ids: list[int]
    slugs: list[str]
    product_urls: dict[str, str]  # its own seeded products' URLs, by name


async def _peers(session: AsyncSession, comp: Competitor) -> _Peers:
    """The PEERS competitors sharing the most JFrog counterparts with its seeded products
    (ties: the closest tier, then slug; never JFrog), best first, and its products' URLs
    (one statement)."""
    rows = await session.execute(
        select(
            Competitor.id,
            Competitor.slug,
            Competitor.tier,
            CompetitorProduct.name,
            CompetitorProduct.url,
            CompetitorProduct.jfrog_products,
        )
        .outerjoin(CompetitorProduct, CompetitorProduct.competitor_id == Competitor.id)
        .where(Competitor.is_self.is_(False))
        .order_by(Competitor.id, CompetitorProduct.id)
    )
    others: dict[int, tuple[str, int, set[str]]] = {}
    mine: set[str] = set()
    urls: dict[str, str] = {}
    for cid, slug, tier, name, url, jfrog in rows.all():
        if cid == comp.id:
            mine |= set(jfrog or [])
            if name and url:
                urls[name] = url
            continue
        others.setdefault(cid, (slug, tier, set()))[2].update(jfrog or [])
    ranked = sorted(
        others.items(),
        key=lambda kv: (-len(kv[1][2] & mine), abs(kv[1][1] - comp.tier), kv[1][0]),
    )[:PEERS]
    return _Peers([cid for cid, _ in ranked], [peer[0] for _, peer in ranked], urls)


def _notes(
    p: CompetitorProfile,
    intel: CompetitorIntel,
    board: _Board,
    sources: list[_SourceRow],
    today: date,
) -> list[CoverageNoteOut]:
    """Why each empty (or partly empty) section is so, said plainly, in section order
    (a gap is shown and explained, never hidden)."""
    settings = get_settings()
    kinds = {s.kind for s in sources}
    public = len({e.signal_id for e in intel.timeline})
    notes: list[tuple[ProfileSection, str]] = []
    if p.brief is None:
        days, needed = settings.brief_lookback_days, settings.brief_min_signals
        first = since(today, days)
        recent = len({e.signal_id for e in intel.timeline if e.day >= first})
        if recent < needed:
            noun = "public signal" if recent == 1 else "public signals"
            notes.append(
                (
                    "overview",
                    f"Not enough evidence for a brief yet: {recent} {noun} in the last {days} "
                    f"days ({needed} needed)",
                )
            )
        else:
            notes.append(("overview", NO_BRIEF_YET))
    if not p.products:
        notes.append(("products", NO_PRODUCTS))
    if not p.timeline:
        notes.append(("momentum", NO_SIGNALS))
    elif p.momentum is None:
        noun = "public signal" if public == 1 else "public signals"
        # a board found but not listed in full is pending, as the hiring note says;
        # a stale one is not readable
        if not board.has_board:
            board_words = "no job board"
        elif board.stale and board.last_listed is not None:
            board_words = f"its job board has not been readable since {_day(board.last_listed)}"
        else:
            board_words = "its job board has not been listed in full yet"
        notes.append(
            (
                "momentum",
                f"Too little evidence for a momentum series: {public} {noun} in the last "
                f"{LOOKBACK_DAYS} days ({settings.threat_min_signals} needed) and {board_words}",
            )
        )
    if (note := board.note) is not None:
        notes.append(("hiring", note))
    if not (p.headcount.points or p.headcount.undated):
        notes.append(("headcount", NO_HEADCOUNT))
    m = p.money
    if not (m.funding_rounds or m.valuations or m.revenue or m.undated):
        notes.append(("money", NO_MONEY))
    if not (p.market.market_share or p.market.undated):
        notes.append(("market", NO_MARKET_SHARE))
    if not (p.customers.facts or p.customers.undated or p.customers.signals):
        notes.append(("customers", NO_CUSTOMERS))
    if not p.pricing:
        watched = any(
            s.kind == SourceKind.page_watch and s.config.get("page") == "pricing" for s in sources
        )
        notes.append(("pricing", NO_PRICING_CHANGE if watched else NO_PRICING_PAGE))
    if not p.community.repos:
        has_repos = SourceKind.github_repo_stats in kinds
        notes.append(("community", NO_REPO_COUNTS if has_repos else NO_REPOS))
    if not p.community.mentions:
        notes.append(("community", NO_HN))
    pp = p.people_partners
    if not (pp.facts or pp.undated or pp.signals):
        notes.append(("people_partners", NO_PEOPLE))
    if not p.latest_signals:
        notes.append(("latest_signals", NO_LATEST))
    if not kinds & {str(k) for k in _OWN_NEWS}:
        notes.append(("sources", NO_OWN_SOURCES))
    return [CoverageNoteOut(section=s, text=t) for s, t in notes]
