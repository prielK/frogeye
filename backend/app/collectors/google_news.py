"""Google News RSS search collector (one generated source per competitor)."""

from datetime import datetime
from urllib.parse import quote_plus, urlsplit

from app.collectors.base import CollectorError, FetchResult, RawItemIn
from app.collectors.queries import build_competitor_query
from app.collectors.rss import _entry_text, parse_feed
from app.core.http import PoliteClient
from app.models import Source
from app.models.enums import SourceKind

GOOGLE_HOST = "news.google.com"


def build_google_news_url(query: str, days: int) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(f"{query} when:{days}d")
        + "&hl=en-US&gl=US&ceid=US:en"
    )


async def resolve_google_news_url(url: str, http: PoliteClient) -> str | None:
    """Follow Google's redirect to the publisher; None if it never leaves news.google.com."""
    resp = await http.get(url, respect_robots=False)
    final = resp.url or url
    host = (urlsplit(final).hostname or "").lower()
    if not host or host == GOOGLE_HOST:
        return None
    return final


class GoogleNewsCollector:
    kind = SourceKind.google_news

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        if source.competitor is None:
            raise CollectorError("google_news source has no competitor")
        days = int(source.config.get("days", 2))
        url = build_google_news_url(build_competitor_query(source.competitor), days)
        resp = await http.get(
            url,
            # Published subscription feed, fetched like a feed reader: robots.txt ignored.
            respect_robots=False,
            etag=source.etag,
            last_modified=source.last_modified,
            browser_ua=source.config.get("browser_ua", False),
        )
        if resp.status == 304:
            return FetchResult(not_modified=True)
        if not 200 <= resp.status < 300:
            raise CollectorError(f"HTTP {resp.status}")

        resolve = bool(source.config.get("resolve_urls", False))  # opt-in: ~1 s per item
        _, entries = parse_feed(resp.content, since)
        items: list[RawItemIn] = []
        for entry, external_id, link, published in entries:
            src = entry.get("source") or {}
            link = link or external_id
            items.append(
                RawItemIn(
                    external_id=f"gn:{external_id}",
                    url=(await resolve_google_news_url(link, http) if resolve else None) or link,
                    title=entry.get("title"),
                    published_at=published,
                    raw_text=_entry_text(entry),
                    payload={"outlet": src.get("title"), "outlet_url": src.get("href")},
                )
            )
        return FetchResult(
            items=items,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
        )
