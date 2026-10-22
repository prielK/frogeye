"""Response schemas of the competitor and JFrog-product deep dives.

Every field is always sent (no defaults), so the generated client types make none optional.
A value FrogEye has not measured is null or an empty list, never 0: a
competitor without an enabled job board listed in full has null open roles and empty hiring
series, an insufficient threat has null overlap and momentum, and so on. Only a competitor's
current (enabled) job boards count; a replaced board's postings are never served.

Days are local days in `settings.timezone` (Asia/Jerusalem); weeks are whole 7-day windows
ending today, named by their first day (`app.intel.profile`). Brief citations are 1-based
source numbers into the brief's own `sources` list (as `SignalDetail.sources`).
"""

import datetime as dt
from typing import Literal, get_args

from pydantic import BaseModel, Field

from app.collectors.jobs.taxonomy import DEPARTMENTS, SENIORITIES
from app.models.enums import FactKind
from app.schemas.signals import CitedTextOut, SignalCard

ThreatLevel = Literal["high", "medium", "low", "insufficient"]
OverlapLevel = Literal["high", "medium", "low", "none"]
Lane = Literal["Launches", "Pricing", "Partners", "Money", "People", "Security", "Market"]
PressureTrend = Literal["rising", "steady", "easing", "none"]
FactKindName = Literal[
    "funding_round",
    "valuation",
    "headcount",
    "layoff",
    "revenue",
    "arr",
    "customer",
    "customer_count",
    "market_share",
    "acquisition",
    "leadership_change",
    "partnership",
    "office",
]
assert set(get_args(FactKindName)) == {k.value for k in FactKind}  # one taxonomy
Department = Literal[DEPARTMENTS]  # type: ignore[valid-type]
Seniority = Literal[SENIORITIES]  # type: ignore[valid-type]
IntelSourceKind = Literal["signal", "fact", "posting", "page"]
ProfileSection = Literal[
    "overview",
    "products",
    "momentum",
    "hiring",
    "headcount",
    "money",
    "market",
    "customers",
    "pricing",
    "community",
    "people_partners",
    "latest_signals",
    "sources",
]

# ---- shared ---------------------------------------------------------------------------------


_COLLECTED_SINCE = (
    "The local day FrogEye filed its first document: weekly series start at the week "
    "holding it, and a trend needs its 9 baseline weeks collected after it; null before any "
    "document."
)
_OPEN_ROLES = (
    "Open roles on its current job board(s) as of their last complete listing (as_of); "
    "null until an enabled board has been listed in full (a board found but not yet "
    "listed, or only listed in part, is pending) and null for a stale board, not listed in "
    "full for board_stale_days (3) days: open_roles_note / the hiring coverage note says "
    "which."
)
_DELTA_30D = (
    "Open roles on the as-of day minus 30 days before it; null until an enabled board has "
    "been listed in full, for a stale board (as open roles), or before the board has 30 days "
    "of history (board_since)."
)
_AS_OF = (
    "When the counts were read: the last complete listing of its job board(s) (the "
    "oldest, with several), usually today's run, yesterday's before it; null when no count "
    "is served."
)
_BOARD_CHECKED_AT = (
    "Without a board, the last time job-board discovery (an operator command; a miss "
    "is cached for 30 days) checked Greenhouse, Lever and Ashby and found none; null when it "
    "never recorded a check, or when a board is found."
)
_BOARD_SINCE = (
    "The board's first snapshot day: the day its current board was first listed in full"
    "; every hiring series starts here. Null until an enabled board has been listed "
    "in full."
)


class ThreatOut(BaseModel):
    """The deterministic threat level and its drivers ("How this is computed")."""

    level: ThreatLevel = Field(
        description='"insufficient": too few public signals and no job board listed in full.'
    )
    score: float | None = Field(description="overlap x momentum factor x hiring factor.")
    overlap: float | None = Field(
        description="Severity-weighted threat impacts on JFrog products in 180 days."
    )
    momentum_trend: float | None = Field(
        description="Last 4 weeks' mean weekly importance over the 9 weeks before; null with "
        "no baseline yet (those 9 weeks empty, or not all collected: collected_since) "
        "or when insufficient."
    )
    hiring_trend: float | None = Field(
        description="Open-roles change over 30 days as a ratio; null without a job board "
        "listed in full, or under 30 days of board history."
    )
    explanation: str = Field(description="Every driver with its number, in words.")


