"""The evidence a weekly brief is built from (the briefs themselves are in
app/intel/briefs.py). Only what FrogEye already holds, read in one short session.

Competitor (`competitor_inputs`), each scraped or LLM-derived text inside `untrusted()`, each source
tagged with its ref:
- the current, usable stage-B analyses of the competitor's public signals
  (`public_signal_clause`: status auto|approved, never JFrog scope) whose day
  falls in the last `brief_lookback_days`: title, companies (never JFrog), summary and
  jfrog_impact. The BRIEF_SIGNALS most important, shown newest first: `signal:<id>`;
- its verified company facts (only verified facts are stored), newest first:
  `fact:<id>`;
- its job board: its newest open postings, `posting:<id>`, and the `hiring_summary` counts,
  collected data shown inside `untrusted("job-board", ...)` and kept to FrogEye's own
  vocabulary. The counts are evidence of every posting ref. A stale board (not listed
  in full for `board_stale_days`) is read as no board: no count, no posting. When some
  open postings are not tagged yet, the hiring areas say they are a share;
- the meaningful changes of its watched pages in the lookback: `page:<snapshot id>`;
- its seeded products with their JFrog counterparts and overlap level (`product_overlap`),
  and its seeded profile: FrogEye's own records, orientation only.

The JFrog brief a brief reads is `jfrog_brief_context()`: the context pack without its
company figures.

Product (`product_inputs`): the current analyses of the public signals whose jfrog_impact
names the product (`product_signals`, the set the product views read): companies,
summary, the impact on the product, the PM assessment and the per-role actions, with
`product_competitors` as orientation. Refs are `signal:<id>`.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import SignalAnalysisContent
from app.agents.analysis_store import stored_analyses
from app.agents.runtime.prompts import untrusted
from app.collectors.jobs.common import KNOWN_COUNTRIES
from app.collectors.jobs.taxonomy import DEPARTMENTS, SENIORITIES
from app.core.config import Settings
from app.core.days import local_day
from app.intel.hiring import DELTA_DAYS, HiringSummary, hiring_summary
from app.intel.products import product_competitors, product_signals
from app.intel.profile import product_overlap, signal_day, signal_rows, since
from app.models import (
    Capability,
    CompanyFact,
    Competitor,
    PageSnapshot,
    Signal,
    SignalCompetitor,
    Source,
)
from app.rag.kb import load_jfrog_context

OTHER = "Other"
_DEPARTMENTS = frozenset(DEPARTMENTS)
_SENIORITIES = frozenset(SENIORITIES)
_LOCATIONS = KNOWN_COUNTRIES | {"Remote", "Unknown"}  # hiring_summary's location keys
BRIEF_SIGNALS = 20  # analyses one brief reads, the most important first
BRIEF_FACTS = 15
BRIEF_POSTINGS = 12
BRIEF_PAGES = 8
BRIEF_COMPETITORS = 10  # orientation lines in a product brief
SOURCE_CHARS = 2500  # one source's body: what the model reads and the number guard checks
DIFF_CHARS = 1200  # of a page change's changed lines
TAGS_SHOWN = 10


# JFrog's company figures never enter a brief, its product capabilities do.
COMPANY_FIGURE_SECTIONS = ("Financial snapshot",)
_SECTION = re.compile(r"^## (?P<title>[^\n]*)\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def jfrog_brief_context() -> str:
    """The JFrog brief the brief writers, the number guard and the checker read: the JFrog
    context pack without its company figures (no JFrog company stats). Every section
    titled in COMPANY_FIGURE_SECTIONS (revenue, growth, net dollar retention, $1M
    customers) is cut; the positioning, product portfolio and capabilities stay."""

    def keep(m: re.Match[str]) -> str:
        return "" if m.group("title").startswith(COMPANY_FIGURE_SECTIONS) else m.group(0)

    return _SECTION.sub(keep, load_jfrog_context()).strip()


@dataclass(frozen=True)
class BriefSource:
    """One citable source: a trusted header, the untrusted body the model and the checker
    see (also the number-guard corpus), and the day an item may name."""

    ref: str
    header: str
    body: str
    published: datetime | None = None

    def view(self) -> str:
        return f"{self.header}\n{untrusted(self.ref, self.body, max_chars=len(self.body))}"


JOB_BOARD_REF = "job-board"


def job_board_view(text: str) -> str:
    """The job-board summary inside untrusted(): it is collected data, like the sources."""
    return untrusted(JOB_BOARD_REF, text, max_chars=len(text))


def flat(text: str | None) -> str:
    return " ".join((text or "").split())


def plural(n: int, noun: str) -> str:
    return f"{n} {noun if n == 1 else noun + 's'}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _noon(day: date | None) -> datetime | None:
    return datetime.combine(day, time(12), UTC) if day else None


def _figure(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,}"


def _kind(signal_type: str | None) -> str:
    return (signal_type or "unclassified").replace("_", " ")


# ---- signals ------------------------------------------------------------------------------


async def _current_analyses(
    session: AsyncSession, ids: Sequence[int]
) -> dict[int, SignalAnalysisContent]:
    """Each signal's current, usable analysis; signals without one are absent."""
    if not ids:
        return {}
    analyzed_at = dict(
        (
            await session.execute(
                select(Signal.id, Signal.analyzed_at).where(Signal.id.in_(list(ids)))
            )
        ).all()
    )
    stored = await stored_analyses(session, analyzed_at)
    return {sid: s.content for sid, s in stored.items() if s.current}


