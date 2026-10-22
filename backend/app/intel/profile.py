"""Competitor intelligence computed from the data alone, with no LLM: product
overlap, momentum, the threat level, the swimlane timeline and share of voice.

How this is computed (the "How this is computed" disclosures quote this):

* Visibility. Only signals that pass `public_signal_clause()` count: status
  auto|approved, scope not JFrog, competitor or industry scope alike, attributed to a
  competitor through `signal_competitors` (the product views read the same set).
  Every persona sees the same numbers. The self competitor gets no aggregate: its lists
  are empty and its threat is "insufficient".
* Days. A signal's day is its stated event date, unless that date is after the day the signal
  was first seen (an announced future date); then, as for a signal with no date, it is the
  local day (settings.timezone, Asia/Jerusalem) it was first seen. A document's day is the
  local day it was published, or the day it was filed when it is undated or dated later.
* Weeks are whole 7-day windows of local days ending today, oldest first. A window's
  `week_start` is its first day, so the latest week ends today and no week is partial.
* Collected since. FrogEye began collecting on the local day it filed its first
  document (`collected_since`, as of `now`). A weekly series (momentum, share of voice, a
  product's pressure) serves only the weeks that end on or after that day (`measured_weeks`):
  the weeks before were never collected, so they are not served, never quiet zero weeks. A
  trend (`momentum_trend`, the pressure word) needs a full baseline after it: the first of
  its 13 weeks starts on or after that day (`has_baseline`), else it has no baseline yet.
  With no document filed yet nothing bounds the weeks (a fresh install, whose weeks are
  empty anyway: every signal comes from a document).
* A signal's current analysis is its latest stage-B analysis when that is current and
  usable (`analysis_content`: a summary and something that happened); otherwise the
  signal has none. The scan reads only its `jfrog_impact` and those two checks, as JSON
  expressions: the payload stays small however long the analyses are. Severity
  weights: high 3, medium 2, low 1.
* Product overlap. For each seeded product of the competitor, evidence is the sum of the
  weights of the `jfrog_impact` entries, any effect, that name one of its JFrog counterparts,
  in the current analyses of the competitor's visible signals of the last 180 days. Level:
  high from `overlap_high_evidence` (6), medium from `overlap_medium_evidence` (3), low from
  1, else none (worded "no recent evidence": the seed already says the products compete).
* Momentum: the sum of the importance of the competitor's visible signals, per week.
* Threat:
      overlap        = the sum of the weights of the `jfrog_impact` entries with effect
                       "threat" (any JFrog product; current analyses; 180 days)
      momentum_trend = the mean weekly momentum of the last 4 weeks divided by that of the 9
                       weeks before, at most 3.0; None when those 9 weeks are empty or were
                       not all collected: no baseline yet, and then its factor is 1.0
      hiring_trend   = delta_30d / max(open roles 30 days ago, 1), from the job board (the
                       `hiring_summary` rule: current boards only, counted as of their last
                       complete listing); None without an enabled job board that has been
                       listed in full (a board found but never listed in full
                       holds no data), for a stale board (not listed in full for
                       `board_stale_days`: read as no board), or before the board has
                       30 days of history (a new board would read as a hiring spike)
      score          = overlap x clamp(momentum_trend, 0.5, 2.0)   (x 1.0 when None)
                               x (1 + clamp(hiring_trend, -0.25, +0.25))   (x 1.0 when None)
      level          = high when score >= `threat_high_score` (12), medium when score >=
                       `threat_medium_score` (4), else low; "insufficient" when the competitor
                       has fewer than `threat_min_signals` (3) visible signals in 180 days and
                       no fetched job board, a stale one counting as none. An insufficient threat
                       has no overlap, trend or score (None), so nothing reads as a measured 0.
  The trends and the score are rounded to 2 decimals before the level is read, and the
  explanation names every driver with its number.
* Timeline: every visible signal of the last 180 days, in its lane (`LANES`).
* Share of voice: per competitor and week, the relevant documents linked to it (JFrog-self
  news excluded) and its visible signals.

Query budget (statements, whatever the history): `competitor_intel` 5 (overlap, momentum,
timeline and threat from ONE signal scan, and the collected-since day; 4 when the caller
passes `is_self`); `overview_intel` 4 for any number of competitors (both carry the board
activity and the collected-since day they read); the single functions are thin wrappers
over the same builders: `product_overlap` and `timeline_events` 2 each, `momentum_series` 3,
`threat` 4, `share_of_voice` 4 (3 for `share_of_voice_since`, given the day).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import ColumnElement, Date, and_, cast, func, literal, select, true
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import JFROG_PRODUCTS, ProductImpact
from app.agents.analysis_store import is_current
from app.api.visibility import public_signal_clause
from app.core.config import Settings, get_settings
from app.core.days import local_day
from app.intel.hiring import (
    DELTA_DAYS,
    BoardActivity,
    board_activity,
    board_since,
    has_history,
)
from app.models import Competitor, CompetitorProduct, Document, DocumentCompetitor
from app.models.enums import DocStatus, SignalType
from app.models.gold import Signal, SignalAnalysis, SignalCompetitor
from app.processing.self_news import self_news_document

LOOKBACK_DAYS = 180
MOMENTUM_WEEKS = 13
TREND_RECENT_WEEKS = 4  # compared with the MOMENTUM_WEEKS - 4 = 9 weeks before
TREND_CAP = 3.0
MOMENTUM_FACTOR = (0.5, 2.0)
HIRING_FACTOR = 0.25  # the hiring factor stays within 1 -/+ this
SEVERITY_WEIGHT: dict[str, int] = {"high": 3, "medium": 2, "low": 1}

OverlapLevel = Literal["high", "medium", "low", "none"]
ThreatLevel = Literal["high", "medium", "low", "insufficient"]
Lane = Literal["Launches", "Pricing", "Partners", "Money", "People", "Security", "Market"]
LANE_ORDER: tuple[Lane, ...] = (
    "Launches",
    "Pricing",
    "Partners",
    "Money",
    "People",
    "Security",
    "Market",
)
_LANE_TYPES: dict[Lane, tuple[SignalType, ...]] = {  # Global Constraints, swimlane table
    "Launches": (SignalType.product_launch, SignalType.feature_update, SignalType.deprecation),
    "Pricing": (SignalType.pricing_change,),
    "Partners": (SignalType.partnership, SignalType.customer_win_loss),
    "Money": (SignalType.funding, SignalType.acquisition, SignalType.earnings_financials),
    "People": (SignalType.leadership_change, SignalType.hiring_trend),
    "Security": (SignalType.security_incident,),
    "Market": (
        SignalType.analyst_recognition,
        SignalType.community_buzz,
        SignalType.event_conference,
        SignalType.messaging_change,
        SignalType.industry_trend,
        SignalType.standard_spec,
        SignalType.legal_regulatory,
        SignalType.other,
    ),
}
LANES: dict[str, Lane] = {t.value: lane for lane, types in _LANE_TYPES.items() for t in types}


def lane_of(signal_type: str | None) -> Lane:
    """The swimlane of a signal type; an unclassified signal goes with "other"."""
    return LANES.get(signal_type or SignalType.other.value, "Market")


@dataclass(frozen=True)
class ProductOverlap:
    product: str
    category: str
    jfrog_products: list[str]
    level: OverlapLevel
    evidence: int
    signal_ids: list[int]  # the signals that gave evidence, newest first


@dataclass(frozen=True)
class WeekPoint:
    week_start: date
    value: float


@dataclass(frozen=True)
class Threat:
    level: ThreatLevel
    overlap: float | None  # None when insufficient: nothing reads as a measured 0
    momentum_trend: float | None  # None when insufficient, or with no baseline yet
    hiring_trend: float | None  # None without a board or under 30 days of board history
    explanation: str
    score: float | None = None  # overlap x momentum factor x hiring factor; None unscored


@dataclass(frozen=True)
class TimelineEvent:
    signal_id: int
    day: date
    lane: Lane
    importance: int
    title: str


@dataclass(frozen=True)
class SeriesPoint:
    """Share of voice: one competitor's counts in one week."""

    week_start: date
    competitor_slug: str
    documents: int
    signals: int


