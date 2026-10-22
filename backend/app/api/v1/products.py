"""The JFrog product deep dive: the 13 JFROG_PRODUCTS with their pressure and top
competitors, and one product's profile.

Slugs are the kebab-case of the names ("JFrog ML" is "jfrog-ml", "AI Catalog" is
"ai-catalog"); any other slug is a 404. The description is the product's line in the
JFrog context pack's "Product portfolio" section, read from `jfrog_brief_context()`, which
has no company figures. Pressure, competitors, the timeline and the product's
signal set are the public aggregates of `app.intel.products`; its signal cards are
those public signals the reader may also see, so the page is identical for every persona, as
the competitor deep dive. The hiring in a product's
area counts the postings tagged with the capabilities mapped to its KB page
(`kb_pages_for`), across the competitors whose enabled job board has been listed in full
(their current boards only, `app.intel.hiring`), JFrog never. Boards are read
from different days, so each competitor's line starts at its own board's first snapshot (its
first complete listing) and the total at the earliest: a board read later never
reads as hiring growth over the days before it was read. A stale board (not listed in full
for `board_stale_days`) is listed but not counted, and the series end on the last day
every board counted was listed in full, so none is carried flat.

Query budget: the list is 4 statements; a profile at most 12, as its test bounds it (both
read the collected-since day).
"""

import re
from datetime import datetime
from functools import cache
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.agents.nodes.signal_analysis import CAPABILITY_KB_PAGES, kb_pages_for
from app.api import intel_out
from app.api.deps import get_current_user
from app.api.intel_sources import brief_sources, cited_out, cited_refs
from app.api.v1.signals import signal_cards
from app.api.visibility import visible_signal_clause
from app.core.config import get_settings
from app.core.days import local_day
from app.core.db import get_session
from app.intel.brief_inputs import jfrog_brief_context
from app.intel.briefs import latest_product_brief, product_brief_content
from app.intel.hiring import (
    first_listed,
    fresh_board,
    last_listing,
    listed_board,
    open_roles_breakdowns,
)
from app.intel.products import ProductIntel, products_intel
from app.models import Competitor, ProductBrief, Signal, Source, User
from app.rag.kb import load_kb_urls
from app.schemas.intel import (
    AreaBoardOut,
    BriefActionsOut,
    DayCountOut,
    DayKeyCountOut,
    HiringAreaOut,
    ProductBriefOut,
    ProductCompetitorOut,
    ProductProfile,
    ProductSummary,
    ProductTimelineEventOut,
    WeekPressureOut,
)

router = APIRouter(prefix="/products", tags=["products"])

TOP_COMPETITORS = 3
SIGNAL_CARDS = 30
HIRING_DAYS = 90
_PORTFOLIO = re.compile(r"^## Product portfolio\n(?P<body>.*?)(?=^## |\Z)", re.M | re.S)
_ITEM = re.compile(r"^- (?P<name>[^:\n]+): (?P<text>.+)$")


def product_slug(name: str) -> str:
    """Kebab-case of a JFROG_PRODUCTS name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


PRODUCTS_BY_SLUG = {product_slug(p): p for p in JFROG_PRODUCTS}


@cache
def product_descriptions() -> dict[str, str]:
    """Each product's line in the context pack's portfolio, never its company figures."""
    found = _PORTFOLIO.search(jfrog_brief_context())
    out: dict[str, str] = {}
    for line in (found.group("body") if found else "").splitlines():
        item = _ITEM.match(line.strip())
        if item and item.group("name") in JFROG_PRODUCTS:
            text = item.group("text").strip()
            out[item.group("name")] = text[:1].upper() + text[1:]
    return out


@cache
def product_capabilities(product: str) -> tuple[str, ...]:
    """The capability slugs whose KB pages include the product's own page (the page whose
    URL ends in the product's slug), in capability order."""
    slug = product_slug(product)
    pages = {u for u in load_kb_urls() if urlsplit(u).path.rstrip("/").rsplit("/", 1)[-1] == slug}
    return tuple(cap for cap in CAPABILITY_KB_PAGES if pages & set(kb_pages_for([cap])))


def _product(slug: str) -> str:
    name = PRODUCTS_BY_SLUG.get(slug)
    if name is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return name


@router.get("", response_model=list[ProductSummary])
async def list_products(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProductSummary]:
    """The 13 JFrog products in JFROG_PRODUCTS order, from one signal scan."""
    views = await products_intel(session, JFROG_PRODUCTS, now=intel_out.utcnow())
    return [
        ProductSummary(
            slug=product_slug(name),
            name=name,
            description=product_descriptions().get(name),
            pressure=view.trend,
            pressure_baseline=view.baseline,
            top_competitors=[c.competitor_slug for c in view.competitors[:TOP_COMPETITORS]],
        )
        for name, view in views.items()
    ]


