"""Hacker News (Algolia search API) collector."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from app.collectors.base import CollectorError, FetchResult, RawItemIn
from app.core.http import PoliteClient
from app.models import Source
from app.models.enums import SourceKind

DEFAULT_LOOKBACK = timedelta(days=2)


def build_hn_url(query: str, since_ts: int) -> str:
    return (
        "https://hn.algolia.com/api/v1/search_by_date?query="
        + quote(query)
        + f"&tags=story&numericFilters=created_at_i>{since_ts}&hitsPerPage=50"
    )


class HnCollector:
    kind = SourceKind.hn

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        if source.competitor is None:
            raise CollectorError("hn source has no competitor")
        since_ts = int((since or datetime.now(UTC) - DEFAULT_LOOKBACK).timestamp())
        url = build_hn_url(source.competitor.name, since_ts)
        # Documented API: robots.txt governs crawlers, not API clients.
        resp = await http.get(url, respect_robots=False)
        if not 200 <= resp.status < 300:
            raise CollectorError(f"HTTP {resp.status}")
        try:
            hits = json.loads(resp.text).get("hits") or []
        except ValueError:
            raise CollectorError("HN: invalid JSON") from None
        items: list[RawItemIn] = []
        for h in hits:
            oid = h.get("objectID")
            if not oid:
                continue
            hn_url = f"https://news.ycombinator.com/item?id={oid}"
            ts = h.get("created_at_i")
            items.append(
                RawItemIn(
                    external_id=f"hn:{oid}",
                    url=h.get("url") or hn_url,
                    title=h.get("title"),
                    published_at=datetime.fromtimestamp(ts, tz=UTC) if ts else None,
                    raw_text=h.get("story_text"),
                    payload={
                        "points": h.get("points"),
                        "num_comments": h.get("num_comments"),
                        "hn_url": hn_url,
                    },
                )
            )
        return FetchResult(items=items)
