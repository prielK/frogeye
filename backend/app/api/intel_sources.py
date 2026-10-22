"""Reader-side sources of the deep dives: company facts with every source, and the
numbered Sources list of a weekly brief (the `analysis_view` pattern for brief refs).

Facts. `company_facts` keeps one row per source, so a fact said by two
sources is two rows that may disagree. Readers group the rows by (competitor, kind,
label_norm, as-of month); an undated fact (as_of null) is grouped by (kind, label_norm) alone
and never goes on a time axis. A group lists every source, newest first (a source's
date is its document's publication or filing time, or its page snapshot's fetch time); its
value is that of the newest source stating one (a source that states none, such as an
undisclosed round, contradicts nothing); `sources_disagree` is set when its sources state
different values. On a time axis a group is one point per distinct value, so two sources
that disagree are two points, never an average.
A named funding round (seed, pre-seed, angel, "Series X") is one event across months, so its
sources form ONE group: a round announced on 30 May and syndicated on 2 June is one
round, dated by its earliest dated source. Only sources within `ROUND_WINDOW_DAYS` (120) of
that earliest one join it: two "seed" rounds years apart are two rounds. Undated
sources of the label join the round when there is one; between two or more they cannot be
placed, so they stay one undated group, which the total does not count again. Other rounds
("debt financing") keep the month rule, as two debt financings months apart are two.
The funding total sums the funding-round groups that state an amount, in the currency most of
them state, and says how many of them are undated; an undated unnamed round whose
label also names a dated round is that round, not a second one. JFrog itself never has facts: rows
of the self competitor are never read.

Brief sources. A brief stores raw refs ("signal:12", "fact:3", "posting:5", "page:7"). The API
numbers them 1..N in first-cited order (bottom line, then edges, then watch items or gaps) and maps
every `cites` list to those numbers; a ref that resolves to nothing (a deleted row, another
competitor's row, an unknown kind) is dropped from the cites. A cited signal that is not public now,
or that the reader may not see, keeps its number but shows `UNAVAILABLE_TITLE` and no link, as on
the analysis page; the deep dives read the same for every persona. Facts resolve from the facts the
caller already loaded; signals, postings and pages from ONE statement.
"""

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import cast as typed

from sqlalchemy import Date, Text, and_, cast, func, literal, null, select, true, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.agents.analysis_schema import CitedText
from app.api.analysis_view import UNAVAILABLE_TITLE
from app.api.visibility import public_signal_clause, visible_signal_clause
from app.collectors.attribution import display_title, host_of, source_label
from app.core.config import get_settings
from app.intel.profile import signal_day
from app.models import (
    CompanyFact,
    Competitor,
    Document,
    JobPosting,
    PageSnapshot,
    RawItem,
    Signal,
    Source,
    User,
)
from app.models.enums import FactKind
from app.schemas.intel import (
    FactKindName,
    FactOut,
    FactPointOut,
    FactSourceOut,
    FundingRoundOut,
    FundingTotalOut,
    HeadcountPointOut,
    IntelSourceKind,
    IntelSourceOut,
)
from app.schemas.signals import CitedTextOut

FACT_KIND_LABELS: dict[str, str] = {
    FactKind.funding_round: "Funding round",
    FactKind.valuation: "Valuation",
    FactKind.headcount: "Headcount",
    FactKind.layoff: "Layoff",
    FactKind.revenue: "Revenue",
    FactKind.arr: "ARR",
    FactKind.customer: "Customer",
    FactKind.customer_count: "Customer count",
    FactKind.market_share: "Market share",
    FactKind.acquisition: "Acquisition",
    FactKind.leadership_change: "Leadership change",
    FactKind.partnership: "Partnership",
    FactKind.office: "Office",
}
# Labels that only repeat their kind ("employees" for a headcount): the title is the kind.
_GENERIC_LABELS = frozenset({"employees", "valuation", "arr", "layoff", "customers"})
POSTING_OUTLET = "Public job board"
# A named round's normalised label (normalise_label strips round/funding/financing).
_NAMED_ROUND = re.compile(r"(?:pre seed|seed|angel|series [a-z](?: ?\d{1,2})?)")
ROUND_WINDOW_DAYS = 120  # a named round's sources lie within this of its earliest one