async def _companies(session: AsyncSession, ids: Sequence[int]) -> dict[int, list[str]]:
    """The companies each signal is about, JFrog itself never."""
    out: dict[int, list[str]] = {}
    if not ids:
        return out
    rows = await session.execute(
        select(SignalCompetitor.signal_id, Competitor.name)
        .join(Competitor, Competitor.id == SignalCompetitor.competitor_id)
        .where(SignalCompetitor.signal_id.in_(list(ids)), Competitor.is_self.is_(False))
        .order_by(Competitor.tier, Competitor.name)
    )
    for sid, name in rows.all():
        out.setdefault(sid, []).append(name)
    return out


@dataclass(frozen=True)
class _Row:
    id: int
    title: str
    signal_type: str | None
    importance: int
    day: date


def _pick(rows: Iterable[_Row], analysed: dict[int, SignalAnalysisContent]) -> list[_Row]:
    """The BRIEF_SIGNALS most important analysed signals, shown newest first."""
    ranked = sorted(
        (r for r in rows if r.id in analysed),
        key=lambda r: (-r.importance, -r.day.toordinal(), -r.id),
    )[:BRIEF_SIGNALS]
    return sorted(ranked, key=lambda r: (r.day, r.id), reverse=True)


def _signal_source(row: _Row, companies: list[str], lines: list[str]) -> BriefSource:
    ref = f"signal:{row.id}"
    body = "\n".join(
        [f"Title: {flat(row.title)}", f"Companies: {', '.join(companies) or 'none named'}"] + lines
    )
    return BriefSource(
        ref=ref,
        header=f"{ref} (FrogEye signal analysis; {_kind(row.signal_type)}; {row.day})",
        body=_clip(body, SOURCE_CHARS),
        published=_noon(row.day),
    )


def _impact_lines(content: SignalAnalysisContent, product: str | None = None) -> list[str]:
    entries = [e for e in content.jfrog_impact if product is None or e.product == product]
    if not entries:
        return ["Impact on JFrog products: none stated"]
    head = f"Impact on {product}:" if product else "Impact on JFrog products:"
    return [head] + [
        f"- {e.product} ({e.effect}, {e.severity} severity): {flat(e.rationale)}" for e in entries
    ]