@dataclass(frozen=True)
class CompetitorIntel:
    """One competitor's deep-dive aggregates, from one signal scan (`competitor_intel`)."""

    overlap: list[ProductOverlap]
    momentum: list[WeekPoint]  # the collected weeks of MOMENTUM_WEEKS
    timeline: list[TimelineEvent]  # LOOKBACK_DAYS days, oldest first
    threat: Threat
    activity: BoardActivity | None = None  # the job-board inputs the threat read
    collected_since: date | None = None  # the day collection began (momentum's start)


@dataclass(frozen=True)
class OverviewIntel:
    """One competitor's row in the all-competitors list (`overview_intel`)."""

    threat: Threat  # level + explanation
    momentum: list[WeekPoint]  # the collected weeks of MOMENTUM_WEEKS
    overlap: float | None  # the threat's overlap driver; None when insufficient
    latest_move: TimelineEvent | None  # the newest visible signal of LOOKBACK_DAYS days
    activity: BoardActivity | None = None  # the job-board inputs the threat read
    collected_since: date | None = None  # the day collection began


@dataclass(frozen=True)
class SignalRow:
    """One visible signal linked to one competitor (not self), with its current analysis's
    `jfrog_impact` entries when asked for (empty when it has no current, usable analysis)."""

    id: int
    title: str
    signal_type: str | None
    importance: int
    day: date
    competitor_id: int
    competitor_slug: str
    competitor_tier: int
    impacts: tuple[ProductImpact, ...] = ()
    analysed: bool = False

    @property
    def event(self) -> TimelineEvent:
        return TimelineEvent(
            self.id, self.day, lane_of(self.signal_type), self.importance, self.title
        )


