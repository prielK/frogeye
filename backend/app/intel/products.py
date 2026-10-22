"""Per-JFrog-product views, computed with no LLM: who competes with a JFrog product,
the weekly pressure on it, and its signals.

All three read the SAME signal set as the competitor overlap: the current analyses
 of every visible signal (status auto|approved, competitor or industry
scope, JFrog scope hidden), attributed to the competitors other than JFrog that
`signal_competitors` links it to. A `jfrog_impact` entry naming the product is the evidence,
so a seeded product's overlap evidence equals the sum, over its JFrog counterparts, of that
competitor's evidence here. A signal linked to no competitor is in neither. Weights, days,
weeks and levels are those of `app.intel.profile`:

* `product_competitors`: the competitors with a seeded product countering it, plus those with
  evidence in `days`. evidence = the weight sum of the entries naming it (any effect);
  threat_level = the overlap level (high from 6, medium from 3, low from 1) of the weight sum
  of its threat entries (`threat_weight`), each signal naming it as a threat listed with its
  weight and highest severity (`threat_signals`, newest first); last_move = the newest
  signal naming it. Ordered by evidence (highest first), then tier, then slug.
* `product_pressure`: per week, the weight sums of the threat entries and of the opportunity
  entries naming it (neutral ones are not pressure), each signal counted once however many
  competitors it is about; only the weeks FrogEye collected in (`measured_weeks`).
* `product_signals`: the signals naming it, newest first.
* `pressure_trend` ("How this is computed"): the mean weekly THREAT weight of the
  last 4 weeks over that of the 9 weeks before (`momentum_trend`'s rule): "rising" from
  `pressure_rising_ratio` (1.25); "easing" at or under `pressure_easing_ratio` (0.8); else
  "steady"; "none" with no threat in 13 weeks. The word is a trend only with a baseline, as
  the competitor momentum: the 9 weeks compared with were all collected
  (`has_baseline`) and name threat. Without one, `baseline` is False and threat in the
  collected weeks reads "rising" only as new pressure, in the API's order; readers say "no
  baseline yet", never "rising". A product with no threat at all keeps its baseline: there
  is nothing to compare.
* `products_intel`: all of the above for many products from ONE signal scan (three
  statements however many products: the seed, the scan, the collected-since day), plus each
  product's events (one per signal and competitor link, for a swimlane by competitor), equal
  to the single views.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import JFROG_PRODUCTS, ProductImpact
from app.core.config import Settings, get_settings
from app.core.days import local_day
from app.intel.profile import (
    LOOKBACK_DAYS,
    MOMENTUM_WEEKS,
    SEVERITY_WEIGHT,
    TREND_RECENT_WEEKS,
    OverlapLevel,
    SignalRow,
    TimelineEvent,
    collected_since,
    has_baseline,
    measured_weeks,
    momentum_trend,
    overlap_level,
    signal_rows,
    since,
    week_of,
    week_starts,
    weight,
)
from app.models import Competitor, CompetitorProduct

PressureTrend = Literal["rising", "steady", "easing", "none"]
Naming = list[tuple[SignalRow, list[ProductImpact]]]
SeededRow = tuple[int, str, int, str]  # (competitor id, slug, tier, seeded product name)


@dataclass(frozen=True)
class ThreatEvidence:
    """One signal whose current analysis names the product as threatened: what it adds to the
    threat weight, and the highest severity it names."""

    event: TimelineEvent
    weight: int
    severity: str


@dataclass(frozen=True)
class ProductCompetitor:
    competitor_slug: str
    their_products: list[str]  # seeded products countering this JFrog product (seed order)
    threat_level: OverlapLevel
    last_move: TimelineEvent | None
    evidence: int
    threat_weight: int = 0  # the weight sum threat_level is read from
    threat_signals: tuple[ThreatEvidence, ...] = ()  # newest first


@dataclass(frozen=True)
class WeekPressure:
    week_start: date
    threat: float
    opportunity: float


@dataclass(frozen=True)
class ProductEvent:
    """One signal naming the product, in the lane of one competitor it is about."""

    competitor_slug: str
    event: TimelineEvent


@dataclass(frozen=True)
class ProductIntel:
    product: str
    competitors: list[ProductCompetitor]  # = product_competitors
    pressure: list[WeekPressure]  # = product_pressure (the collected weeks)
    trend: PressureTrend  # pressure_trend(pressure, baseline=baseline)
    signal_ids: list[int]  # = product_signals, newest first
    events: list[ProductEvent]  # every (signal, competitor) of `days` days, oldest first
    baseline: bool = True  # the trend's 9 baseline weeks were collected, with threat
    collected_since: date | None = None  # the day collection began


@dataclass
class _Tally:
    slug: str
    tier: int
    products: list[str] = field(default_factory=list)
    evidence: int = 0
    threat: int = 0
    last: SignalRow | None = None
    threats: list[ThreatEvidence] = field(default_factory=list)


def _check(product: str) -> None:
    if product not in JFROG_PRODUCTS:
        raise ValueError(f"unknown JFrog product {product!r}")


def _entries(rows: Iterable[SignalRow], product: str) -> Naming:
    """(row, the entries naming the product) for each row whose analysis names it."""
    out = []
    for r in rows:
        entries = [e for e in r.impacts if e.product == product]
        if entries:
            out.append((r, entries))
    return out


async def _naming(session: AsyncSession, product: str, *, start: date, end: date) -> Naming:
    """(row, the entries naming the product) for each competitor link of each signal whose
    current analysis names it (one statement)."""
    rows = await signal_rows(session, start=start, end=end, analyses=True, naming=product)
    return _entries(rows, product)


async def _seeded(
    session: AsyncSession, products: Sequence[str]
) -> list[tuple[SeededRow, tuple[str, ...]]]:
    """Each seeded product countering any of `products`, with its JFrog counterparts, in
    seed order (one statement)."""
    rows = await session.execute(
        select(
            Competitor.id,
            Competitor.slug,
            Competitor.tier,
            CompetitorProduct.name,
            CompetitorProduct.jfrog_products,
        )
        .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
        .where(
            CompetitorProduct.jfrog_products.overlap(list(products)),
            Competitor.is_self.is_(False),
        )
        .order_by(CompetitorProduct.id)
    )
    return [((cid, slug, tier, name), tuple(jfrog)) for cid, slug, tier, name, jfrog in rows.all()]


def _competitors_from(
    seeded: Iterable[SeededRow],
    naming: Naming,
    start: date,
    settings: Settings,
) -> list[ProductCompetitor]:
    tallies: dict[int, _Tally] = {}
    for cid, slug, tier, name in seeded:
        tallies.setdefault(cid, _Tally(slug, tier)).products.append(name)
    for row, entries in naming:
        if row.day < start:
            continue
        t = tallies.setdefault(row.competitor_id, _Tally(row.competitor_slug, row.competitor_tier))
        t.evidence += weight(entries)
        threats = [e for e in entries if e.effect == "threat"]
        if threats:
            w = weight(threats)
            t.threat += w
            t.threats.append(ThreatEvidence(row.event, w, _highest(threats)))
        if t.last is None or (row.day, row.id) > (t.last.day, t.last.id):
            t.last = row
    ranked = sorted(tallies.values(), key=lambda t: (-t.evidence, t.tier, t.slug))
    return [
        ProductCompetitor(
            t.slug,
            t.products,
            overlap_level(t.threat, settings),
            t.last.event if t.last else None,
            t.evidence,
            t.threat,
            tuple(sorted(t.threats, key=lambda x: (x.event.day, x.event.signal_id), reverse=True)),
        )
        for t in ranked
    ]


def _highest(entries: Sequence[ProductImpact]) -> str:
    return max((e.severity for e in entries), key=lambda s: SEVERITY_WEIGHT.get(s, 0))


def _pressure_from(naming: Naming, starts: Sequence[date]) -> list[WeekPressure]:
    threat = [0.0] * len(starts)
    opportunity = [0.0] * len(starts)
    counted: set[int] = set()
    for row, entries in naming:
        i = week_of(row.day, starts)
        if row.id in counted or i is None:
            continue
        counted.add(row.id)
        threat[i] += weight(e for e in entries if e.effect == "threat")
        opportunity[i] += weight(e for e in entries if e.effect == "opportunity")
    return [WeekPressure(s, t, o) for s, t, o in zip(starts, threat, opportunity, strict=True)]


def _signal_ids_from(naming: Naming, start: date) -> list[int]:
    rows = {row.id: row for row, _ in naming if row.day >= start}
    return [r.id for r in sorted(rows.values(), key=lambda r: (r.day, r.id), reverse=True)]


def pressure_baseline(weeks: Sequence[WeekPressure], *, collected: bool = True) -> bool:
    """Whether the pressure word is a trend: the 9 weeks it compares with were all
    collected and name threat, as the competitor momentum's baseline; a product with
    no threat at all keeps it (there is nothing to compare)."""
    threat = [w.threat for w in weeks]
    if not any(threat):
        return collected
    return collected and any(threat[:-TREND_RECENT_WEEKS])


def pressure_trend(
    weeks: Sequence[WeekPressure], settings: Settings | None = None, *, baseline: bool = True
) -> PressureTrend:
    """Rising, steady or easing threat pressure (see the module docstring). Without a
    collected baseline threat can only read as new: "rising"."""
    s = settings or get_settings()
    threat = [w.threat for w in weeks]
    if not any(threat):
        return "none"
    trend = momentum_trend(threat) if baseline else None
    if trend is None or trend >= s.pressure_rising_ratio:
        return "rising"  # None: threat with no baseline yet, which is new pressure
    return "easing" if trend <= s.pressure_easing_ratio else "steady"


async def product_competitors(
    session: AsyncSession, jfrog_product: str, *, days: int = LOOKBACK_DAYS, now: datetime
) -> list[ProductCompetitor]:
    _check(jfrog_product)
    today = local_day(now)
    start = since(today, days)
    seeded = [row for row, _ in await _seeded(session, [jfrog_product])]
    naming = await _naming(session, jfrog_product, start=start, end=today)
    return _competitors_from(seeded, naming, start, get_settings())


async def product_pressure(
    session: AsyncSession, jfrog_product: str, *, weeks: int = MOMENTUM_WEEKS, now: datetime
) -> list[WeekPressure]:
    _check(jfrog_product)
    today = local_day(now)
    starts = measured_weeks(week_starts(today, weeks), await collected_since(session, now=now))
    if not starts:
        return []
    return _pressure_from(await _naming(session, jfrog_product, start=starts[0], end=today), starts)


async def product_signals(
    session: AsyncSession, jfrog_product: str, *, days: int = LOOKBACK_DAYS, now: datetime
) -> list[int]:
    _check(jfrog_product)
    today = local_day(now)
    start = since(today, days)
    return _signal_ids_from(await _naming(session, jfrog_product, start=start, end=today), start)


async def products_intel(
    session: AsyncSession,
    products: Sequence[str],
    *,
    days: int = LOOKBACK_DAYS,
    weeks: int = MOMENTUM_WEEKS,
    now: datetime,
) -> dict[str, ProductIntel]:
    """Every listed product's views from one scan (see the module docstring), in the given
    order. Raises ValueError for a name outside JFROG_PRODUCTS."""
    wanted = list(dict.fromkeys(products))
    for p in wanted:
        _check(p)
    if not wanted:
        return {}
    settings = get_settings()
    today = local_day(now)
    start, every_week = since(today, days), week_starts(today, weeks)
    collected = await collected_since(session, now=now)
    starts = measured_weeks(every_week, collected)
    baseline = has_baseline(every_week, collected)
    seeded = await _seeded(session, wanted)
    rows = await signal_rows(
        session,
        start=min(start, starts[0]) if starts else start,
        end=today,
        analyses=True,
        naming=wanted[0] if len(wanted) == 1 else None,  # the JSONB pre-filter for one
    )
    out: dict[str, ProductIntel] = {}
    for p in wanted:
        naming = _entries(rows, p)
        pressure = _pressure_from(naming, starts) if starts else []
        based = pressure_baseline(pressure, collected=baseline)
        out[p] = ProductIntel(
            product=p,
            competitors=_competitors_from(
                [row for row, jfrog in seeded if p in jfrog], naming, start, settings
            ),
            pressure=pressure,
            trend=pressure_trend(pressure, settings, baseline=based),
            signal_ids=_signal_ids_from(naming, start),
            events=[ProductEvent(r.competitor_slug, r.event) for r, _ in naming if r.day >= start],
            baseline=based,
            collected_since=collected,
        )
    return out