class WeekValueOut(BaseModel):
    week_start: dt.date
    value: float


class TimelineEventOut(BaseModel):
    """A visible signal on the swimlane; its analysis is at /signals/{signal_id}."""

    signal_id: int
    day: dt.date
    lane: Lane
    importance: int
    title: str


class FundingTotalOut(BaseModel):
    """The sum of the funding rounds that state an amount, in one currency. A named round
    (seed, "Series B") is one round across the months its sources date it to, within 120 days
    of its earliest source (two "seed" rounds years apart are two)."""

    amount: float
    currency: str | None = Field(description="ISO code; the currency most rounds state.")
    rounds: int = Field(description="Rounds in the sum.")
    undated_rounds: int = Field(
        description="Of those, rounds no source dates (not among money.funding_rounds, which "
        'are dated; they are in money.undated): "incl. N undated".'
    )
    excluded_rounds: int = Field(
        description="Rounds not in the sum: no amount stated, or another currency."
    )
    as_of: dt.date | None = Field(
        description="The newest dated round in the sum: how current the total is; "
        "null when every round in it is undated."
    )


class IntelSourceOut(BaseModel):
    """One numbered source of a brief."""

    n: int = Field(description="1-based source number; the brief's `cites` refer to it.")
    kind: IntelSourceKind
    ref: str = Field(description='"signal:<id>", "fact:<id>", "posting:<id>" or "page:<id>".')
    title: str = Field(description='"Signal not available" for a signal the reader may not see.')
    url: str | None = Field(
        description="/signals/<id> for a signal (null when not visible); the article, page or "
        "posting URL otherwise."
    )
    outlet: str | None
    day: dt.date | None = Field(
        description="A signal's day, a fact's as-of date, a posting's first-seen day, a page "
        "change's day."
    )
    excerpt: str | None = Field(description="A fact's verified quote; a page change's summary.")


class ProductEdgeOut(BaseModel):
    product: str = Field(description="The competitor's seeded product.")
    jfrog_product: str
    edge: CitedTextOut


# ---- facts ----------------------------------------------------------------------------------


class FactSourceOut(BaseModel):
    """One source's statement of a fact (facts are never averaged)."""

    fact_id: int
    value: float | None
    unit: str | None
    currency: str | None
    as_of: dt.date | None
    quote: str = Field(description="The verified quote, as the source writes it.")
    source_kind: Literal["document", "page_snapshot"]
    title: str = Field(description="The article's title, or '<Name> <kind> page'.")
    url: str | None
    outlet: str | None
    published_at: dt.datetime | None = Field(
        description="When the source was published (an article) or fetched (a page)."
    )


class FactOut(BaseModel):
    """A fact grouped by (kind, label, as-of month) over its sources: the value is
    the newest source's that states one; `sources` lists every source, newest first."""

    kind: FactKindName
    label: str
    value: float | None
    unit: str | None
    currency: str | None
    as_of: dt.date | None = Field(description="Null: an undated fact, never on a time axis.")
    sources_disagree: bool = Field(description="The sources state different values.")
    sources: list[FactSourceOut]


class FactPointOut(BaseModel):
    """A reported figure on a time axis: one point per distinct value of a dated fact, so two
    sources that disagree are two points, never an average."""

    kind: FactKindName
    label: str
    as_of: dt.date
    value: float | None
    unit: str | None
    currency: str | None
    sources_disagree: bool = Field(description="Its fact's sources state different values.")
    sources: list[FactSourceOut] = Field(description="The sources stating this value.")


class HeadcountPointOut(FactPointOut):
    layoff: bool = Field(description="A layoff marker (value: the number or % cut, if stated).")


class FundingRoundOut(BaseModel):
    """One dated funding round (a fact group); the amount is its newest source's."""

    as_of: dt.date
    amount: float | None = Field(description="Null: the amount was not disclosed.")
    currency: str | None
    label: str
    sources_disagree: bool
    sources: list[FactSourceOut]


# ---- competitor overview --------------------------------------------------------------------