# ---- shared rules -------------------------------------------------------------------------


def week_starts(today: date, weeks: int) -> list[date]:
    """The first days of `weeks` whole 7-day windows ending today, oldest first."""
    n = max(weeks, 1)
    first = today - timedelta(days=7 * n - 1)
    return [first + timedelta(days=7 * i) for i in range(n)]


async def collected_since(session: AsyncSession, *, now: datetime) -> date | None:
    """The local day FrogEye filed its first document, the day collection began, as of
    `now` (a document filed later does not exist yet; one statement, on
    ix_documents_created_at); None before any document."""
    first = await session.scalar(
        select(func.min(Document.created_at)).where(Document.created_at <= now)
    )
    return local_day(first) if first is not None else None


def measured_weeks(starts: Sequence[date], collected: date | None) -> list[date]:
    """The weeks FrogEye collected in, those ending on or after the collected-since day
    (a suffix of `starts`); all of them when nothing bounds them (no document yet)."""
    if collected is None:
        return list(starts)
    return [w for w in starts if w + timedelta(days=6) >= collected]


def has_baseline(starts: Sequence[date], collected: date | None) -> bool:
    """A trend over these weeks compares with weeks that were all collected: the first
    starts on or after the collected-since day."""
    return collected is None or starts[0] >= collected


def _day(d: date) -> str:
    return f"{d.day} {d:%b} {d.year}"


def week_of(day: date, starts: Sequence[date]) -> int | None:
    i = (day - starts[0]).days // 7
    return i if 0 <= i < len(starts) else None


def since(today: date, days: int) -> date:
    """The first day of a `days`-day window ending today."""
    return today - timedelta(days=max(days, 1) - 1)


def weight(entries: Iterable[ProductImpact]) -> int:
    return sum(SEVERITY_WEIGHT.get(e.severity, 0) for e in entries)


def overlap_level(evidence: int, settings: Settings | None = None) -> OverlapLevel:
    s = settings or get_settings()
    if evidence >= s.overlap_high_evidence:
        return "high"
    if evidence >= s.overlap_medium_evidence:
        return "medium"
    return "low" if evidence >= 1 else "none"


def signal_day(tz: str) -> ColumnElement[date]:
    seen = func.date(func.timezone(tz, Signal.first_seen_at), type_=Date)
    return func.least(func.coalesce(Signal.event_date, seen), seen, type_=Date)


