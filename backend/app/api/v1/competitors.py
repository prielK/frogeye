"""Competitors: the list, the all-competitors overview, the deep-dive profile, a page change's
whole diff and the paged job postings. The sections, their rules (visibility, honest
empty states, coverage notes) and the query budgets are in app/api/competitor_profile.py.

JFrog itself is never in the overview; its profile and postings are 404.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import exists, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import intel_out
from app.api.competitor_profile import (
    build_overview,
    build_profile,
    competitor_or_404,
    logo_url,
    page_change_diff,
)
from app.api.deps import get_current_user
from app.core.db import get_session
from app.intel.hiring import fresh_board
from app.models import Capability, Competitor, JobPosting, Source, User
from app.schemas.competitors import CompetitorOut
from app.schemas.intel import (
    CompetitorProfile,
    CompetitorSummary,
    Department,
    PageDiffOut,
    PostingPage,
)

router = APIRouter(prefix="/competitors", tags=["competitors"])


@router.get("", response_model=list[CompetitorOut])
async def list_competitors(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CompetitorOut]:
    rows = await session.execute(
        select(Competitor).order_by(Competitor.is_self.desc(), Competitor.tier, Competitor.name)
    )
    return [
        CompetitorOut(
            id=c.id,
            slug=c.slug,
            name=c.name,
            tier=c.tier,
            relationship=c.relationship,
            is_self=c.is_self,
            website=c.website,
            ticker=c.ticker,
            description=c.description,
            logo_url=logo_url(c),
        )
        for c in rows.scalars().all()
    ]


@router.get("/overview", response_model=list[CompetitorSummary])
async def competitors_overview(
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CompetitorSummary]:
    """Every competitor but JFrog itself, by threat (high first, then score), then tier and
    name."""
    return await build_overview(session, intel_out.utcnow())


@router.get("/{slug}/profile", response_model=CompetitorProfile)
async def competitor_profile(
    slug: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CompetitorProfile:
    return await build_profile(session, user, slug, intel_out.utcnow())


@router.get("/{slug}/page-changes/{snapshot_id}/diff", response_model=PageDiffOut)
async def competitor_page_change_diff(
    slug: str,
    snapshot_id: int,
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PageDiffOut:
    """The whole stored diff of a page change in its profile's pricing section (the
    profile serves each one capped); JFrog itself and any other snapshot are a 404."""
    comp = await competitor_or_404(session, slug)
    return PageDiffOut(
        snapshot_id=snapshot_id, diff=await page_change_diff(session, comp, snapshot_id)
    )


@router.get("/{slug}/postings", response_model=PostingPage)
async def competitor_postings(
    slug: str,
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    posting_status: Annotated[
        Literal["open", "closed"] | None,
        Query(alias="status", description="Open (not closed) or closed; both when omitted."),
    ] = None,
    department: Department | None = None,
    tag: Annotated[
        str | None,
        Query(max_length=80, description="A capability slug (GET /capabilities); else 422."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> PostingPage:
    """Its job postings, newest first-seen first: those of its current (enabled) boards
    once one has been listed in full and while it is (a board not
    listed in full for board_stale_days serves none), else none (the profile says why: "No
    public Greenhouse, Lever or Ashby board found", "Job board found — first snapshot
    pending" or "Job board not readable since <day>"); JFrog itself is a 404. Each
    posting says its tagging state (`tag_state`)."""
    comp = await competitor_or_404(session, slug)
    if tag is not None and not await session.scalar(
        select(exists().where(Capability.slug == tag))
    ):  # one taxonomy, as for the department
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown capability tag {tag!r}"
        )
    # only through a current board listed in full, and lately (app.intel.hiring)
    stmt = (
        select(JobPosting)
        .join(Source, (Source.id == JobPosting.source_id) & fresh_board(intel_out.utcnow()))
        .where(JobPosting.competitor_id == comp.id)
    )
    if posting_status == "open":
        stmt = stmt.where(JobPosting.closed_at.is_(None))
    elif posting_status == "closed":
        stmt = stmt.where(JobPosting.closed_at.is_not(None))
    if department is not None:
        stmt = stmt.where(JobPosting.department == department)
    if tag is not None:
        stmt = stmt.where(JobPosting.tags.contains([tag]))
    if cursor:
        at, posting_id = intel_out.decode_posting_cursor(cursor)
        stmt = stmt.where(tuple_(JobPosting.first_seen_at, JobPosting.id) < tuple_(at, posting_id))
    rows = list(
        await session.scalars(
            stmt.order_by(JobPosting.first_seen_at.desc(), JobPosting.id.desc()).limit(limit + 1)
        )
    )
    page, extra = rows[:limit], rows[limit:]
    last = page[-1] if extra else None
    return PostingPage(
        items=[intel_out.posting_out(p) for p in page],
        next_cursor=intel_out.encode_posting_cursor(last.first_seen_at, last.id) if last else None,
    )