class CompetitorSummary(BaseModel):
    """One row of GET /competitors/overview (JFrog itself is never listed)."""

    slug: str
    name: str
    tier: int
    relationship: str
    jfrog_products: list[str] = Field(
        description="JFrog products its seeded products compete with (JFROG_PRODUCTS order)."
    )
    product_categories: list[str] = Field(
        description="The categories of its seeded products (ProductOverlapOut.category), each "
        "once, in seed order: the competitor list's category filter."
    )
    threat: ThreatOut
    momentum: list[WeekValueOut] | None = Field(
        description="Up to 13 weekly importance sums, oldest first, from the week holding the "
        "collected-since day (no week before collection began); null when the threat is "
        "insufficient (never thirteen zeros)."
    )
    overlap: float | None = Field(description="The threat's overlap driver; null if insufficient.")
    open_roles: int | None = Field(description=_OPEN_ROLES)
    delta_30d: int | None = Field(description=_DELTA_30D)
    open_roles_as_of: dt.datetime | None = Field(description=_AS_OF)
    board_since: dt.date | None = Field(description=_BOARD_SINCE)
    has_board: bool
    board_checked_at: dt.datetime | None = Field(description=_BOARD_CHECKED_AT)
    board_state: Literal["measured", "pending", "stale", "none"] = Field(
        description="Why open_roles is (or is not) served: measured; pending (a board found, not "
        "listed in full yet); stale (not listed in full for board_stale_days); none (no "
        "board found)."
    )
    open_roles_note: str | None = Field(
        description='Why open_roles is null: "No public Greenhouse, Lever or Ashby board found" '
        '(with " (checked <day>)" when discovery recorded one), "Job board found — first '
        'snapshot pending" or "Job board not readable since <day>"; null when open_roles is '
        "measured."
    )
    funding_total: FundingTotalOut | None = Field(
        description="Null when no funding round states an amount."
    )
    latest_move: TimelineEventOut | None = Field(description="Its newest visible signal (180 d).")


# ---- competitor profile ---------------------------------------------------------------------


class CompetitorHeaderOut(BaseModel):
    """No logo is served: a third-party logo would make readers' browsers request
    other hosts."""

    slug: str
    name: str
    tier: int
    relationship: str
    description: str | None
    website: str | None
    ticker: str | None


class CompetitorKpisOut(BaseModel):
    threat: ThreatOut
    has_board: bool
    open_roles: int | None = Field(description=_OPEN_ROLES)
    delta_30d: int | None = Field(description=_DELTA_30D)
    latest_headcount: FactOut | None = Field(description="The newest-dated headcount fact.")
    funding_total: FundingTotalOut | None
    last_round: FundingRoundOut | None = Field(description="The newest-dated funding round.")


class CompetitorBriefOut(BaseModel):
    """The current "bottom line for JFrog" with its numbered sources."""

    version: int
    created_at: dt.datetime = Field(description='When it was written: the brief\'s "as of".')
    bottom_line: list[CitedTextOut]
    product_edges: list[ProductEdgeOut]
    watch: list[CitedTextOut]
    evidence: dict[str, int] = Field(description="Sources it was written from, by ref kind.")
    sources: list[IntelSourceOut]


class ProductOverlapOut(BaseModel):
    """A seeded product vs its JFrog counterparts, with evidence-based overlap."""

    product: str
    category: str
    url: str | None
    jfrog_products: list[str]
    level: OverlapLevel = Field(description='"none" reads "no recent evidence".')
    evidence: int = Field(description="Severity-weighted jfrog_impact entries in 180 days.")
    signal_ids: list[int] = Field(
        description="The analysed signals it is built from, newest first."
    )
    edges: list[ProductEdgeOut] = Field(description="The brief's edges for this product.")


class KeyCountOut(BaseModel):
    key: str
    count: int


class TagCountOut(BaseModel):
    slug: str = Field(description="Capability slug (GET /capabilities names it).")
    open: int
    delta_30d: int | None = Field(
        description=(
            "Its open roles now minus 30 days ago; null before the board has 30 days of history."
        )
    )


TagState = Literal["pending", "tagged", "untagged", "gave_up"]


