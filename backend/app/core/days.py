"""Local days: FrogEye counts days in `settings.timezone` (Asia/Jerusalem), never in UTC.

One helper for every reader that buckets a moment by day (hiring, the intel aggregates, the
briefs, the bounded LLM retries), so a day boundary is the same everywhere.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def local_day(moment: datetime, tz: str | None = None) -> date:
    """The local day of `moment`, in `tz` (default: settings.timezone)."""
    return moment.astimezone(ZoneInfo(tz or get_settings().timezone)).date()