def named_round(kind: str, label_norm: str) -> bool:
    return kind == FactKind.funding_round and _NAMED_ROUND.fullmatch(label_norm) is not None


# ---- facts --------------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedFact:
    """One verified fact row with its source, as readers show it."""

    id: int
    competitor_id: int
    kind: FactKindName
    label: str
    label_norm: str
    value: float | None
    unit: str | None
    currency: str | None
    as_of: date | None
    quote: str
    source: FactSourceOut

    @property
    def title(self) -> str:
        kind = FACT_KIND_LABELS.get(self.kind, self.kind.replace("_", " ").capitalize())
        return kind if self.label.casefold() in _GENERIC_LABELS else f"{kind}: {self.label}"

    @property
    def figure(self) -> tuple[float | None, str | None, str | None]:
        return (self.value, self.unit, self.currency)


async def load_facts(
    session: AsyncSession, competitor_ids: Sequence[int], *, kinds: Collection[str] | None = None
) -> list[LoadedFact]:
    """The verified facts of these competitors (never the self one), each with its source
    document or page snapshot, in ONE statement."""
    if not competitor_ids:
        return []
    doc_source = aliased(Source)
    page_source = aliased(Source)
    stmt = (
        select(
            CompanyFact,
            Competitor.name,
            Document.title,
            Document.canonical_url,
            func.coalesce(Document.published_at, Document.created_at),
            RawItem.url,
            RawItem.payload,
            doc_source.kind,
            doc_source.name,
            PageSnapshot.page_kind,
            PageSnapshot.fetched_at,
            func.coalesce(page_source.config["url"].astext, page_source.url),
        )
        .join(
            Competitor,
            and_(Competitor.id == CompanyFact.competitor_id, Competitor.is_self.is_(False)),
        )
        .outerjoin(
            Document,
            and_(CompanyFact.source_kind == "document", Document.id == CompanyFact.source_id),
        )
        .outerjoin(RawItem, RawItem.id == Document.raw_item_id)
        .outerjoin(doc_source, doc_source.id == Document.source_id)
        .outerjoin(
            PageSnapshot,
            and_(
                CompanyFact.source_kind == "page_snapshot",
                PageSnapshot.id == CompanyFact.source_id,
            ),
        )
        .outerjoin(page_source, page_source.id == PageSnapshot.source_id)
        .where(CompanyFact.competitor_id.in_(list(competitor_ids)), CompanyFact.verified.is_(True))
        .order_by(CompanyFact.id)
    )
    if kinds is not None:
        stmt = stmt.where(CompanyFact.kind.in_(list(kinds)))
    out = []
    for row in (await session.execute(stmt)).all():
        f, company, title, canonical, doc_at, raw_url, payload, kind, name = row[:9]
        page_kind, fetched_at, page_url = row[9:]
        if f.source_kind == "document":
            url = raw_url or canonical
            source = FactSourceOut(
                fact_id=f.id,
                value=f.value_num,
                unit=f.unit,
                currency=f.currency,
                as_of=f.as_of,
                quote=f.quote,
                source_kind="document",
                title=display_title(title, url or ""),
                url=url,
                outlet=source_label(kind, name, payload) if name else None,
                published_at=doc_at,
            )
        else:
            source = FactSourceOut(
                fact_id=f.id,
                value=f.value_num,
                unit=f.unit,
                currency=f.currency,
                as_of=f.as_of,
                quote=f.quote,
                source_kind="page_snapshot",
                title=f"{company} {page_kind or 'watched'} page",
                url=page_url,
                outlet=host_of(page_url) if page_url else None,
                published_at=fetched_at,
            )
        out.append(
            LoadedFact(
                id=f.id,
                competitor_id=f.competitor_id,
                kind=typed(FactKindName, f.kind),
                label=f.label,
                label_norm=f.label_norm,
                value=f.value_num,
                unit=f.unit,
                currency=f.currency,
                as_of=f.as_of,
                quote=f.quote,
                source=source,
            )
        )
    return out


def _newest_first(fact: LoadedFact) -> tuple[bool, datetime | None, int]:
    at = fact.source.published_at
    return (at is not None, at, fact.id)


