"""At most one request per job board or watched page per local day.

The collection footprint rule: FrogEye reads a public job board or a watched company
page at most once a day. Every path that fetches one claims it first:

- ingestion runs, scheduled or manual, and the daily graph's `ingest` node
  (`app.processing.ingest`), whether a run lists its sources or not;
- `app.cli.check_sources`;
- the admin "test fetch" (`POST /sources/{id}/test`);
- job-board discovery, whose probe stores the board's first snapshot.

A claim (`claim_daily_fetches`) stamps `sources.last_attempt_at` BEFORE the request, in one
UPDATE that only matches sources not attempted since the local day began (settings.timezone,
Asia/Jerusalem). It is atomic: of two runs racing for a board, one gets it. A failed request
counts too, as does one a paused host never sent: the day's attempt was spent either way, so
a failing page is not retried by every manual run.

One operator-only escape hatch lifts the guard for one run: `python -m app.cli.run_ingestion
--allow-refetch-today` (the same-day acceptance snapshot). It logs a warning, and the
scheduler never passes it.
"""

from collections.abc import Collection
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.days import local_day
from app.models import Source
from app.models.enums import SourceKind

ONCE_A_DAY = frozenset({SourceKind.job_board.value, SourceKind.page_watch.value})
ALREADY_FETCHED = "Already fetched today; next fetch tomorrow"


def once_a_day(kind: str) -> bool:
    """Whether a source of this kind is read at most once per local day."""
    return str(kind) in ONCE_A_DAY


def day_start(now: datetime, tz: str | None = None) -> datetime:
    """The moment the local day holding `now` began, in UTC."""
    zone = ZoneInfo(tz or get_settings().timezone)
    return datetime.combine(local_day(now, zone.key), time(0), zone).astimezone(UTC)


async def claim_daily_fetches(
    session: AsyncSession,
    source_ids: Collection[int],
    *,
    now: datetime,
    allow_refetch_today: bool = False,
) -> set[int]:
    """Claim today's one request for each of `source_ids` not attempted since the local day
    began (every one of them with `allow_refetch_today`): their `last_attempt_at` becomes
    `now`. Returns the ids claimed; the rest were already read today. Flush only: the caller
    commits before sending any request."""
    ids = sorted(set(source_ids))
    if not ids:
        return set()
    stmt = update(Source).where(Source.id.in_(ids))
    if not allow_refetch_today:
        stmt = stmt.where(
            or_(Source.last_attempt_at.is_(None), Source.last_attempt_at < day_start(now))
        )
    rows = await session.execute(
        stmt.values(last_attempt_at=now)
        .returning(Source.id)
        .execution_options(synchronize_session=False)
    )
    return set(rows.scalars().all())