def document_day(tz: str) -> ColumnElement[date]:
    filed = Document.created_at
    moment = func.least(func.coalesce(Document.published_at, filed), filed)
    return func.date(func.timezone(tz, moment), type_=Date)


_IMPACTS = TypeAdapter(list[ProductImpact])
# `analysis_content`'s two usability checks, as JSON path tests on the stored content: a
# summary with text, and at least one "what happened" item with text
_HAS_TEXT = '? (@ like_regex "[^[:space:]]")'


def _has_text(content: ColumnElement[Any], path: str) -> ColumnElement[bool]:
    return func.jsonb_path_exists(content, cast(literal(f"{path} {_HAS_TEXT}"), JSONPATH))


async def signal_rows(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    competitor_ids: Sequence[int] | None = None,
    analyses: bool = False,
    naming: str | None = None,
) -> list[SignalRow]:
    """Visible signals whose day is in [start, end], one row per link to a competitor
    other than self, ordered by day then id (ONE statement; the signal scan every aggregate
    shares). With `analyses` each row carries its current analysis's jfrog_impact; `naming`
    keeps only signals whose latest analysis names that JFrog product (a JSONB pre-filter;
    the current/usable check still applies)."""
    day = signal_day(get_settings().timezone)
    day_col = day.label("day")
    stmt = (
        select(
            Signal.id,
            Signal.title,
            Signal.signal_type,
            Signal.importance,
            day_col,
            Competitor.id,
            Competitor.slug,
            Competitor.tier,
        )
        .join(SignalCompetitor, SignalCompetitor.signal_id == Signal.id)
        .join(
            Competitor,
            and_(Competitor.id == SignalCompetitor.competitor_id, Competitor.is_self.is_(False)),
        )
        .where(public_signal_clause(), day >= start, day <= end)
        .order_by(day_col, Signal.id, Competitor.id)
    )
    if competitor_ids is not None:
        stmt = stmt.where(SignalCompetitor.competitor_id.in_(list(competitor_ids)))
    if analyses:
        content = SignalAnalysis.content
        latest = (
            select(
                SignalAnalysis.version,
                content["jfrog_impact"].label("impact"),
                and_(
                    _has_text(content, "$.summary.text"),
                    _has_text(content, "$.what_happened[*].text"),
                ).label("usable"),
                SignalAnalysis.created_at,
            )
            .where(SignalAnalysis.signal_id == Signal.id)
            .order_by(SignalAnalysis.version.desc())
            .limit(1)
            .lateral("latest")
        )
        stmt = stmt.add_columns(
            Signal.analyzed_at,
            latest.c.version,
            latest.c.impact,
            latest.c.usable,
            latest.c.created_at,
        )
        if naming is None:
            stmt = stmt.outerjoin(latest, true())
        else:
            stmt = stmt.join(latest, true()).where(latest.c.impact.contains([{"product": naming}]))

    parsed: dict[int, tuple[tuple[ProductImpact, ...], bool]] = {}
    out: list[SignalRow] = []
    for row in (await session.execute(stmt)).all():
        sid, title, kind, importance, d, cid, slug, tier = row[:8]
        if sid not in parsed and analyses:
            extra: tuple[Any, ...] = tuple(row)[8:13]  # the add_columns above
            analyzed_at, version, impact, usable, created_at = extra
            parsed[sid] = _impacts(sid, analyzed_at, version, impact, usable, created_at)
        elif sid not in parsed:
            parsed[sid] = ((), False)
        impacts, analysed = parsed[sid]
        out.append(SignalRow(sid, title, kind, importance, d, cid, slug, tier, impacts, analysed))
    return out