@dataclass(frozen=True)
class FactGroup:
    """One fact over its sources (see the module docstring); `facts` is newest source first."""

    competitor_id: int
    kind: FactKindName
    label_norm: str
    month: date | None  # the as-of month's first day (a named round: its earliest); None undated
    facts: tuple[LoadedFact, ...]
    named: bool = False  # a named funding round: one group across months

    @property
    def head(self) -> LoadedFact:
        """The newest source stating a value, else the newest source."""
        return next((f for f in self.facts if f.value is not None), self.facts[0])

    @property
    def value(self) -> float | None:
        return self.head.value

    @property
    def as_of(self) -> date | None:
        """The head's date; a named round's is its earliest dated source's."""
        if self.named:
            return min((f.as_of for f in self.facts if f.as_of), default=None)
        return self.head.as_of

    @property
    def sources_disagree(self) -> bool:
        return len({f.figure for f in self.facts if f.value is not None}) > 1

    def out(self) -> FactOut:
        head = self.head
        return FactOut(
            kind=self.kind,
            label=head.label,
            value=head.value,
            unit=head.unit,
            currency=head.currency,
            as_of=self.as_of,
            sources_disagree=self.sources_disagree,
            sources=[f.source for f in self.facts],
        )

    def points(self) -> list[FactPointOut]:
        """One point per distinct value (a dated group only), oldest first."""
        if self.month is None:
            return []
        by_figure: dict[tuple, list[LoadedFact]] = {}
        for f in self.facts:
            by_figure.setdefault(f.figure, []).append(f)
        disagree = self.sources_disagree
        points = []
        for facts in by_figure.values():
            newest = facts[0]
            points.append(
                FactPointOut(
                    kind=self.kind,
                    label=newest.label,
                    as_of=newest.as_of or self.month,
                    value=newest.value,
                    unit=newest.unit,
                    currency=newest.currency,
                    sources_disagree=disagree,
                    sources=[f.source for f in facts],
                )
            )
        return sorted(points, key=lambda p: (p.as_of, p.value is None, p.value or 0.0))

    def funding_round(self) -> FundingRoundOut:
        """A dated round (an undated one has no place on a time axis)."""
        head = self.head
        as_of = self.as_of or self.month
        if as_of is None:
            raise ValueError("an undated funding round is no FundingRoundOut")
        return FundingRoundOut(
            as_of=as_of,
            amount=head.value,
            currency=head.currency,
            label=head.label,
            sources_disagree=self.sources_disagree,
            sources=[f.source for f in self.facts],
        )


_ANY_MONTH = date.min  # the month key of a named round: split by `_rounds` afterwards


def _rounds(rows: list[LoadedFact]) -> list[list[LoadedFact]]:
    """One named label's sources as rounds: dated sources, oldest first, join the
    round of the earliest within ROUND_WINDOW_DAYS of them; undated ones join the round when
    there is only one, else stay apart as one undated group."""
    rounds: list[list[LoadedFact]] = []
    for f in sorted((f for f in rows if f.as_of), key=lambda f: (f.as_of, f.id)):
        first = rounds[-1][0].as_of if rounds else None
        if (
            first is not None
            and f.as_of is not None
            and (f.as_of - first).days <= ROUND_WINDOW_DAYS
        ):
            rounds[-1].append(f)
        else:
            rounds.append([f])
    undated = [f for f in rows if f.as_of is None]
    if len(rounds) <= 1:
        return [(rounds[0] if rounds else []) + undated]
    return rounds + ([undated] if undated else [])


def group_facts(facts: Iterable[LoadedFact]) -> list[FactGroup]:
    """Facts grouped by (competitor, kind, label_norm, as-of month), a named round across
    months within ROUND_WINDOW_DAYS (see the module docstring), in first-stored order."""
    groups: dict[tuple[int, FactKindName, str, date | None], list[LoadedFact]] = {}
    for f in facts:
        month: date | None
        if named_round(f.kind, f.label_norm):
            month = _ANY_MONTH
        else:
            month = f.as_of.replace(day=1) if f.as_of else None
        groups.setdefault((f.competitor_id, f.kind, f.label_norm, month), []).append(f)
    out = []
    for (cid, kind, norm, month), rows in groups.items():
        if month != _ANY_MONTH:
            ordered = tuple(sorted(rows, key=_newest_first, reverse=True))
            out.append(FactGroup(cid, kind, norm, month, ordered))
            continue
        for found in _rounds(rows):
            ordered = tuple(sorted(found, key=_newest_first, reverse=True))
            first = min((f.as_of for f in found if f.as_of), default=None)
            start = first.replace(day=1) if first else None
            out.append(FactGroup(cid, kind, norm, start, ordered, named=True))
    return out


