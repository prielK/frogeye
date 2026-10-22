"""The comparison matrix: JFrog's 13 products against every competitor with seeded
products, in one read.

Nothing is computed here that another page does not already show: the rows' pressure and
each cell's threat come from `products_intel` (the product pages' scan: a cell is the product
deep dive's Competitors row for that competitor), the columns' threat from the competitor
overview (`build_overview`), and the competing products from the seed. The same for every
persona, as the pages it gathers.

Query budget: 11 statements (the overview's 7, the products' one scan 3 and the seed 1).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.api import intel_out
from app.api.competitor_profile import build_overview
from app.api.deps import get_current_user
from app.api.v1.products import product_slug
from app.core.days import local_day
from app.core.db import get_session
from app.intel.products import products_intel
from app.intel.profile import LOOKBACK_DAYS
from app.models import Competitor, CompetitorProduct, User
from app.schemas.compare import (
    CompareCellOut,
    CompareCompetitorOut,
    CompareEvidenceOut,
    CompareOut,
    CompareProductOut,
    CompareTheirProductOut,
)

router = APIRouter(prefix="/compare", tags=["compare"])


@router.get("", response_model=CompareOut)
async def compare(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CompareOut:
    """Rows in JFROG_PRODUCTS order; columns the competitors with a seeded product, by tier,
    then threat, then name; a cell for each pair with a seeded product or evidence."""
    now = intel_out.utcnow()
    overview = await build_overview(session, now)  # by threat, then tier and name
    views = await products_intel(session, JFROG_PRODUCTS, now=now)
    seeded: dict[tuple[str, str], CompareTheirProductOut] = {}
    for slug, name, category, url in await session.execute(
        select(
            Competitor.slug,
            CompetitorProduct.name,
            CompetitorProduct.category,
            CompetitorProduct.url,
        )
        .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
        .where(Competitor.is_self.is_(False))
        .order_by(CompetitorProduct.id)
    ):
        seeded[slug, name] = CompareTheirProductOut(name=name, category=category, url=url)
    with_products = {slug for slug, _ in seeded}
    columns = sorted((c for c in overview if c.slug in with_products), key=lambda c: c.tier)
    order = {c.slug: i for i, c in enumerate(columns)}

    cells: list[CompareCellOut] = []
    for name, view in views.items():
        row = [c for c in view.competitors if c.competitor_slug in order]
        for c in sorted(row, key=lambda c: order[c.competitor_slug]):
            cells.append(
                CompareCellOut(
                    product_slug=product_slug(name),
                    competitor_slug=c.competitor_slug,
                    their_products=[seeded[c.competitor_slug, p] for p in c.their_products],
                    threat_level=c.threat_level,
                    threat_weight=c.threat_weight,
                    evidence=c.evidence,
                    last_move=intel_out.event_out(c.last_move) if c.last_move else None,
                    threat_signals=[
                        CompareEvidenceOut(
                            **intel_out.event_out(t.event).model_dump(),
                            weight=t.weight,
                            severity=t.severity,
                        )
                        for t in c.threat_signals
                    ],
                )
            )
    return CompareOut(
        as_of=local_day(now),
        lookback_days=LOOKBACK_DAYS,
        collected_since=next(iter(views.values())).collected_since if views else None,
        products=[
            CompareProductOut(
                slug=product_slug(name),
                name=name,
                pressure=view.trend,
                pressure_baseline=view.baseline,
            )
            for name, view in views.items()
        ],
        competitors=[
            CompareCompetitorOut(
                slug=c.slug,
                name=c.name,
                tier=c.tier,
                relationship=c.relationship,
                threat=c.threat,
                product_categories=c.product_categories,
            )
            for c in columns
        ],
        cells=cells,
    )