def _impacts(
    signal_id: int,
    analyzed_at: datetime | None,
    version: int | None,
    impact: Any,
    usable: bool | None,
    created_at: datetime | None,
) -> tuple[tuple[ProductImpact, ...], bool]:
    """The jfrog_impact of the latest analysis when it is current and usable, by the rule
    every reader shares (analysis_store): current, usable (its two checks, read in
    SQL) and a `jfrog_impact` that parses (validated alone)."""
    if version is None:
        return (), False
    stored = SignalAnalysis(signal_id=signal_id, version=version, created_at=created_at)
    if not is_current(stored, analyzed_at) or not usable:
        return (), False
    try:
        return tuple(_IMPACTS.validate_python(impact or [])), True
    except ValidationError:
        return (), False


async def _scored(session: AsyncSession, competitor_id: int) -> bool | None:
    """True for a competitor that gets aggregates, False for the self competitor, None
    for an unknown id."""
    is_self = await session.scalar(select(Competitor.is_self).where(Competitor.id == competitor_id))
    return None if is_self is None else not is_self


async def _products(session: AsyncSession, competitor_id: int) -> list[tuple[str, str, list[str]]]:
    rows = await session.execute(
        select(CompetitorProduct.name, CompetitorProduct.category, CompetitorProduct.jfrog_products)
        .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
        .where(CompetitorProduct.competitor_id == competitor_id, Competitor.is_self.is_(False))
        .order_by(CompetitorProduct.id)
    )
    return [(name, category, list(jfrog)) for name, category, jfrog in rows.all()]


def momentum_trend(values: Sequence[float]) -> float | None:
    """The last 4 weeks' mean over the mean of the weeks before, at most TREND_CAP; None when
    the weeks before are empty (no baseline yet)."""
    recent, prior = values[-TREND_RECENT_WEEKS:], values[:-TREND_RECENT_WEEKS]
    recent_mean = sum(recent) / len(recent) if recent else 0.0
    prior_mean = sum(prior) / len(prior) if prior else 0.0
    if prior_mean == 0:
        return None
    return round(min(recent_mean / prior_mean, TREND_CAP), 2)


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else plural or word + 's'}"


# ---- pure builders over one competitor's rows ---------------------------------------------


def _overlap_from(
    products: Sequence[tuple[str, str, list[str]]],
    rows: Iterable[SignalRow],
    settings: Settings,
) -> list[ProductOverlap]:
    newest = sorted(rows, key=lambda r: (r.day, r.id), reverse=True)
    out = []
    for name, category, jfrog in products:
        counterparts = set(jfrog)
        evidence, ids = 0, []
        for r in newest:
            w = weight(e for e in r.impacts if e.product in counterparts)
            if w:
                evidence += w
                ids.append(r.id)
        level = overlap_level(evidence, settings)
        out.append(ProductOverlap(name, category, list(jfrog), level, evidence, ids))
    return out


def _momentum(rows: Iterable[SignalRow], starts: Sequence[date]) -> list[WeekPoint]:
    values = [0.0] * len(starts)
    for r in rows:
        i = week_of(r.day, starts)
        if i is not None:
            values[i] += r.importance
    return [WeekPoint(s, v) for s, v in zip(starts, values, strict=True)]