class PostingOut(BaseModel):
    id: int
    title: str
    department: Department
    seniority: Seniority
    location: str | None
    country: str | None
    remote: bool
    url: str | None
    first_seen_at: dt.datetime
    closed_at: dt.datetime | None = Field(description="Null while the posting is open.")
    tags: list[str] = Field(description="Capability slugs.")
    tag_state: TagState = Field(
        description='The tagging state: "tagged" (tags found); "untagged" (tagged, and it '
        'names no product area: the explicit untagged state); "pending" (not tagged yet); '
        '"gave_up" (tagging failed on 3 days, not asked again until the posting changes).'
    )


class TagCoverageOut(BaseModel):
    """The open postings by tagging state: the tag counts are a share of `tagged`."""

    tagged: int
    pending: int
    untagged: int
    gave_up: int


class PostingPage(BaseModel):
    items: list[PostingOut]
    next_cursor: str | None = Field(description="Opaque keyset cursor; null on the last page.")


class DayCountOut(BaseModel):
    day: dt.date
    count: int


class DayKeyCountOut(BaseModel):
    day: dt.date
    key: str
    count: int


class HiringSeriesOut(BaseModel):
    """Open roles per day over 90 days, or from the board's first snapshot (`board_since`)
    when that is later, never zero-filled before it, to the board's last complete
    listing (a stale board's history ends there); empty until an enabled board has
    been listed in full. Every key has a point on every day of that window; location
    keeps its top 8 keys plus "Other"; a posting counts once per tag and untagged postings
    are absent from `tag`."""

    total: list[DayCountOut]
    department: list[DayKeyCountOut]
    seniority: list[DayKeyCountOut]
    location: list[DayKeyCountOut]
    tag: list[DayKeyCountOut]


class HiringOut(BaseModel):
    has_board: bool
    open_now: int | None = Field(description=_OPEN_ROLES)
    delta_30d: int | None = Field(description=_DELTA_30D)
    board_since: dt.date | None = Field(description=_BOARD_SINCE)
    as_of: dt.datetime | None = Field(description=_AS_OF)
    not_readable_since: dt.date | None = Field(
        description="A stale board's last complete listing day (no current count is "
        "served; its series end there); null otherwise."
    )
    board_checked_at: dt.datetime | None = Field(description=_BOARD_CHECKED_AT)
    by_department: list[KeyCountOut]
    by_seniority: list[KeyCountOut]
    by_location: list[KeyCountOut] = Field(description='Top 8 plus "Other".')
    tags: list[TagCountOut] = Field(
        description="Tags on the open postings that are tagged: a share of the board when "
        "tag_coverage has pending or given-up postings."
    )
    tag_coverage: TagCoverageOut | None = Field(
        description="The open postings by tagging state; null when open_now is."
    )
    recent: list[PostingOut] = Field(description="The 20 newest open postings.")
    series: HiringSeriesOut


class HeadcountOut(BaseModel):
    points: list[HeadcountPointOut] = Field(
        description="Reported figures only, oldest first: no line is drawn between them."
    )
    undated: list[FactOut]


class MoneyOut(BaseModel):
    funding_rounds: list[FundingRoundOut] = Field(description="Dated rounds, oldest first.")
    valuations: list[FactPointOut]
    revenue: list[FactPointOut] = Field(description="Revenue and ARR points, oldest first.")
    undated: list[FactOut] = Field(description="Undated rounds, valuations, revenue and ARR.")


class VoicePointOut(BaseModel):
    week_start: dt.date
    competitor_slug: str
    documents: int
    signals: int


class MarketOut(BaseModel):
    market_share: list[FactOut] = Field(description="Dated market-share facts, newest first.")
    undated: list[FactOut]
    peers: list[str] = Field(
        description="The 4 competitors sharing the most JFrog counterparts with it (ties: the "
        "closest tier, then slug); never JFrog."
    )
    share_of_voice: list[VoicePointOut] = Field(
        description="Up to 13 weeks for it and each peer, from the week holding the "
        "collected-since day: relevant documents and visible signals."
    )


class CustomersOut(BaseModel):
    facts: list[FactOut] = Field(description="Named customers and customer counts, newest first.")
    undated: list[FactOut]
    signals: list[SignalCard] = Field(
        description="Public customer win/loss signals the reader may see, newest first."
    )


