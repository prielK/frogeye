"""Greenhouse job board API (public, unauthenticated, one response per board)."""

from typing import Any
from urllib.parse import quote

from app.collectors.base import PostingIn
from app.collectors.jobs.common import (
    UNTITLED,
    BoardMissing,
    BoardPage,
    country_of,
    html_to_text,
    mentions_remote,
    parse_iso,
    text_or_none,
)


def _names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [n for n in (text_or_none(i.get("name")) for i in items if isinstance(i, dict)) if n]


class GreenhouseAdapter:
    name = "greenhouse"
    label = "Greenhouse"

    def url(self, token: str) -> str:
        return (
            f"https://boards-api.greenhouse.io/v1/boards/{quote(token, safe='')}/jobs?content=true"
        )

    def parse(self, data: Any) -> BoardPage:
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise BoardMissing("Greenhouse: not a job board listing")
        postings: list[PostingIn] = []
        for job in data["jobs"]:
            if not isinstance(job, dict) or job.get("id") is None:
                continue
            location = job.get("location")
            place = text_or_none(location.get("name")) if isinstance(location, dict) else None
            departments = _names(job.get("departments"))
            postings.append(
                PostingIn(
                    external_id=str(job["id"]),
                    title=text_or_none(job.get("title")) or UNTITLED,
                    department_raw=departments[0] if departments else None,
                    location=place,
                    country=country_of(place),
                    remote=mentions_remote(place, *_names(job.get("offices"))),
                    url=text_or_none(job.get("absolute_url")),
                    posted_at=parse_iso(job.get("first_published")),
                    # Greenhouse sends `content` as entity-escaped HTML
                    description_text=html_to_text(job.get("content"), escaped=True),
                )
            )
        meta = data.get("meta")
        total = meta.get("total") if isinstance(meta, dict) else None
        # a board that says it has more jobs than it listed sent a partial page
        partial = isinstance(total, int) and total > len(data["jobs"])
        return BoardPage(postings=postings, complete=not partial)