def _action_lines(content: SignalAnalysisContent) -> list[str]:
    a = content.recommended_actions
    pairs = [
        ("sales talk track", a.sales.talk_track),
        ("sales discovery question", a.sales.discovery_questions),
        ("sales landmine question", a.sales.landmines),
        ("PM follow-up", a.pm.follow_ups),
        ("exec implication", a.exec.implications),
        ("exec decision", a.exec.decisions),
        ("PMM/DevRel messaging", a.pmm_devrel.messaging),
        ("PMM/DevRel content idea", a.pmm_devrel.content_ideas),
    ]
    lines = [f"- {label}: {flat(t)}" for label, texts in pairs for t in texts if t.strip()]
    lines += [
        f'- sales objection: "{flat(o.objection)}" response: "{flat(o.response)}"'
        for o in a.sales.objections
    ]
    return ["Actions this analysis recommends:", *lines] if lines else []


# ---- competitor ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompetitorInputs:
    slug: str
    profile: str  # trusted: the seeded profile
    public_signals: int  # every public signal in the lookback, analysed or not
    signals: list[BriefSource]
    facts: list[BriefSource]
    postings: list[BriefSource]
    pages: list[BriefSource]
    hiring: str  # the job-board summary: evidence of every posting ref
    has_board: bool  # a board listed in full (measured): its summary is collected data
    products: list[str]  # trusted product-map lines
    product_map: dict[str, tuple[str, ...]]  # seeded product -> its JFrog counterparts

    @property
    def sources(self) -> dict[str, BriefSource]:
        return {s.ref: s for s in (*self.signals, *self.facts, *self.postings, *self.pages)}

    @property
    def posting_note(self) -> str:
        """The job-board summary as evidence of the posting refs ("" without postings)."""
        return self.hiring if self.postings else ""

    def hiring_view(self) -> str:
        """The summary as a model sees it: collected board data, inside untrusted();
        FrogEye's own "no board" line stays plain."""
        return job_board_view(self.hiring) if self.has_board else self.hiring

    def evidence(self) -> dict[str, int]:
        return {
            "signals": len(self.signals),
            "facts": len(self.facts),
            "postings": len(self.postings),
            "pages": len(self.pages),
        }

    def material(self) -> dict[str, Any]:
        """Everything the brief is built from, for the input hash."""
        return {
            "profile": self.profile,
            "products": self.products,
            "hiring": self.hiring,
            "sources": [[s.ref, s.header, s.body] for s in self.sources.values()],
        }


def _profile(comp: Competitor) -> str:
    bits = [f"{comp.name} (tier {comp.tier}, {comp.relationship})"]
    if comp.website:
        bits.append(f"website {comp.website}")
    line = "; ".join(bits) + "."
    return f"{line} {flat(comp.description)}" if comp.description else line


async def _fact_sources(session: AsyncSession, competitor_id: int) -> list[BriefSource]:
    rows = await session.scalars(
        select(CompanyFact)
        .where(CompanyFact.competitor_id == competitor_id, CompanyFact.verified.is_(True))
        .order_by(CompanyFact.as_of.desc().nulls_last(), CompanyFact.id.desc())
        .limit(BRIEF_FACTS)
    )
    out = []
    for f in rows:
        ref = f"fact:{f.id}"
        origin = "a news article" if f.source_kind == "document" else "a watched company page"
        as_of = f.as_of.isoformat() if f.as_of else "undated"
        lines = [f"Kind: {f.kind.replace('_', ' ')}", f"Label: {flat(f.label)}"]
        if f.value_num is not None:
            unit = " ".join(u for u in (f.currency, f.unit) if u)
            lines.append(f"Value: {_figure(f.value_num)}{' ' + unit if unit else ''}")
        lines += [f"As of: {as_of}", f'Quote: "{flat(f.quote)}"']
        out.append(
            BriefSource(
                ref=ref,
                header=f"{ref} (verified company fact from {origin}; as of {as_of})",
                body=_clip("\n".join(lines), SOURCE_CHARS),
                published=_noon(f.as_of),
            )
        )
    return out