class PageChangeOut(BaseModel):
    """A meaningful change to a watched pricing page (or one judged a pricing/packaging
    change on another page), with its diff ("-" removed, "+" added)."""

    snapshot_id: int
    day: dt.date
    page_kind: str | None
    change_kind: str | None
    summary: str | None
    diff: str | None = Field(
        description="The stored diff's first whole lines, at most 4,000 characters "
        "(changed lines with their context); the whole of it at "
        "GET /competitors/{slug}/page-changes/{snapshot_id}/diff when `truncated`."
    )
    truncated: bool = Field(description="The diff served is the start of a longer one.")
    url: str


class PageDiffOut(BaseModel):
    """The whole stored diff of one page change."""

    snapshot_id: int
    diff: str = Field(description='The unified diff ("-" removed, "+" added), as stored.')


class DayValueOut(BaseModel):
    day: dt.date
    value: float


class RepoSeriesOut(BaseModel):
    repo: str = Field(description="owner/name")
    stars: list[DayValueOut]
    forks: list[DayValueOut]


class WeekCountOut(BaseModel):
    week_start: dt.date
    count: int


class CommunityOut(BaseModel):
    repos: list[RepoSeriesOut] = Field(description="GitHub stars/forks per day over 180 days.")
    mentions: list[WeekCountOut] = Field(
        description="Relevant Hacker News items about it per week: up to 13 weeks, from the "
        "week holding collected_since; empty when no Hacker News search runs for it."
    )


class PeoplePartnersOut(BaseModel):
    facts: list[FactOut] = Field(
        description="Leadership changes, acquisitions, partnerships and offices, newest first."
    )
    undated: list[FactOut]
    signals: list[SignalCard] = Field(
        description="Public leadership, acquisition and partnership signals the reader may see, "
        "newest first."
    )


class SourceFreshnessOut(BaseModel):
    kind: str
    name: str
    health: str = Field(description="ok | degraded | down")
    last_fetched_at: dt.datetime | None


class CoverageNoteOut(BaseModel):
    section: ProfileSection
    text: str = Field(
        description='Why the section is empty or partial, said plainly ("No public job board '
        'found", "Job board found — first snapshot pending").'
    )


class SourcesFreshnessOut(BaseModel):
    sources: list[SourceFreshnessOut] = Field(description="Its enabled sources.")
    notes: list[CoverageNoteOut] = Field(
        description="At least one per empty section (none for a section with data), in "
        "section order."
    )


class CompetitorProfile(BaseModel):
    """GET /competitors/{slug}/profile: the deep dive, identical for every persona. Its signal cards
    (latest_signals, customers.signals, people_partners.signals) and the brief's cited signals are
    its public signals (status auto|approved, never JFrog scope) that the reader may also see, the
    same rule as the product page. Every empty
    section has a coverage note in `sources_freshness.notes` saying why."""

    competitor: CompetitorHeaderOut
    kpis: CompetitorKpisOut
    brief: CompetitorBriefOut | None = Field(description="Null: see the overview coverage note.")
    products: list[ProductOverlapOut]
    momentum: list[WeekValueOut] | None = Field(
        description="Up to 13 weeks, from the week holding collected_since; null when "
        "the threat is insufficient."
    )
    timeline: list[TimelineEventOut] = Field(
        description="180 days of visible signals, oldest first."
    )
    hiring: HiringOut
    headcount: HeadcountOut
    money: MoneyOut
    market: MarketOut
    customers: CustomersOut
    pricing: list[PageChangeOut] = Field(description="Newest first.")
    community: CommunityOut
    people_partners: PeoplePartnersOut
    latest_signals: list[SignalCard] = Field(
        description="Its 20 newest public signals the reader may see (the same for everyone)."
    )
    collected_since: dt.date | None = Field(description=_COLLECTED_SINCE)
    sources_freshness: SourcesFreshnessOut


# ---- JFrog products -------------------------------------------------------------------------


