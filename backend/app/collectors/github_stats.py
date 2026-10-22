"""GitHub REST API collectors: repo stats (metrics) and releases (items)."""

import json
from datetime import UTC, date, datetime

from app.collectors.base import CollectorError, FetchResult, MetricPointIn, RawItemIn
from app.core.config import get_settings
from app.core.http import HttpResponse, PoliteClient
from app.models import Source
from app.models.enums import SourceKind


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _check(resp: HttpResponse) -> None:
    if resp.status == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        raise CollectorError("GitHub rate limit exceeded")
    if not 200 <= resp.status < 300:
        raise CollectorError(f"HTTP {resp.status}")


def _json(resp: HttpResponse):
    try:
        return json.loads(resp.text)
    except ValueError:
        raise CollectorError("GitHub: invalid JSON") from None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class GithubStatsCollector:
    kind = SourceKind.github_repo_stats

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        # Documented API: robots.txt governs crawlers, not API clients.
        resp = await http.get(
            source.url,
            headers=_headers(),
            respect_robots=False,
            browser_ua=source.config.get("browser_ua", False),
        )
        _check(resp)
        data = _json(resp)
        try:
            full_name = data["full_name"]
            stars, forks = data["stargazers_count"], data["forks_count"]
        except (KeyError, TypeError):
            raise CollectorError("GitHub: unexpected repo payload") from None
        today: date = datetime.now(UTC).date()
        return FetchResult(
            metrics=[
                MetricPointIn(f"github_stars:{full_name}", today, stars),
                MetricPointIn(f"github_forks:{full_name}", today, forks),
            ]
        )


class GithubReleasesCollector:
    kind = SourceKind.github_releases

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        resp = await http.get(
            f"{source.url}?per_page=20",
            headers=_headers(),
            respect_robots=False,
            browser_ua=source.config.get("browser_ua", False),
        )
        _check(resp)
        data = _json(resp)
        if not isinstance(data, list):
            raise CollectorError("GitHub: unexpected releases payload")
        repo = source.url.split("/repos/", 1)[-1].removesuffix("/releases").strip("/")
        items: list[RawItemIn] = []
        for rel in data:
            if rel.get("draft") or rel.get("id") is None or not rel.get("html_url"):
                continue
            published = _parse_ts(rel.get("published_at")) or _parse_ts(rel.get("created_at"))
            if since is not None and published is not None and published < since:
                continue
            items.append(
                RawItemIn(
                    external_id=f"ghrel:{rel['id']}",
                    url=rel["html_url"],
                    title=f"{repo} {rel.get('name') or rel.get('tag_name')}",
                    published_at=published,
                    raw_text=rel.get("body"),
                    payload={
                        "tag_name": rel.get("tag_name"),
                        "prerelease": bool(rel.get("prerelease")),
                        "repo": repo,
                    },
                )
            )
        return FetchResult(items=items)
