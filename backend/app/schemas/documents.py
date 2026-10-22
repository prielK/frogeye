from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: int
    title: str | None
    url: str
    source_name: str
    published_at: datetime | None
    status: str
    scope_hint: str | None
    competitors: list[str]
    matched_terms: list[str]
    excerpt: str


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    next_cursor: str | None
