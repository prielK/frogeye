"""Ashby job posting API (public, unauthenticated, one response per board)."""

from typing import Any
from urllib.parse import quote

from app.collectors.base import PostingIn
from app.collectors.jobs.common import (
    UNTITLED,
    BoardMissing,
    BoardPage,
    country_name,
    country_of,
    html_to_text,
    mentions_remote,
    parse_iso,
    plain,
    text_or_none,
)

_EMPLOYMENT = {
    "FullTime": "Full-time",
    "PartTime": "Part-time",
    "Intern": "Intern",
    "Contract": "Contract",
    "Temporary": "Temporary",
}


def _country(job: dict) -> str | None:
    address = job.get("address")
    postal = address.get("postalAddress") if isinstance(address, dict) else None
    return country_name(postal.get("addressCountry")) if isinstance(postal, dict) else None


class AshbyAdapter:
    name = "ashby"
    label = "Ashby"

    def url(self, token: str) -> str:
        return (
            "https://api.ashbyhq.com/posting-api/job-board/"
            f"{quote(token, safe='')}?includeCompensation=true"
        )

    def parse(self, data: Any) -> BoardPage:
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise BoardMissing("Ashby: not a job board listing")
        postings: list[PostingIn] = []
        for job in data["jobs"]:
            if not isinstance(job, dict) or not text_or_none(job.get("id")):
                continue
            if job.get("isListed") is False:  # unlisted jobs are not public postings
                continue
            place = text_or_none(job.get("location"))
            workplace = (text_or_none(job.get("workplaceType")) or "").lower()
            kind = text_or_none(job.get("employmentType"))
            postings.append(
                PostingIn(
                    external_id=job["id"].strip(),
                    title=text_or_none(job.get("title")) or UNTITLED,
                    department_raw=text_or_none(job.get("department")),
                    team=text_or_none(job.get("team")),
                    location=place,
                    country=_country(job) or country_of(place),
                    remote=job.get("isRemote") is True
                    or workplace == "remote"
                    or mentions_remote(place),
                    employment_type=_EMPLOYMENT.get(kind, kind) if kind else None,
                    url=text_or_none(job.get("jobUrl")),
                    posted_at=parse_iso(job.get("publishedAt")),
                    description_text=plain(job.get("descriptionPlain"))
                    or html_to_text(job.get("descriptionHtml")),
                )
            )
        return BoardPage(postings=postings)