async def _capability_names(session: AsyncSession, slugs: Iterable[str]) -> dict[str, str]:
    wanted = sorted(set(slugs))
    if not wanted:
        return {}
    rows = await session.execute(
        select(Capability.slug, Capability.name).where(Capability.slug.in_(wanted))
    )
    return dict(rows.all())


def _known(pairs: Iterable[tuple[str, int]], allowed: frozenset[str]) -> list[tuple[str, int]]:
    """The counts with every key outside `allowed` folded into "Other" (listed last)."""
    counts: dict[str, int] = {}
    for key, n in pairs:
        kept = key if key in allowed else OTHER
        counts[kept] = counts.get(kept, 0) + n
    ranked = sorted(
        ((k, n) for k, n in counts.items() if k != OTHER), key=lambda kv: (-kv[1], kv[0])
    )
    return ranked + ([(OTHER, counts[OTHER])] if OTHER in counts else [])


def _hiring_text(summary: HiringSummary, names: dict[str, str], today: date | None = None) -> str:
    """The job-board summary. It is the evidence of every posting ref, so it
    carries only counts and FrogEye's own vocabulary: the department and seniority taxonomy,
    the countries the collectors name (an ATS's raw country text folds into "Other") and the
    capability names of the tags. It still reaches a model only inside untrusted()."""
    if not summary.has_board:
        return "No public job board found."
    if summary.stale and summary.last_listed_day is not None:  # read as no board
        d = summary.last_listed_day
        return (
            f"The public job board has not been readable since {d.day} {d:%b} {d.year}, so "
            "no current hiring count is known."
        )
    if not summary.measured:  # never a 0 or a partial count read as measured
        return "A public job board was found, but it has not been listed in full yet."
    # a board read for under 30 days has no 30-day change to state, only its count.
    change = (
        f", {summary.delta_30d:+d} against {DELTA_DAYS} days ago"
        if summary.delta_30d is not None
        else f"; the board has under {DELTA_DAYS} days of history, so no change is known yet"
    )
    # the counts are as of the board's last complete listing, today's or an earlier one
    day = summary.as_of_day
    when = "today" if day is None or day == today or today is None else f"on {day.day} {day:%b}"
    lines = [f"{plural(summary.open_now, 'open role')} {when}{change}."]
    for label, pairs, allowed in (
        ("By department", summary.by_department, _DEPARTMENTS),
        ("By seniority", summary.by_seniority, _SENIORITIES),
        ("By location", summary.by_location, _LOCATIONS),
    ):
        if pairs:
            lines.append(f"{label}: " + ", ".join(f"{k} {n}" for k, n in _known(pairs, allowed)))
    tags = [(names[slug], n, d) for slug, n, d in summary.tags if slug in names][:TAGS_SHOWN]
    coverage = summary.coverage
    # tags on part of the board are a share, never read as the whole board
    share = (
        f"; {coverage.tagged + coverage.untagged} of {plural(coverage.total, 'open role')} "
        "read for product areas so far, so a share of the board"
        if coverage.partial
        else ""
    )
    if tags and summary.delta_30d is not None:
        lines.append(
            "Hiring areas (product-area tags on open postings, with the change in "
            f"{DELTA_DAYS} days{share}): "
            + ", ".join(
                f"{name} {n} ({d:+d})" if d is not None else f"{name} {n}" for name, n, d in tags
            )
        )
    elif tags:
        lines.append(
            f"Hiring areas (product-area tags on open postings{share}): "
            + ", ".join(f"{name} {n}" for name, n, _ in tags)
        )
    elif coverage.partial:
        lines.append(
            f"Hiring areas: {coverage.pending + coverage.gave_up} of "
            f"{plural(coverage.total, 'open role')} not read for product areas yet."
        )
    return "\n".join(lines)


