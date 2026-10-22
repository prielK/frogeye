"""The shared local-day helper."""

from datetime import UTC, date, datetime
from pathlib import Path

import app
from app.agents import retries
from app.core.days import local_day
from app.intel import facts


def test_local_day_is_the_configured_zones_day():
    # 22:30 UTC is 01:30 the next day in Asia/Jerusalem (UTC+3 in October)
    assert local_day(datetime(2026, 10, 19, 22, 30, tzinfo=UTC)) == date(2026, 10, 20)
    assert local_day(datetime(2026, 10, 19, 22, 30, tzinfo=UTC), "UTC") == date(2026, 10, 19)


def test_retries_share_one_error_cap_and_no_intel_import():
    """One ERROR_CHARS for a recorded failure's reason (the facts extractor uses the
    retries' own), and the retry module needs nothing from app.intel."""
    assert facts.ERROR_CHARS is retries.ERROR_CHARS
    source = (Path(app.__file__).parent / "agents" / "retries.py").read_text(encoding="utf-8")
    assert "app.intel" not in source