def _newest_source(g: FactGroup) -> tuple[bool, datetime | None]:
    at = g.facts[0].source.published_at
    return (at is not None, at)


def dated(groups: Iterable[FactGroup], *kinds: str) -> list[FactGroup]:
    """The dated groups of these kinds, newest first (ties: label)."""
    rows = [g for g in groups if g.kind in kinds and g.month is not None]
    rows.sort(key=lambda g: g.head.label.casefold())
    return sorted(rows, key=lambda g: (g.month, g.as_of), reverse=True)


def undated(groups: Iterable[FactGroup], *kinds: str) -> list[FactGroup]:
    """The undated groups of these kinds, newest source first (ties: label)."""
    rows = [g for g in groups if g.kind in kinds and g.month is None]
    rows.sort(key=lambda g: g.head.label.casefold())
    return sorted(rows, key=_newest_source, reverse=True)


def points(groups: Iterable[FactGroup], *kinds: str) -> list[FactPointOut]:
    """Every reported figure of these kinds on a time axis, oldest first."""
    out = [p for g in groups if g.kind in kinds for p in g.points()]
    return sorted(out, key=lambda p: (p.as_of, p.kind, p.value is None, p.value or 0.0))


def headcount_points(groups: Iterable[FactGroup]) -> list[HeadcountPointOut]:
    return [
        HeadcountPointOut(**p.model_dump(), layoff=p.kind == FactKind.layoff)
        for p in points(groups, FactKind.headcount, FactKind.layoff)
    ]


def latest(groups: Iterable[FactGroup], kind: str) -> FactGroup | None:
    """The newest-dated group of a kind (undated ones are never "latest")."""
    found = dated(groups, kind)
    return found[0] if found else None


def funding_total(groups: Iterable[FactGroup]) -> FundingTotalOut | None:
    """The sum of the funding rounds stating an amount (see the module docstring); None when
    no round states one."""
    rounds = [g for g in groups if g.kind == FactKind.funding_round]
    dated_labels = {g.label_norm for g in rounds if g.month is not None}
    counted = [g for g in rounds if g.month is not None or g.label_norm not in dated_labels]
    by_currency: dict[str, list[FactGroup]] = {}
    for g in counted:
        if g.value is not None:
            by_currency.setdefault(g.head.currency or "", []).append(g)
    if not by_currency:
        return None

    def total(found: list[FactGroup]) -> float:
        return sum(g.value or 0.0 for g in found)

    currency, summed = min(by_currency.items(), key=lambda kv: (-len(kv[1]), -total(kv[1]), kv[0]))
    return FundingTotalOut(
        amount=total(summed),
        currency=currency or None,
        rounds=len(summed),
        undated_rounds=sum(g.month is None for g in summed),
        excluded_rounds=len(counted) - len(summed),
        # how current the total is, the newest dated round in it
        as_of=max((g.as_of for g in summed if g.month is not None and g.as_of), default=None),
    )


# ---- brief sources ------------------------------------------------------------------------

REF_KINDS = ("signal", "fact", "posting", "page")


def parse_ref(ref: str) -> tuple[str, int] | None:
    kind, _, raw = ref.partition(":")
    if kind in REF_KINDS and raw.isdecimal() and len(raw) < 10:
        return kind, int(raw)
    return None


def cited_refs(*sections: Iterable[CitedText]) -> list[str]:
    """Every ref the sections cite, once, in first-cited order."""
    return list(
        dict.fromkeys(ref for section in sections for item in section for ref in item.cites)
    )


def cited_out(items: Iterable[CitedText], numbers: Mapping[str, int]) -> list[CitedTextOut]:
    return [cited_one(item, numbers) for item in items]


def cited_one(item: CitedText, numbers: Mapping[str, int]) -> CitedTextOut:
    return CitedTextOut(
        text=item.text, cites=list(dict.fromkeys(numbers[c] for c in item.cites if c in numbers))
    )