def _posting_sources(summary: HiringSummary, names: dict[str, str], tz: str) -> list[BriefSource]:
    if not summary.measured:
        return []
    out = []
    for p in summary.recent[:BRIEF_POSTINGS]:
        ref = f"posting:{p.id}"
        where = p.location or p.country or "location not stated"
        lines = [
            f"Title: {flat(p.title)}",
            f"Department: {p.department}; seniority: {p.seniority}; location: {flat(where)}"
            + ("; remote" if p.remote else ""),
        ]
        if p.tags:
            lines.append("Hiring areas: " + ", ".join(names.get(t, t) for t in p.tags))
        seen = local_day(p.first_seen_at, tz).isoformat()
        out.append(
            BriefSource(
                ref=ref,
                header=f"{ref} (open job posting on the public job board; first seen {seen})",
                body=_clip("\n".join(lines), SOURCE_CHARS),
                published=p.first_seen_at,
            )
        )
    return out


def _changed_lines(diff: str | None) -> str:
    lines = [
        ln
        for ln in (diff or "").splitlines()
        if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
    ]
    return _clip("\n".join(lines), DIFF_CHARS)


async def _page_sources(
    session: AsyncSession, competitor_id: int, *, start: date, end: date, tz: str
) -> list[BriefSource]:
    day = func.date(func.timezone(tz, PageSnapshot.fetched_at), type_=Date)
    rows = await session.execute(
        select(
            PageSnapshot.id,
            PageSnapshot.page_kind,
            PageSnapshot.change_kind,
            PageSnapshot.change_summary,
            PageSnapshot.diff_from_prev,
            PageSnapshot.fetched_at,
            day.label("day"),
            Source.url,
            Source.config,
        )
        .join(Source, Source.id == PageSnapshot.source_id)
        .where(
            Source.competitor_id == competitor_id,
            PageSnapshot.meaningful.is_(True),
            day >= start,
            day <= end,
        )
        .order_by(PageSnapshot.fetched_at.desc(), PageSnapshot.id.desc())
        .limit(BRIEF_PAGES)
    )
    out = []
    for sid, page_kind, change_kind, summary, diff, fetched, seen, url, config in rows.all():
        ref = f"page:{sid}"
        page_url = str((config or {}).get("url") or url)
        lines = [f"Summary: {flat(summary) or 'none'}"]
        changed = _changed_lines(diff)
        if changed:
            lines.append(f'Changed lines ("-" removed, "+" added):\n{changed}')
        out.append(
            BriefSource(
                ref=ref,
                header=f"{ref} (meaningful change to its {page_kind or 'watched'} page "
                f"{page_url}, seen {seen}; kind: {change_kind or 'unstated'})",
                body=_clip("\n".join(lines), SOURCE_CHARS),
                published=fetched,
            )
        )
    return out


