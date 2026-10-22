"""Collector contract: value objects, error type and the Collector protocol."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from app.core.http import PoliteClient
from app.models import Source
from app.models.enums import SourceKind


@dataclass(frozen=True)
class RawItemIn:
    external_id: str
    url: str
    title: str | None
    published_at: datetime | None
    raw_text: str | None
    payload: dict


@dataclass(frozen=True)
class MetricPointIn:
    metric: str
    date: date
    value: float


@dataclass(frozen=True)
class PostingIn:
    """One job posting as a job board lists it (stored by collectors.jobs.store)."""

    external_id: str
    title: str
    department_raw: str | None = None
    team: str | None = None
    location: str | None = None
    country: str | None = None
    remote: bool = False
    employment_type: str | None = None
    url: str | None = None
    posted_at: datetime | None = None  # from the ATS when it gives one, never guessed
    description_text: str | None = None  # plain text, at most 4000 characters


@dataclass
class FetchResult:
    items: list[RawItemIn] = field(default_factory=list)
    metrics: list[MetricPointIn] = field(default_factory=list)
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    # Job boards: the postings listed, and whether that listing is the whole board. Only a
    # complete listing may close the postings it lacks (a partial or failed one closes none).
    postings: list[PostingIn] = field(default_factory=list)
    postings_complete: bool = False
    # Page watch: the page's main text, when the fetch succeeded.
    page_text: str | None = None


@dataclass(frozen=True)
class FetchSummary:
    """What one fetch returned, in its source kind's own unit: feed or search items;
    a job board's postings (and whether its listing was the whole board); a watched page,
    one when its text was read (and how much text); a metric source (GitHub repository
    stats), the readings it took. The admin test fetch and check_sources report it, so a
    working board, page or repository never reads "0 items"."""

    count: int
    postings: int | None = None  # job boards
    postings_complete: bool | None = None  # job boards: a partial listing measures nothing
    page_text_chars: int | None = None  # page watch
    metrics: int | None = None  # metric sources: readings, no items


def summarize(kind: str, result: FetchResult) -> FetchSummary:
    if kind == SourceKind.job_board:
        n = len(result.postings)
        return FetchSummary(n, postings=n, postings_complete=result.postings_complete)
    if kind == SourceKind.page_watch:
        chars = len(result.page_text or "")
        return FetchSummary(1 if chars else 0, page_text_chars=chars)
    if result.metrics and not result.items:  # GitHub stars and forks, not items
        return FetchSummary(len(result.metrics), metrics=len(result.metrics))
    return FetchSummary(len(result.items))


class CollectorError(Exception):
    """A fetch failed in a way that should count against the source's health."""


class Collector(Protocol):
    kind: SourceKind

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult: ...
