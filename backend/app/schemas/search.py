from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class QueryPlanOut(BaseModel):
    rewritten: str
    variants: list[str]
    competitors: list[str]
    date_from: date | None
    date_to: date | None
    kinds: list[str]


class SearchHitOut(BaseModel):
    kind: str
    ref_id: int
    title: str
    url: str | None
    snippet: str
    published_at: datetime | None
    competitors: list[str]
    score: float


class SearchResponse(BaseModel):
    plan: QueryPlanOut | None
    hits: list[SearchHitOut]
    search_mode: Literal["hybrid", "keyword"] = Field(
        description='"keyword" when no query embedding could be had (no OpenAI key, an '
        "embedding error or the LLM budget): the hits match the words, not the meaning."
    )
