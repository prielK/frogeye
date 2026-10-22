"""Response schema of the comparison matrix (GET /compare): JFrog's products against the
competitors with seeded products, every value from the same aggregates the product and
competitor pages read (`app.intel.products`, `app.intel.profile`).

Every field is always sent, as in `app.schemas.intel`. Days are local days in
`settings.timezone`; the threat window is the overlap's 180 days ending `as_of`.
"""

import datetime as dt

from pydantic import BaseModel, Field

from app.schemas.intel import (
    Lane,
    OverlapLevel,
    PressureTrend,
    ThreatOut,
    TimelineEventOut,
)


class CompareProductOut(BaseModel):
    """A row: one JFrog product, in JFROG_PRODUCTS order, with its pressure as GET /products
    serves it."""

    slug: str = Field(description="The product page's slug (/products/{slug}).")
    name: str
    pressure: PressureTrend = Field(description="As ProductSummary.pressure.")
    pressure_baseline: bool = Field(description="As ProductSummary.pressure_baseline.")


class CompareCompetitorOut(BaseModel):
    """A column: a competitor with at least one seeded product, by tier, then threat (high
    first, then score), then name."""

    slug: str
    name: str
    tier: int
    relationship: str
    threat: ThreatOut = Field(description="Its overall threat to JFrog, as the overview's.")
    product_categories: list[str] = Field(
        description="The categories of its seeded products, seed order (the category filter)."
    )


class CompareTheirProductOut(BaseModel):
    name: str
    category: str
    url: str | None = Field(description="The vendor's own public page for it (the seed's).")


class CompareEvidenceOut(BaseModel):
    """A signal whose current analysis names the JFrog product as threatened."""

    signal_id: int
    day: dt.date
    lane: Lane
    importance: int
    title: str
    weight: int = Field(description="What it adds to the cell's threat weight (high 3 ...).")
    severity: str = Field(description="The highest severity it names: high, medium or low.")


class CompareCellOut(BaseModel):
    """One JFrog product against one competitor (a column): its seeded products countering
    the product and the evidence-based threat to it, as the product page's Competitors table
    computes them. A pair is served when a product is seeded for it or its signals name the
    product; any other pair has no cell."""

    product_slug: str
    competitor_slug: str
    their_products: list[CompareTheirProductOut] = Field(
        description="Its seeded products countering the JFrog product (seed order); empty when "
        "only its signals name the product."
    )
    threat_level: OverlapLevel = Field(
        description="The overlap level of threat_weight (high from 6, medium from 3, low from 1)."
    )
    threat_weight: int = Field(
        description="Severity-weighted threat impacts naming the product, 180 days."
    )
    evidence: int = Field(description="Severity-weighted impacts of any effect naming it.")
    last_move: TimelineEventOut | None = Field(
        description="Its newest signal naming the product (any effect) in 180 days."
    )
    threat_signals: list[CompareEvidenceOut] = Field(
        description="The signals threat_weight is summed from, newest first."
    )


class CompareOut(BaseModel):
    as_of: dt.date = Field(description="The local day the matrix is computed for.")
    lookback_days: int = Field(description="The threat window, in days ending as_of.")
    collected_since: dt.date | None = Field(
        description="The local day FrogEye filed its first document; null before any."
    )
    products: list[CompareProductOut]
    competitors: list[CompareCompetitorOut]
    cells: list[CompareCellOut] = Field(
        description="Row by row (products order), then by column (competitors order)."
    )