@router.get("/{slug}", response_model=ProductProfile)
async def product_profile(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProductProfile:
    name = _product(slug)
    now = intel_out.utcnow()
    brief_row = await latest_product_brief(session, name)
    view = (await products_intel(session, [name], now=now))[name]
    brief = await _brief(session, user, brief_row)
    ids = view.signal_ids[:SIGNAL_CARDS]  # newest first; the cards keep that order
    visible: dict[int, Signal] = {}
    if ids:
        query = select(Signal).where(Signal.id.in_(ids), visible_signal_clause(user))
        visible = {s.id: s for s in await session.scalars(query)}
    cards = await signal_cards(session, [visible[i] for i in ids if i in visible])
    return ProductProfile(
        slug=slug,
        name=name,
        description=product_descriptions().get(name),
        pressure=view.trend,
        pressure_baseline=view.baseline,
        pressure_series=_pressure(view),
        collected_since=view.collected_since,
        competitors=[
            ProductCompetitorOut(
                competitor_slug=c.competitor_slug,
                their_products=c.their_products,
                threat_level=c.threat_level,
                evidence=c.evidence,
                last_move=intel_out.event_out(c.last_move) if c.last_move else None,
            )
            for c in view.competitors
        ],
        brief=brief,
        hiring_in_area=await _hiring_area(session, name, now),
        timeline=[
            ProductTimelineEventOut(
                **intel_out.event_out(e.event).model_dump(), competitor_slug=e.competitor_slug
            )
            for e in view.events
        ],
        signals=cards,
    )


def _pressure(view: ProductIntel) -> list[WeekPressureOut]:
    """The collected weeks of 13, or none at all when no signal names the product
    (never 13 zero weeks)."""
    if not any(w.threat or w.opportunity for w in view.pressure):
        return []
    return [
        WeekPressureOut(week_start=w.week_start, threat=w.threat, opportunity=w.opportunity)
        for w in view.pressure
    ]


async def _brief(
    session: AsyncSession, user: User, row: ProductBrief | None
) -> ProductBriefOut | None:
    content = product_brief_content(row)
    if row is None or content is None:
        return None
    refs = cited_refs(content.bottom_line, content.gaps_parity)
    sources, numbers = await brief_sources(session, user, refs, competitor_id=None, facts={})
    a = content.actions
    return ProductBriefOut(
        version=row.version,
        created_at=row.created_at,
        bottom_line=cited_out(content.bottom_line, numbers),
        gaps_parity=cited_out(content.gaps_parity, numbers),
        actions=BriefActionsOut(sales=a.sales, pm=a.pm, exec=a.exec, pmm_devrel=a.pmm_devrel),
        evidence=content.evidence,
        sources=sources,
    )


async def _hiring_area(session: AsyncSession, product: str, now: datetime) -> HiringAreaOut:
    """Open postings tagged with the product's capabilities, per day over 90 days, across the
    competitors whose enabled job board has been listed in full, and lately
    (a stale board is listed, not counted), each competitor from its board's first
    snapshot (its first complete listing; current boards only), the total from the
    earliest, all to the last day every counted board was listed in full (two
    statements). Every competitor is served: the chart folds."""
    caps = list(product_capabilities(product))
    tz = get_settings().timezone
    fresh = fresh_board(now, tz)
    rows = (
        await session.execute(
            select(
                Competitor.id,
                Competitor.slug,
                first_listed(),
                func.max(last_listing()),
                func.min(last_listing()).filter(fresh),
            )
            .join(Source, Source.competitor_id == Competitor.id)
            .where(Competitor.is_self.is_(False), listed_board())
            .group_by(Competitor.id, Competitor.slug)
        )
    ).all()
    counted = [(cid, slug, first, as_of) for cid, slug, first, _, as_of in rows if as_of]
    starts = {slug: local_day(first, tz) for _, slug, first, _ in counted}
    series = (
        await open_roles_breakdowns(
            session,
            [cid for cid, *_ in counted],
            days=HIRING_DAYS,
            by=("total", "competitor"),
            tags=caps,
            now=now,
            since=min(starts.values()),
            competitor_since=starts,
            fold=False,
            until=min(local_day(as_of, tz) for *_, as_of in counted),
        )
        if counted and caps
        else {}
    )
    boards = sorted(
        (
            AreaBoardOut(
                competitor_slug=slug,
                board_since=local_day(first, tz),
                last_listed=local_day(last, tz),
                stale=as_of is None,
            )
            for _, slug, first, last, as_of in rows
        ),
        key=lambda b: (b.board_since, b.competitor_slug),
    )
    return HiringAreaOut(
        capabilities=caps,
        boards=boards,
        total=[DayCountOut(day=p.day, count=p.count) for p in series.get("total", [])],
        by_competitor=[
            DayKeyCountOut(day=p.day, key=p.key, count=p.count)
            for p in series.get("competitor", [])
        ],
    )
