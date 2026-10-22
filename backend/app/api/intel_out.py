"""Shared pieces of the deep-dive APIs: the clock every route reads (tests pin it
here, once), the converters from the intel dataclasses to the response schemas, and the
postings cursor."""

import base64
import binascii
import json
from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.api.v1.signals import ID_RANGE, MAX_CURSOR_CHARS
from app.intel.hiring import PostingRow, tag_state
from app.intel.profile import Threat, TimelineEvent
from app.models import JobPosting
from app.schemas.intel import PostingOut, ThreatOut, TimelineEventOut


def utcnow() -> datetime:
    return datetime.now(UTC)


def event_out(e: TimelineEvent) -> TimelineEventOut:
    return TimelineEventOut(
        signal_id=e.signal_id, day=e.day, lane=e.lane, importance=e.importance, title=e.title
    )


def threat_out(t: Threat) -> ThreatOut:
    return ThreatOut(
        level=t.level,
        score=t.score,
        overlap=t.overlap,
        momentum_trend=t.momentum_trend,
        hiring_trend=t.hiring_trend,
        explanation=t.explanation,
    )


def posting_out(p: PostingRow | JobPosting) -> PostingOut:
    """A posting row of `hiring_summary` (always open) or a stored posting."""
    return PostingOut(
        id=p.id,
        title=p.title,
        department=p.department,
        seniority=p.seniority,
        location=p.location,
        country=p.country,
        remote=bool(p.remote),
        url=p.url,
        first_seen_at=p.first_seen_at,
        closed_at=p.closed_at if isinstance(p, JobPosting) else None,
        tags=list(p.tags or []),
        tag_state=tag_state(p.tagged_at, p.tags, p.tag_failures)
        if isinstance(p, JobPosting)
        else p.tag_state,
    )


def encode_posting_cursor(first_seen_at: datetime, posting_id: int) -> str:
    payload = {"at": first_seen_at.isoformat(), "id": posting_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_posting_cursor(cursor: str) -> tuple[datetime, int]:
    """A forged or malformed cursor is a 422, never a database error."""
    invalid = HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid cursor")
    if len(cursor) > MAX_CURSOR_CHARS:
        raise invalid
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        at, posting_id = datetime.fromisoformat(data["at"]), data["id"]
    except (binascii.Error, ValueError, KeyError, TypeError, IndexError, UnicodeError):
        raise invalid from None
    if at.tzinfo is None or type(posting_id) is not int or posting_id not in ID_RANGE:
        raise invalid
    return at, posting_id
