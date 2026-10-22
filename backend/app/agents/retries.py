"""Bounded retries of the per-item LLM writers.

The posting tagger, the facts extractor and the page-change judge leave an item for the next
run when its LLM call fails (an LLMError, a schema-invalid answer, a refusal). Without a
bound one bad item is asked again every day, and takes a slot of the run's cap every day. So
each failure is recorded on the item, in its three retry columns (`<prefix>_failures`,
`_failed_at`, `_error`):

- `failures` counts the local days (settings.timezone) its call failed: a second failure on
  the same day is the same failed day (a backfill beside the daily run is not a new day);
- `error` keeps the last failure's reason, `failed_at` its time;
- after MAX_FAILED_DAYS failed days the item is given up: the selections keep only
  `eligible` items, so it is never asked again and the cap counts only items still
  eligible. The posting tagger tries a posting afresh once its content changes (the store
  clears its tags and these columns); a document or page snapshot never changes;
- a success clears the three columns (`cleared`): they describe the failures still
  standing, so a done item never carries a stale count or error.

A budget stop is not a failure (nothing was asked), nor is a database error.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, case, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.config import get_settings
from app.core.days import local_day

MAX_FAILED_DAYS = 3
ERROR_CHARS = 300


@dataclass(frozen=True)
class RetryColumns:
    """One table's retry columns."""

    failures: InstrumentedAttribute[int]
    failed_at: InstrumentedAttribute[Any]
    error: InstrumentedAttribute[Any]


def cleared(cols: RetryColumns) -> dict[str, Any]:
    """The values that clear an item's retry columns, for a success's own write."""
    return {cols.failures.key: 0, cols.failed_at.key: None, cols.error.key: None}


def eligible(cols: RetryColumns) -> ColumnElement[bool]:
    """An item not given up: fewer than MAX_FAILED_DAYS failed days."""
    return cols.failures < MAX_FAILED_DAYS


async def record_failure(
    session: AsyncSession,
    model: Any,
    cols: RetryColumns,
    *where: ColumnElement[bool],
    reason: str,
    now: datetime,
) -> int:
    """Record a failed call on the rows matching `where` (flush only, the caller commits).
    Returns how many of them were given up by it (reached MAX_FAILED_DAYS failed days)."""
    tz = get_settings().timezone
    new_day = or_(
        cols.failed_at.is_(None),
        func.date(func.timezone(tz, cols.failed_at)) < local_day(now, tz),
    )
    rows = await session.execute(
        update(model)
        .where(*where)
        .values(
            {
                cols.failures.key: cols.failures + case((new_day, 1), else_=0),
                cols.failed_at.key: now,
                cols.error.key: reason[:ERROR_CHARS],
            }
        )
        .returning(cols.failures)
    )
    return sum(1 for (failures,) in rows.all() if failures >= MAX_FAILED_DAYS)