def _threat_from(
    rows: Sequence[SignalRow],
    activity: BoardActivity | None,
    today: date,
    settings: Settings,
    collected: date | None = None,
) -> Threat:
    """The threat of one competitor from its rows of the last LOOKBACK_DAYS days (analyses
    attached) and its job-board activity. A board counts only once it has been listed in full:
    before that it holds no hiring data. The momentum trend needs a full
    baseline after the collected-since day."""
    found = activity is not None and activity.has_board
    # a stale board (not listed in full for board_stale_days) is read as no board
    has_board = found and activity is not None and activity.measured
    stale_since = activity.last_listed_day if activity is not None and activity.stale else None
    signals = _plural(len(rows), "visible signal")
    if len(rows) < settings.threat_min_signals and not has_board:
        if stale_since is not None:
            board = f"the job board has not been readable since {_day(stale_since)}"
        elif found:
            board = "the job board found has not been listed in full yet"
        else:
            board = "no public job board found"
        return Threat(
            "insufficient",
            None,
            None,
            None,
            f"Insufficient data: {signals} in the last {LOOKBACK_DAYS} days "
            f"({settings.threat_min_signals} needed) and {board}.",
        )
    analysed = sum(r.analysed for r in rows)
    overlap = float(
        sum(
            weight(e for e in r.impacts if e.effect == "threat" and e.product in JFROG_PRODUCTS)
            for r in rows
        )
    )
    starts = week_starts(today, MOMENTUM_WEEKS)
    baseline = has_baseline(starts, collected)
    trend = momentum_trend([p.value for p in _momentum(rows, starts)]) if baseline else None

    hiring_trend: float | None = None
    open_now = open_then = 0
    if has_board and activity is not None:
        open_now, open_then = activity.open_now, activity.open_then
        # The same history rule as every 30-day change on the page (app.intel.hiring),
        # on the day the counts are as of
        since_day = board_since(activity.first_listed_at, settings.timezone)
        if has_history(since_day, activity.as_of_day or today):
            hiring_trend = round((open_now - open_then) / max(open_then, 1), 2)

    low, high = MOMENTUM_FACTOR
    m = 1.0 if trend is None else min(max(trend, low), high)
    h = 1.0 if hiring_trend is None else 1.0 + min(max(hiring_trend, -HIRING_FACTOR), HIRING_FACTOR)
    score = round(overlap * m * h, 2)
    level: ThreatLevel = (
        "high"
        if score >= settings.threat_high_score
        else "medium"
        if score >= settings.threat_medium_score
        else "low"
    )

    prior_weeks = MOMENTUM_WEEKS - TREND_RECENT_WEEKS
    if not baseline and collected is not None:
        momentum_text = (
            f"Momentum has no baseline yet: FrogEye has collected since {_day(collected)}, "
            f"under the {MOMENTUM_WEEKS} weeks a trend compares, so its factor is 1.00."
        )
    elif trend is None:
        momentum_text = (
            f"Momentum has no baseline yet (no visible signals in the {prior_weeks} weeks "
            f"before the last {TREND_RECENT_WEEKS}), so its factor is 1.00."
        )
    else:
        capped = " (capped)" if trend >= TREND_CAP else ""
        momentum_text = (
            f"The momentum trend {trend:.2f}{capped} compares weekly signal importance in the "
            f"last {TREND_RECENT_WEEKS} weeks with the {prior_weeks} weeks before (factor "
            f"bounded to {low:g}-{high:g})."
        )
    if not found:
        hiring_text = "Hiring is not counted: no public job board found."
    elif stale_since is not None:
        hiring_text = (
            f"Hiring is not counted: the job board has not been readable since {_day(stale_since)}."
        )
    elif not has_board:
        hiring_text = "Hiring is not counted: the job board has not been listed in full yet."
    elif hiring_trend is None:
        hiring_text = (
            f"Hiring is not counted: the job board has under {DELTA_DAYS} days of history."
        )
    else:
        hiring_text = (
            f"The hiring trend {hiring_trend:+.0%} compares "
            f"{_plural(open_now, 'open role')} now with {open_then} {DELTA_DAYS} days ago "
            f"(factor bounded to {1 - HIRING_FACTOR:g}-{1 + HIRING_FACTOR:g})."
        )
    analyses = _plural(analysed, "current analysis", "current analyses")
    explanation = " ".join(
        [
            f"{level.capitalize()}: score {score:.1f} = overlap {overlap:g} × momentum {m:.2f} "
            f"× hiring {h:.2f} (high from {settings.threat_high_score:g}, medium from "
            f"{settings.threat_medium_score:g}).",
            f"Overlap {overlap:g} is the severity-weighted sum (high 3, medium 2, low 1) of "
            f"threat impacts on JFrog products, from {analyses} of {signals} in "
            f"{LOOKBACK_DAYS} days.",
            momentum_text,
            hiring_text,
        ]
    )
    return Threat(level, overlap, trend, hiring_trend, explanation, score)


def _unscored(scored: bool | None) -> Threat:
    why = "Unknown competitor." if scored is None else "JFrog itself is never scored."
    return Threat("insufficient", None, None, None, why)


# ---- aggregates ---------------------------------------------------------------------------


