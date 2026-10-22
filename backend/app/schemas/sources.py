from datetime import datetime

from pydantic import BaseModel, Field


class SourceOut(BaseModel):
    id: int
    kind: str
    name: str
    url: str
    competitor_slug: str | None
    reliability: str
    enabled: bool
    health: str
    consecutive_failures: int
    last_fetched_at: datetime | None
    last_error: str | None
    items_7d: int


class SourcePatch(BaseModel):
    enabled: bool


class SourceSample(BaseModel):
    title: str | None
    url: str
    published_at: datetime | None


class SourceTestOut(BaseModel):
    """A dry run of one source's collector (nothing is stored)."""

    ok: bool
    item_count: int = Field(
        description="What the fetch returned, in the source's own unit: feed or search items; "
        "a job board's postings (postings_count); 1 for a watched page whose text was read "
        "(page_text_chars); a repository's metric readings (metrics_count)."
    )
    sample: list[SourceSample] = Field(
        description="The first 5 items, a job board's first 5 postings (published_at: the "
        "ATS's posting date, if it gives one), or the watched page itself."
    )
    error: str | None
    postings_count: int | None = Field(
        description="Job boards: the postings the listing holds; null for other kinds."
    )
    postings_complete: bool | None = Field(
        description="Job boards: whether the listing was the whole board. A partial one (a "
        "page short of its stated total) stores postings but measures nothing; null for "
        "other kinds."
    )
    page_text_chars: int | None = Field(
        description="Page watch: characters of main text read from the page; null for other kinds."
    )
    metrics_count: int | None = Field(
        description="Metric sources (GitHub repository stats): the readings taken (stars and "
        "forks; item_count says the same), never items; null for other kinds."
    )
