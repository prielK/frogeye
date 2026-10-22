"""Lever postings API (public, unauthenticated, one response per board)."""

from typing import Any
from urllib.parse import quote

from app.collectors.base import PostingIn
from app.collectors.jobs.common import (
    UNTITLED,
    BoardMissing,
    BoardPage,
    country_name,
    country_of,
    from_epoch_ms,
    html_to_text,
    mentions_remote,
    plain,
    text_or_none,
)


def _description(posting: dict) -> str | None:
    parts = [plain(posting.get("descriptionPlain")) or html_to_text(posting.get("description"))]
    for section in posting.get("lists") or []:
        if isinstance(section, dict):
            parts += [plain(section.get("text")), html_to_text(section.get("content"))]
    parts.append(plain(posting.get("additionalPlain")) or html_to_text(posting.get("additional")))
    return plain("\n".join(p for p in parts if p))


class LeverAdapter:
    name = "lever"
    label = "Lever"

    def url(self, token: str) -> str:
        return f"https://api.lever.co/v0/postings/{quote(token, safe='')}?mode=json"

    def parse(self, data: Any) -> BoardPage:
        if not isinstance(data, list):
            raise BoardMissing("Lever: not a postings list")
        postings: list[PostingIn] = []
        for posting in data:
            if not isinstance(posting, dict) or not text_or_none(posting.get("id")):
                continue
            categories = posting.get("categories")
            cats = categories if isinstance(categories, dict) else {}
            place = text_or_none(cats.get("location"))
            workplace = (text_or_none(posting.get("workplaceType")) or "").lower()
            postings.append(
                PostingIn(
                    external_id=posting["id"].strip(),
                    title=text_or_none(posting.get("text")) or UNTITLED,
                    department_raw=text_or_none(cats.get("department")),
                    team=text_or_none(cats.get("team")),
                    location=place,
                    country=country_name(posting.get("country")) or country_of(place),
                    remote=workplace == "remote" or mentions_remote(place),
                    employment_type=text_or_none(cats.get("commitment")),
                    url=text_or_none(posting.get("hostedUrl")),
                    posted_at=from_epoch_ms(posting.get("createdAt")),
                    description_text=_description(posting),
                )
            )
        return BoardPage(postings=postings)
