"""Posting sync: one board snapshot opens, updates and (only when complete) closes postings."""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import PostingIn
from app.collectors.jobs.common import UNTITLED, clip
from app.collectors.jobs.taxonomy import normalize_department, seniority_of
from app.models import Competitor, JobPosting, Source

logger = logging.getLogger(__name__)

# complete-but-empty listings in a row it takes to close a board's open postings.
EMPTY_LISTINGS_TO_CLOSE = 2


@dataclass(frozen=True)
class PostingSync:
    new: int  # postings seen for the first time
    updated: int  # known postings whose stored fields changed (a reopened one included)
    closed: int  # open postings this complete snapshot no longer lists
    open: int  # the board's open postings afterwards


def content_hash(title: str, description_text: str | None) -> str:
    """What the posting tagger reads: a change here means the tags must be redone."""
    return hashlib.sha256(f"{title}\n{description_text or ''}".encode()).hexdigest()


def _fields(p: PostingIn, competitor_id: int) -> dict:
    title = p.title.strip() or UNTITLED
    description = clip(p.description_text) if p.description_text else None
    return {
        "competitor_id": competitor_id,
        "title": title,
        "department_raw": p.department_raw,
        "department": normalize_department(p.department_raw or p.team, title),
        "team": p.team,
        "seniority": seniority_of(title),
        "location": p.location,
        "country": p.country,
        "remote": bool(p.remote),
        "employment_type": p.employment_type,
        "url": p.url,
        "posted_at": p.posted_at,
        "description_text": description,
        "content_hash": content_hash(title, description),
    }


async def sync_postings(
    session: AsyncSession,
    source: Source,
    postings: list[PostingIn],
    *,
    complete: bool,
    now: datetime,
) -> PostingSync:
    """Upsert ``postings`` on (source_id, external_id) as seen at ``now``. Flush only.

    - A new posting is opened (first_seen_at = last_seen_at = now).
    - A known one is refreshed (last_seen_at = now) and reopened if it had closed. If its
      content_hash changed, its tags (and its failed tagging days) are cleared so the
      tagger reads it again.
    - Only a ``complete`` snapshot closes the open postings it lacks (closed_at = now): a
      partial listing proves nothing about the postings it does not show.
    - A complete listing with no postings at all closes nothing the first time; only the
      second such listing in a row does (counted in ``source.consecutive_empty``,
      which any listing with postings resets).
    - The first ``complete`` snapshot, empty or not, is the board's first complete listing
      (``source.first_complete_fetch_at``): only from it are the board's numbers
      measured, and its local day is the board's history start (``board_since``). Every
      ``complete`` snapshot is its latest (``source.last_complete_fetch_at``): a board
      without one for ``board_stale_days`` is stale and serves no current numbers.
    - The self competitor never gets postings: nothing is stored for it.
    """
    if source.competitor_id is None:
        raise ValueError(f"source {source.id} has no competitor; postings need one")
    is_self = await session.scalar(
        select(Competitor.is_self).where(Competitor.id == source.competitor_id)
    )
    if is_self:
        logger.warning("source %s belongs to the self competitor: postings dropped", source.id)
        return PostingSync(new=0, updated=0, closed=0, open=0)

    ats = str((source.config or {}).get("ats") or source.kind)
    incoming: dict[str, PostingIn] = {}
    for p in postings:  # a listing may repeat an id; the first one wins
        incoming.setdefault(p.external_id, p)
    # the open postings (to close or refresh) and the closed ones listed again (to reopen);
    # a long history of closed postings is never loaded
    known = {
        row.external_id: row
        for row in (
            await session.scalars(
                select(JobPosting).where(
                    JobPosting.source_id == source.id,
                    or_(
                        JobPosting.closed_at.is_(None),
                        JobPosting.external_id.in_(list(incoming)),
                    ),
                )
            )
        ).all()
    }

    new = updated = closed = 0
    for external_id, posting in incoming.items():
        fields = _fields(posting, source.competitor_id)
        row = known.get(external_id)
        if row is None:
            session.add(
                JobPosting(
                    source_id=source.id,
                    ats=ats,
                    external_id=external_id,
                    first_seen_at=now,
                    last_seen_at=now,
                    tags=[],
                    **fields,
                )
            )
            new += 1
            continue
        changed = row.closed_at is not None
        if row.content_hash != fields["content_hash"]:
            row.tags, row.tagged_at, row.tag_version = [], None, None
            row.tag_failures, row.tag_failed_at, row.tag_error = 0, None, None
            changed = True
        if fields["posted_at"] is None:
            del fields["posted_at"]  # a listing without a date keeps the one already known
        for name, value in fields.items():
            if getattr(row, name) != value:
                setattr(row, name, value)
                changed = True
        row.closed_at = None
        row.last_seen_at = now
        updated += changed

    # an empty listing may be an ATS glitch (a board briefly unpublished), so only the
    # second empty one in a row may close the board's postings; any listing with postings
    # resets the streak.
    if complete and source.first_complete_fetch_at is None:
        source.first_complete_fetch_at = now
    if complete:
        source.last_complete_fetch_at = now
    if incoming:
        source.consecutive_empty = 0
    elif complete:
        source.consecutive_empty = (source.consecutive_empty or 0) + 1
    confirmed = bool(incoming) or source.consecutive_empty >= EMPTY_LISTINGS_TO_CLOSE
    if complete and confirmed:
        for external_id, row in known.items():
            if external_id not in incoming and row.closed_at is None:
                row.closed_at = now
                closed += 1
    elif complete:
        logger.info(
            "source %s listed no postings (%s in a row): nothing closed yet",
            source.id,
            source.consecutive_empty,
        )

    await session.flush()
    still_open = await session.scalar(
        select(func.count())
        .select_from(JobPosting)
        .where(JobPosting.source_id == source.id, JobPosting.closed_at.is_(None))
    )
    return PostingSync(new=new, updated=updated, closed=closed, open=int(still_open or 0))
