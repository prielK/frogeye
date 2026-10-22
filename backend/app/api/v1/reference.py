"""Read-only reference data behind the Signals Explorer facets: the capability
taxonomy by category, and the CI team's key intelligence topics (KITs); and the rules the
competitor and product pages quote."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.api import intel_out
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_session
from app.intel.hiring import DELTA_DAYS
from app.intel.profile import (
    HIRING_FACTOR,
    LOOKBACK_DAYS,
    MOMENTUM_FACTOR,
    MOMENTUM_WEEKS,
    SEVERITY_WEIGHT,
    TREND_CAP,
    TREND_RECENT_WEEKS,
    collected_since,
)
from app.models import Capability, CapabilityCategory, Kit, User
from app.schemas.reference import (
    BriefRulesOut,
    CapabilityCategoryOut,
    CapabilityOut,
    IntelReferenceOut,
    KitOut,
    OverlapRulesOut,
    SeverityWeightsOut,
    ThreatRulesOut,
    TrendRulesOut,
)

router = APIRouter(tags=["reference"])


@router.get("/capabilities", response_model=list[CapabilityCategoryOut])
async def list_capabilities(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CapabilityCategoryOut]:
    """Categories in their seed order, each with its capabilities by name."""
    categories = list(
        await session.scalars(
            select(CapabilityCategory).order_by(CapabilityCategory.order, CapabilityCategory.id)
        )
    )
    grouped: dict[int, list[CapabilityOut]] = {c.id: [] for c in categories}
    # Alphabetical regardless of case or database collation ("Agent governance" < "AI catalog").
    caps = await session.scalars(select(Capability))
    for cap in sorted(caps, key=lambda c: (c.name.casefold(), c.id)):
        grouped.setdefault(cap.category_id, []).append(CapabilityOut(slug=cap.slug, name=cap.name))
    return [
        CapabilityCategoryOut(slug=c.slug, name=c.name, capabilities=grouped[c.id])
        for c in categories
    ]


@router.get("/kits", response_model=list[KitOut])
async def list_kits(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[KitOut]:
    """KITs, highest priority (1) first."""
    rows = await session.scalars(select(Kit).order_by(Kit.priority, Kit.slug))
    return [KitOut(slug=k.slug, title=k.title, question=k.question) for k in rows]


@router.get("/intel-reference", response_model=IntelReferenceOut)
async def intel_reference(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IntelReferenceOut:
    """The pages' rules, from the same constants and settings the aggregates use (one
    statement: the collected-since day)."""
    s = get_settings()
    low, high = MOMENTUM_FACTOR
    return IntelReferenceOut(
        jfrog_products=list(JFROG_PRODUCTS),
        severity_weights=SeverityWeightsOut(**SEVERITY_WEIGHT),
        overlap=OverlapRulesOut(
            high_evidence=s.overlap_high_evidence,
            medium_evidence=s.overlap_medium_evidence,
            lookback_days=LOOKBACK_DAYS,
        ),
        threat=ThreatRulesOut(
            high_score=s.threat_high_score,
            medium_score=s.threat_medium_score,
            min_signals=s.threat_min_signals,
            lookback_days=LOOKBACK_DAYS,
            momentum_factor_min=low,
            momentum_factor_max=high,
            hiring_factor_bound=HIRING_FACTOR,
            hiring_history_days=DELTA_DAYS,
        ),
        trend=TrendRulesOut(
            weeks=MOMENTUM_WEEKS,
            recent_weeks=TREND_RECENT_WEEKS,
            baseline_weeks=MOMENTUM_WEEKS - TREND_RECENT_WEEKS,
            cap=TREND_CAP,
            rising_ratio=s.pressure_rising_ratio,
            easing_ratio=s.pressure_easing_ratio,
        ),
        briefs=BriefRulesOut(min_signals=s.brief_min_signals, lookback_days=s.brief_lookback_days),
        collected_since=await collected_since(session, now=intel_out.utcnow()),
    )