async def competitor_intel(
    session: AsyncSession, competitor_id: int, *, now: datetime, is_self: bool | None = None
) -> CompetitorIntel:
    """Overlap, momentum, timeline and threat for one competitor from ONE signal scan (four
    statements in all: competitor, products, signals, job board), with the job-board
    activity the threat read. A caller that has loaded the competitor passes its `is_self`,
    which saves the first statement (three)."""
    scored = await _scored(session, competitor_id) if is_self is None else not is_self
    if not scored:
        return CompetitorIntel([], [], [], _unscored(scored))
    settings = get_settings()
    today = local_day(now, settings.timezone)
    products = await _products(session, competitor_id)
    rows = await signal_rows(
        session,
        start=since(today, LOOKBACK_DAYS),
        end=today,
        competitor_ids=[competitor_id],
        analyses=True,
    )
    activity = (await board_activity(session, [competitor_id], now=now)).get(competitor_id)
    collected = await collected_since(session, now=now)
    return CompetitorIntel(
        overlap=_overlap_from(products, rows, settings),
        momentum=_momentum(rows, measured_weeks(week_starts(today, MOMENTUM_WEEKS), collected)),
        timeline=[r.event for r in rows],
        threat=_threat_from(rows, activity, today, settings, collected),
        activity=activity,
        collected_since=collected,
    )


async def overview_intel(
    session: AsyncSession, competitor_ids: Sequence[int], *, now: datetime
) -> dict[int, OverviewIntel]:
    """The list view's threat, momentum, overlap and latest move for many competitors in
    four statements, however many there are (competitors, one signal scan, job boards, the
    collected-since day). The self competitor and unknown ids are absent."""
    if not competitor_ids:
        return {}
    ids = [
        cid
        for cid, is_self in (
            await session.execute(
                select(Competitor.id, Competitor.is_self).where(
                    Competitor.id.in_(list(competitor_ids))
                )
            )
        ).all()
        if not is_self
    ]
    if not ids:
        return {}
    settings = get_settings()
    today = local_day(now, settings.timezone)
    rows = await signal_rows(
        session, start=since(today, LOOKBACK_DAYS), end=today, competitor_ids=ids, analyses=True
    )
    activity = await board_activity(session, ids, now=now)
    collected = await collected_since(session, now=now)
    by_competitor: dict[int, list[SignalRow]] = {cid: [] for cid in ids}
    for r in rows:
        by_competitor[r.competitor_id].append(r)
    starts = measured_weeks(week_starts(today, MOMENTUM_WEEKS), collected)
    out: dict[int, OverviewIntel] = {}
    for cid, own in by_competitor.items():
        t = _threat_from(own, activity.get(cid), today, settings, collected)
        latest = max(own, key=lambda r: (r.day, r.importance, r.id), default=None)
        out[cid] = OverviewIntel(
            threat=t,
            momentum=_momentum(own, starts),
            overlap=t.overlap,
            latest_move=latest.event if latest else None,
            activity=activity.get(cid),
            collected_since=collected,
        )
    return out


async def product_overlap(
    session: AsyncSession, competitor_id: int, *, days: int = LOOKBACK_DAYS, now: datetime
) -> list[ProductOverlap]:
    """The competitor's seeded products (seed order) with their evidence-based overlap."""
    products = await _products(session, competitor_id)
    if not products:
        return []
    today = local_day(now)
    rows = await signal_rows(
        session, start=since(today, days), end=today, competitor_ids=[competitor_id], analyses=True
    )
    return _overlap_from(products, rows, get_settings())


async def momentum_series(
    session: AsyncSession, competitor_id: int, *, weeks: int = MOMENTUM_WEEKS, now: datetime
) -> list[WeekPoint]:
    """The importance-weighted count of visible signals per week (0 in a quiet week), over
    the weeks FrogEye collected in."""
    if not await _scored(session, competitor_id):
        return []
    today = local_day(now)
    starts = measured_weeks(week_starts(today, weeks), await collected_since(session, now=now))
    if not starts:
        return []
    rows = await signal_rows(session, start=starts[0], end=today, competitor_ids=[competitor_id])
    return _momentum(rows, starts)