async def brief_sources(
    session: AsyncSession,
    user: User,
    refs: Sequence[str],
    *,
    competitor_id: int | None,
    facts: Mapping[int, LoadedFact],
) -> tuple[list[IntelSourceOut], dict[str, int]]:
    """The numbered sources of `refs` (first-cited order) and the ref -> number map. Facts,
    postings and pages resolve only for `competitor_id` (a product brief cites signals
    only); at most one statement."""
    wanted: dict[str, list[int]] = {k: [] for k in REF_KINDS}
    for ref in refs:
        parsed = parse_ref(ref)
        if parsed is not None:
            wanted[parsed[0]].append(parsed[1])
    found: dict[str, IntelSourceOut] = {}
    for fid in wanted["fact"]:
        f = facts.get(fid)
        if f is not None and competitor_id is not None and f.competitor_id == competitor_id:
            found[f"fact:{fid}"] = _source(
                "fact", fid, f.title, f.source.url, f.source.outlet, f.as_of, f.quote
            )
    for kind, n, title, url, day, excerpt, visible in await _lookup(
        session, user, wanted, competitor_id
    ):
        ref = f"{kind}:{n}"
        if kind == "signal":
            shown = bool(visible)
            found[ref] = _source(
                "signal",
                n,
                title if shown else UNAVAILABLE_TITLE,
                f"/signals/{n}" if shown else None,
                None,
                day if shown else None,
                None,
            )
        elif kind == "posting":
            found[ref] = _source("posting", n, title, url, POSTING_OUTLET, day, None)
        else:  # a page change; `title` carries its page kind
            label = f"{(title or 'watched').capitalize()} page change"
            outlet = host_of(url) if url else None
            found[ref] = _source("page", n, label, url, outlet, day, excerpt)
    sources: list[IntelSourceOut] = []
    numbers: dict[str, int] = {}
    for ref in dict.fromkeys(refs):
        if (src := found.get(ref)) is not None:
            numbers[ref] = len(sources) + 1
            sources.append(src.model_copy(update={"n": numbers[ref]}))
    return sources, numbers


def _source(
    kind: IntelSourceKind,
    n: int,
    title: str | None,
    url: str | None,
    outlet: str | None,
    day: date | None,
    excerpt: str | None,
) -> IntelSourceOut:
    return IntelSourceOut(
        n=0,
        kind=kind,
        ref=f"{kind}:{n}",
        title=title or display_title(None, url or ""),
        url=url,
        outlet=outlet,
        day=day,
        excerpt=excerpt,
    )


async def _lookup(
    session: AsyncSession, user: User, wanted: Mapping[str, list[int]], competitor_id: int | None
) -> list[tuple]:
    """(kind, id, title, url, day, excerpt, visible) for the cited signals, postings and page
    changes, in ONE statement (none when nothing is cited)."""
    tz = get_settings().timezone

    def kind_of(name: str):
        return cast(literal(name), Text).label("kind")

    no_text = cast(null(), Text)
    parts = []
    if wanted["signal"]:
        parts.append(
            select(
                kind_of("signal"),
                Signal.id.label("id"),
                Signal.title.label("title"),
                no_text.label("url"),
                signal_day(tz).label("day"),
                no_text.label("excerpt"),
                and_(public_signal_clause(), visible_signal_clause(user)).label("visible"),
            ).where(Signal.id.in_(wanted["signal"]))
        )
    if wanted["posting"] and competitor_id is not None:
        parts.append(
            select(
                kind_of("posting"),
                JobPosting.id,
                JobPosting.title,
                JobPosting.url,
                func.date(func.timezone(tz, JobPosting.first_seen_at), type_=Date),
                no_text,
                true(),
            ).where(JobPosting.id.in_(wanted["posting"]), JobPosting.competitor_id == competitor_id)
        )
    if wanted["page"] and competitor_id is not None:
        parts.append(
            select(
                kind_of("page"),
                PageSnapshot.id,
                PageSnapshot.page_kind,
                func.coalesce(Source.config["url"].astext, Source.url),
                func.date(func.timezone(tz, PageSnapshot.fetched_at), type_=Date),
                PageSnapshot.change_summary,
                true(),
            )
            .join(Source, Source.id == PageSnapshot.source_id)
            .where(PageSnapshot.id.in_(wanted["page"]), Source.competitor_id == competitor_id)
        )
    if not parts:
        return []
    stmt = parts[0] if len(parts) == 1 else union_all(*parts)
    return [tuple(row) for row in (await session.execute(stmt)).all()]