async def competitor_inputs(
    session: AsyncSession, comp: Competitor, settings: Settings, now: datetime
) -> CompetitorInputs:
    """A competitor's evidence (see the module docstring); `comp` is never the self one."""
    tz = settings.timezone
    today = local_day(now, tz)
    start = since(today, settings.brief_lookback_days)
    public = await signal_rows(session, start=start, end=today, competitor_ids=[comp.id])
    rows = [_Row(r.id, r.title, r.signal_type, r.importance, r.day) for r in public]
    analysed = await _current_analyses(session, [r.id for r in rows])
    picked = _pick(rows, analysed)
    companies = await _companies(session, [r.id for r in picked])
    signals = [
        _signal_source(
            r,
            companies.get(r.id, []),
            [f"Summary: {flat(analysed[r.id].summary.text)}", *_impact_lines(analysed[r.id])],
        )
        for r in picked
    ]
    shown = {s.ref for s in signals}

    overlap = await product_overlap(session, comp.id, days=settings.brief_lookback_days, now=now)
    products = []
    for p in overlap:
        refs = [f"signal:{sid}" for sid in p.signal_ids if f"signal:{sid}" in shown]
        level = "no recent evidence" if p.level == "none" else p.level
        products.append(
            f"- {p.product} ({p.category}) vs JFrog {', '.join(p.jfrog_products)}: overlap "
            f"{level}" + (f" ({', '.join(refs)})" if refs else "")
        )

    summary = await hiring_summary(session, comp.id, now=now)
    names = await _capability_names(
        session,
        [slug for slug, _, _ in summary.tags] + [t for p in summary.recent for t in p.tags],
    )
    return CompetitorInputs(
        slug=comp.slug,
        profile=_profile(comp),
        public_signals=len({r.id for r in rows}),
        signals=signals,
        facts=await _fact_sources(session, comp.id),
        postings=_posting_sources(summary, names, tz),
        pages=await _page_sources(session, comp.id, start=start, end=today, tz=tz),
        hiring=_hiring_text(summary, names, today),
        has_board=summary.measured,
        products=products,
        product_map={p.product: tuple(p.jfrog_products) for p in overlap},
    )


# ---- product ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductInputs:
    product: str
    public_signals: int  # public signals whose current analysis names the product
    signals: list[BriefSource]
    competitors: list[str]  # trusted orientation lines

    @property
    def sources(self) -> dict[str, BriefSource]:
        return {s.ref: s for s in self.signals}

    def evidence(self) -> dict[str, int]:
        return {"signals": len(self.signals)}

    def material(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "competitors": self.competitors,
            "sources": [[s.ref, s.header, s.body] for s in self.signals],
        }


async def product_inputs(
    session: AsyncSession, product: str, settings: Settings, now: datetime
) -> ProductInputs:
    """A JFrog product's evidence (see the module docstring); `product` is in JFROG_PRODUCTS."""
    days = settings.brief_lookback_days
    ids = await product_signals(session, product, days=days, now=now)
    rows = [
        _Row(sid, title, kind, importance, day)
        for sid, title, kind, importance, day in (
            await session.execute(
                select(
                    Signal.id,
                    Signal.title,
                    Signal.signal_type,
                    Signal.importance,
                    signal_day(settings.timezone),
                ).where(Signal.id.in_(ids))
            )
        ).all()
    ]
    analysed = await _current_analyses(session, ids)
    picked = _pick(rows, analysed)
    companies = await _companies(session, [r.id for r in picked])
    signals = []
    for r in picked:
        content = analysed[r.id]
        lines = [f"Summary: {flat(content.summary.text)}", *_impact_lines(content, product)]
        assessment = [a for a in content.recommended_actions.pm.assessment if a.strip()]
        if assessment:
            lines += ["Product-manager assessment:", *(f"- {flat(a)}" for a in assessment)]
        lines += _action_lines(content)
        signals.append(_signal_source(r, companies.get(r.id, []), lines))
    shown = {s.ref for s in signals}

    rivals = (await product_competitors(session, product, days=days, now=now))[:BRIEF_COMPETITORS]
    names = dict(
        (
            await session.execute(
                select(Competitor.slug, Competitor.name).where(
                    Competitor.slug.in_([c.competitor_slug for c in rivals])
                )
            )
        ).all()
    )
    competitors = []
    for c in rivals:
        theirs = ", ".join(c.their_products) or "no mapped product"
        level = "no recent threat evidence" if c.threat_level == "none" else c.threat_level
        line = f"- {names.get(c.competitor_slug, c.competitor_slug)}: {theirs}; threat {level}"
        move = c.last_move
        if move is not None and f"signal:{move.signal_id}" in shown:
            line += f"; latest move signal:{move.signal_id} on {move.day}"
        competitors.append(line)
    return ProductInputs(product, len(ids), signals, competitors)
