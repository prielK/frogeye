"""Morning briefings. One row per (date, edition, version); the API serves one row per date.

- Edition: each reader is served only their own edition, `default_edition(user)` (analysts
  read the master); there is no parameter to ask for another. The reader's edition row
  when the date has one, else that date's master row -- a skipped_budget/failed day files a
  master row only and a role adapter error drops one edition. `edition` in the
  response is the row served; `requested_edition` is the reader's own.
- Version, within the served edition: the latest published|quiet one when there is
  one, else the latest, so a day that ended skipped_budget or failed still shows its state.
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.briefing_schema import LEGACY_SELF_THEME, BriefingContent
from app.api.deps import get_current_user
from app.api.v1.signals import signal_cards
from app.api.visibility import visible_signal_clause
from app.core.db import get_session
from app.models import Briefing, Signal, User
from app.models.enums import LIVE_BRIEFING_STATUSES as LIVE_STATUSES
from app.models.enums import BriefingEdition
from app.schemas.briefings import BriefingOut, BriefingSummary

router = APIRouter(prefix="/briefings", tags=["briefings"])

EDITIONS = {e.value for e in BriefingEdition}
MASTER = BriefingEdition.master.value


def default_edition(user: User) -> str:
    """A role's own edition (role ids are edition ids); analysts and any role without an
    edition read the master."""
    role = str(user.role)
    return role if role in EDITIONS else MASTER


def _served(edition: str) -> Select:
    """One row per date, newest first: the edition's row if the date has one, else the
    master's; within that edition the latest published|quiet version, else the
    latest version."""
    return (
        select(Briefing)
        .where(Briefing.edition.in_(sorted({edition, MASTER})))
        .ext(distinct_on(Briefing.date))
        .order_by(
            Briefing.date.desc(),
            (Briefing.edition == edition).desc(),
            Briefing.status.in_(LIVE_STATUSES).desc(),
            Briefing.version.desc(),
        )
    )


def redact_content(content: BriefingContent, visible_ids: set[int]) -> BriefingContent:
    """A filed briefing shows only what this reader may still see. Takeaways citing
    only invisible signals go (a mixed one keeps its visible ids), as do section and watch
    items with an invisible id, sections left empty, and the legacy "JFrog in the news"
    section. Free-text headline and lead are never rewritten."""
    takeaways = []
    for t in content.top_takeaways:
        ids = [i for i in t.signal_ids if i in visible_ids]
        if ids or not t.signal_ids:
            takeaways.append(t.model_copy(update={"signal_ids": ids}))
    sections = []
    for s in content.sections:
        if s.theme == LEGACY_SELF_THEME:
            continue
        items = [i for i in s.items if i.signal_id in visible_ids]
        if items:
            sections.append(s.model_copy(update={"items": items}))
    return content.model_copy(
        update={
            "top_takeaways": takeaways,
            "sections": sections,
            "watchlist": [w for w in content.watchlist if w.signal_id in visible_ids],
        }
    )


async def _out(session: AsyncSession, user: User, b: Briefing, requested: str) -> BriefingOut:
    # cards and content show only the filed signals this reader may see NOW (status
    # per role, never scope=jfrog); everything else was filed earlier and is redacted.
    by_id = {
        s.id: s
        for s in await session.scalars(
            select(Signal).where(Signal.id.in_(b.signal_ids), visible_signal_clause(user))
        )
    }
    signals = [by_id[i] for i in b.signal_ids if i in by_id]
    content = redact_content(BriefingContent.model_validate(b.content), set(by_id))
    return BriefingOut(
        id=b.id,
        date=b.date,
        edition=b.edition,
        requested_edition=requested,
        version=b.version,
        status=b.status,
        content=content,
        funnel=b.funnel or {},
        signals=await signal_cards(session, signals),
        published_at=b.published_at,
        agent_run_id=b.agent_run_id,
    )


@router.get("", response_model=list[BriefingSummary])
async def list_briefings(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100, description="Number of dates.")] = 14,
) -> list[BriefingSummary]:
    requested = default_edition(user)
    rows = await session.scalars(_served(requested).limit(limit))
    return [
        BriefingSummary(
            id=b.id,
            date=b.date,
            edition=b.edition,
            requested_edition=requested,
            version=b.version,
            status=b.status,
            headline=str((b.content or {}).get("headline", "")),
            published_at=b.published_at,
        )
        for b in rows
    ]


@router.get("/latest", response_model=BriefingOut)
async def latest_briefing(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BriefingOut:
    requested = default_edition(user)
    b = await session.scalar(_served(requested).limit(1))
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No briefing yet")
    return await _out(session, user, b, requested)


@router.get("/{date}", response_model=BriefingOut)
async def briefing_by_date(
    date: dt.date,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BriefingOut:
    requested = default_edition(user)
    b = await session.scalar(_served(requested).where(Briefing.date == date).limit(1))
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No briefing for that date")
    return await _out(session, user, b, requested)
