from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agents.briefing_schema import BriefingContent
from app.schemas.signals import SignalCard

_EDITION = "The edition of the row served: the requested one, or master when the date has none."
_REQUESTED = (
    "The reader's own edition (their role's; master for analysts). When it differs from "
    '`edition`, the date has no such edition and its master is shown ("Showing Master").'
)


class BriefingSummary(BaseModel):
    id: int
    date: date
    edition: str = Field(description=_EDITION)
    requested_edition: str = Field(description=_REQUESTED)
    version: int
    status: str = Field(description="published | quiet | skipped_budget | failed")
    headline: str
    published_at: datetime | None


class BriefingOut(BaseModel):
    id: int
    date: date
    edition: str = Field(description=_EDITION)
    requested_edition: str = Field(description=_REQUESTED)
    version: int
    status: str = Field(description="published | quiet | skipped_budget | failed")
    content: BriefingContent
    funnel: dict[str, Any] = Field(
        description="The day's run counts, summed over the runs that filed this date (fetched, "
        "documents_created, prefilter_passed, triaged, relevant, signals_new, "
        "signals_analyzed, signals_deep_analyzed, postings_tagged, page_changes, "
        "facts_extracted, signals_breaking), plus briefing_status and runs (how many runs "
        "were summed); {} when the briefing has no run counts."
    )
    signals: list[SignalCard] = Field(description="The briefing's signals, as filed.")
    published_at: datetime | None
    agent_run_id: int | None