class ProductSummary(BaseModel):
    slug: str = Field(description='Kebab-case of the name ("JFrog ML" is "jfrog-ml").')
    name: str
    description: str | None = Field(
        description="What it does, from the JFrog context pack's portfolio (capabilities only)."
    )
    pressure: PressureTrend = Field(
        description="Threat pressure over 90 days: the last 4 weeks vs the 9 before. A trend "
        "only when pressure_baseline is true; otherwise threat in the collected weeks reads "
        '"rising" as new pressure, and the page says there is no baseline yet, never '
        '"rising".'
    )
    pressure_baseline: bool = Field(
        description="Whether the 9 weeks the pressure compares with were all collected"
        " and hold threat (as the momentum trend): false while FrogEye has "
        "collected for under 13 weeks, or when those 9 weeks name no threat."
    )
    top_competitors: list[str] = Field(
        description="Up to 3 competitor slugs, by evidence, then tier, then slug."
    )


class WeekPressureOut(BaseModel):
    week_start: dt.date
    threat: float = Field(description="Severity-weighted threat impacts naming the product.")
    opportunity: float


class ProductCompetitorOut(BaseModel):
    competitor_slug: str
    their_products: list[str] = Field(description="Their seeded products countering it.")
    threat_level: OverlapLevel = Field(description="From its threat impacts in 180 days.")
    evidence: int
    last_move: TimelineEventOut | None


class BriefActionsOut(BaseModel):
    sales: list[str]
    pm: list[str]
    exec: list[str]
    pmm_devrel: list[str]


class ProductBriefOut(BaseModel):
    version: int
    created_at: dt.datetime = Field(description='When it was written: the brief\'s "as of".')
    bottom_line: list[CitedTextOut]
    gaps_parity: list[CitedTextOut]
    actions: BriefActionsOut = Field(description="Every role's actions (persona-agnostic).")
    evidence: dict[str, int]
    sources: list[IntelSourceOut]


class ProductTimelineEventOut(TimelineEventOut):
    competitor_slug: str = Field(description="The swimlane: one event per competitor it names.")


class AreaBoardOut(BaseModel):
    competitor_slug: str
    board_since: dt.date | None = Field(
        description="Its board's first snapshot day, the day it was first listed in full"
        ": its line starts here. Always set, as only boards listed in full are read."
    )
    last_listed: dt.date = Field(description="Its board's latest complete listing day.")
    stale: bool = Field(
        description="Not listed in full for board_stale_days (3) days, so it is not "
        "counted in the series (as if it had no board)."
    )


class HiringAreaOut(BaseModel):
    """Competitor postings tagged with the product's capabilities, over 90 days, from the
    competitors whose enabled job board has been listed in full (JFrog never). Each competitor's
    line starts at its board's first snapshot and the total at the earliest board's: no day before a
    board was read is drawn, let alone as zero."""

    capabilities: list[str] = Field(description="Capability slugs mapped to its KB page.")
    boards: list[AreaBoardOut] = Field(
        description="The competitors' job boards (each listed in full), by first "
        "snapshot, then slug; a stale one is listed but not counted."
    )
    total: list[DayCountOut] = Field(
        description="Each posting once, from the earliest board's first snapshot to the last "
        "day every board counted was listed in full; empty without a board or a "
        "capability."
    )
    by_competitor: list[DayKeyCountOut] = Field(
        description="Every competitor with an area posting in the window, each from its "
        "board's first snapshot; no server-side Other (the chart folds on the client)."
    )


class ProductProfile(BaseModel):
    """GET /products/{slug}: no JFrog company figures anywhere; identical for every
    persona. Its signal cards and the brief's cited signals are the product's
    public signals (status auto|approved, never JFrog scope) that the reader may also see,
    the same rule as the competitor deep dive."""

    slug: str
    name: str
    description: str | None = Field(description="From the JFrog context pack's portfolio.")
    pressure: PressureTrend = Field(description="As ProductSummary.pressure.")
    pressure_baseline: bool = Field(description="As ProductSummary.pressure_baseline.")
    pressure_series: list[WeekPressureOut] = Field(
        description="Up to 13 weeks, oldest first, from the week holding collected_since"
        "; empty when no signal names the product."
    )
    collected_since: dt.date | None = Field(description=_COLLECTED_SINCE)
    competitors: list[ProductCompetitorOut]
    brief: ProductBriefOut | None
    hiring_in_area: HiringAreaOut
    timeline: list[ProductTimelineEventOut] = Field(
        description="180 days of visible signals naming it, oldest first."
    )
    signals: list[SignalCard] = Field(
        description="Its 30 newest public signals the reader may see (the same for everyone)."
    )
