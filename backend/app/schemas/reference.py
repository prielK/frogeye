import datetime as dt

from pydantic import BaseModel, Field


class CapabilityOut(BaseModel):
    slug: str
    name: str


class CapabilityCategoryOut(BaseModel):
    slug: str
    name: str
    capabilities: list[CapabilityOut]


class KitOut(BaseModel):
    """A key intelligence topic (KIT): `title` is its short name, `question` the full ask."""

    slug: str
    title: str
    question: str


# ---- the intelligence pages' rules -------------------------


class SeverityWeightsOut(BaseModel):
    """The weight of a jfrog_impact entry by its severity (overlap, threat and pressure)."""

    high: int
    medium: int
    low: int


class OverlapRulesOut(BaseModel):
    """A seeded product's overlap level from its evidence (weighted jfrog_impact entries)."""

    high_evidence: int = Field(description="Evidence from which the level is high.")
    medium_evidence: int = Field(description="Evidence from which it is medium (low from 1).")
    lookback_days: int = Field(description="Days of public signals the evidence counts.")


class ThreatRulesOut(BaseModel):
    """score = overlap x clamp(momentum_trend, factor min, max) x (1 + clamp(hiring_trend,
    -bound, +bound)); each factor is 1.0 when its trend is null."""

    high_score: float = Field(description="Score from which the threat is high.")
    medium_score: float = Field(description="Score from which it is medium (else low).")
    min_signals: int = Field(
        description="Fewer public signals than this in lookback_days and no job board listed "
        "in full: insufficient data."
    )
    lookback_days: int
    momentum_factor_min: float
    momentum_factor_max: float
    hiring_factor_bound: float = Field(description="The hiring factor stays within 1 ± this.")
    hiring_history_days: int = Field(
        description="Days of board history a 30-day change needs (board_since)."
    )


class TrendRulesOut(BaseModel):
    """A weekly trend (a competitor's momentum_trend, a product's pressure): the mean of the
    last `recent_weeks` over the mean of the `baseline_weeks` before, at most `cap`; rising
    from `rising_ratio`, easing at or under `easing_ratio`, else steady. It needs a full
    baseline after `collected_since`, else there is no baseline yet."""

    weeks: int = Field(description="Weeks a weekly series spans (whole 7-day windows).")
    recent_weeks: int
    baseline_weeks: int
    cap: float
    rising_ratio: float
    easing_ratio: float


class BriefRulesOut(BaseModel):
    min_signals: int = Field(
        description="Public signals in lookback_days a weekly brief needs (else none yet)."
    )
    lookback_days: int


class IntelReferenceOut(BaseModel):
    """GET /intel-reference: the lists and thresholds the competitor and product pages quote
    ("How this is computed", trend words), served from the backend's own constants and
    settings so the pages keep no copies, plus the day FrogEye began
    collecting."""

    jfrog_products: list[str] = Field(description="JFROG_PRODUCTS, in their order.")
    severity_weights: SeverityWeightsOut
    overlap: OverlapRulesOut
    threat: ThreatRulesOut
    trend: TrendRulesOut
    briefs: BriefRulesOut
    collected_since: dt.date | None = Field(
        description="The local day FrogEye filed its first document: weekly series "
        "start at the week holding it, never drawn as quiet weeks before it; null before "
        "any document."
    )
