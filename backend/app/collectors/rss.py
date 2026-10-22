"""RSS / Atom collector (feedparser)."""

import calendar
from datetime import UTC, datetime, timedelta

import feedparser

from app.collectors.base import CollectorError, FetchResult, RawItemIn
from app.core.http import PoliteClient
from app.models import Source
from app.models.enums import SourceKind

FUTURE_TOLERANCE = timedelta(days=1)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _entry_date(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    dt = datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    if dt > utcnow() + FUTURE_TOLERANCE:
        return None
    return dt


def _entry_text(entry) -> str | None:
    content = entry.get("content")
    if content and content[0].get("value"):
        return content[0]["value"]
    return entry.get("summary") or None


def parse_feed(content: bytes, since: datetime | None):
    """Parse a feed; returns (feed, [(entry, external_id, link, published)]) past `since`."""
    feed = feedparser.parse(content)
    if feed.get("bozo") and not feed.entries:
        raise CollectorError("unparseable feed")
    entries = []
    for entry in feed.entries:
        link = entry.get("link")
        external_id = entry.get("id") or link
        if not external_id:
            continue
        published = _entry_date(entry)
        if since is not None and published is not None and published < since:
            continue
        entries.append((entry, external_id, link, published))
    return feed, entries


class RssCollector:
    kind = SourceKind.rss

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        # Feeds are fetched the way a feed reader would: robots.txt is not consulted.
        resp = await http.get(
            source.url,
            etag=source.etag,
            last_modified=source.last_modified,
            respect_robots=False,
            browser_ua=source.config.get("browser_ua", False),
        )
        if resp.status == 304:
            return FetchResult(not_modified=True)
        if resp.status >= 400 or not 200 <= resp.status < 300:
            raise CollectorError(f"HTTP {resp.status}")

        feed, entries = parse_feed(resp.content, since)
        feed_title = feed.feed.get("title")
        items: list[RawItemIn] = []
        for entry, external_id, link, published in entries:
            items.append(
                RawItemIn(
                    external_id=external_id,
                    url=link or external_id,
                    title=entry.get("title"),
                    published_at=published,
                    raw_text=_entry_text(entry),
                    payload={
                        "feed_title": feed_title,
                        "author": entry.get("author"),
                        "tags": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
                    },
                )
            )
        return FetchResult(
            items=items,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
        )