async def timeline_events(
    session: AsyncSession, competitor_id: int, *, days: int = LOOKBACK_DAYS, now: datetime
) -> list[TimelineEvent]:
    """Every visible signal of the last `days` days in its lane, oldest first."""
    if not await _scored(session, competitor_id):
        return []
    today = local_day(now)
    rows = await signal_rows(
        session, start=since(today, days), end=today, competitor_ids=[competitor_id]
    )
    return [r.event for r in rows]


async def threat(session: AsyncSession, competitor_id: int, *, now: datetime) -> Threat:
    """The competitor's threat level and its drivers (see the module docstring)."""
    scored = await _scored(session, competitor_id)
    if not scored:
        return _unscored(scored)
    settings = get_settings()
    today = local_day(now, settings.timezone)
    rows = await signal_rows(
        session,
        start=since(today, LOOKBACK_DAYS),
        end=today,
        competitor_ids=[competitor_id],
        analyses=True,
    )
    activity = (await board_activity(session, [competitor_id], now=now)).get(competitor_id)
    return _threat_from(rows, activity, today, settings, await collected_since(session, now=now))


async def share_of_voice(
    session: AsyncSession,
    competitor_ids: Sequence[int],
    *,
    weeks: int = MOMENTUM_WEEKS,
    now: datetime,
) -> list[SeriesPoint]:
    """Weekly relevant documents and visible signals per competitor (self skipped), a
    point for every collected week and competitor, ordered by week then slug."""
    if not competitor_ids:
        return []
    collected = await collected_since(session, now=now)
    return await share_of_voice_since(
        session, competitor_ids, weeks=weeks, now=now, collected=collected
    )


async def share_of_voice_since(
    session: AsyncSession,
    competitor_ids: Sequence[int],
    *,
    weeks: int = MOMENTUM_WEEKS,
    now: datetime,
    collected: date | None,
) -> list[SeriesPoint]:
    """`share_of_voice` for a caller that has read the collected-since day (the profile, from
    `competitor_intel`): three statements."""
    if not competitor_ids:
        return []
    comps = dict(
        (
            await session.execute(
                select(Competitor.id, Competitor.slug).where(
                    Competitor.id.in_(list(competitor_ids)), Competitor.is_self.is_(False)
                )
            )
        ).all()
    )
    if not comps:
        return []
    tz = get_settings().timezone
    today = local_day(now, tz)
    starts = measured_weeks(week_starts(today, weeks), collected)
    if not starts:
        return []
    ids = list(comps)
    doc_day = document_day(tz)
    docs = (
        select(DocumentCompetitor.competitor_id.label("cid"), doc_day.label("day"))
        .join(Document, Document.id == DocumentCompetitor.document_id)
        .where(
            DocumentCompetitor.competitor_id.in_(ids),
            Document.status == DocStatus.relevant.value,
            ~self_news_document(),
            doc_day >= starts[0],
            doc_day <= today,
        )
        .subquery()
    )
    sig_day = signal_day(tz)
    sigs = (
        select(SignalCompetitor.competitor_id.label("cid"), sig_day.label("day"))
        .join(Signal, Signal.id == SignalCompetitor.signal_id)
        .where(
            SignalCompetitor.competitor_id.in_(ids),
            public_signal_clause(),
            sig_day >= starts[0],
            sig_day <= today,
        )
        .subquery()
    )
    counts: dict[tuple[int, int], list[int]] = {}
    for kind, sub in enumerate((docs, sigs)):  # 0 documents, 1 signals
        grouped = select(sub.c.cid, sub.c.day, func.count()).group_by(sub.c.cid, sub.c.day)
        for cid, d, n in (await session.execute(grouped)).all():
            i = week_of(d, starts)
            if i is not None:
                counts.setdefault((cid, i), [0, 0])[kind] += n
    order = sorted(comps.items(), key=lambda kv: kv[1])
    return [
        SeriesPoint(start, slug, *counts.get((cid, i), (0, 0)))
        for i, start in enumerate(starts)
        for cid, slug in order
    ]
