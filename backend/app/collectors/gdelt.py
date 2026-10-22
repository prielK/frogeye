"""GDELT DOC 2.0 API collector."""

import hashlib
import json
import re
from datetime import UTC, datetime
from urllib.parse import quote

from app.collectors.base import CollectorError, FetchResult, RawItemIn
from app.collectors.queries import build_competitor_query
from app.core.http import PoliteClient
from app.models import Source
from app.models.enums import SourceKind


def build_gdelt_url(query: str, timespan: str, maxrecords: int = 75) -> str:
    return (
        "https://api.gdeltproject.org/api/v2/doc/doc?query="
        + quote(f"{query} sourcelang:english")
        + f"&mode=ArtList&format=json&sort=DateDesc&timespan={timespan}&maxrecords={maxrecords}"
    )


# A quoted single word: GDELT answers "The specified phrase is too short" for these.
_SINGLE_WORD_PHRASE = re.compile(r'"([A-Za-z0-9]+)"')
# A parenthesised single term (quoted phrase or bare word) at the start of the query.
_SINGLE_TERM_GROUP = re.compile(r'^\(("[^"]*"|[^\s()"]+)\)')


def _gdelt_query(query: str) -> str:
    """Adapt the shared competitor query to GDELT's syntax.

    GDELT rejects quoted single words ("phrase too short"), so single-token terms lose their
    quotes (multi-word and punctuated terms such as "Socket.dev" keep them), and it rejects
    parentheses around a single term ("Parentheses may only be used around OR'd").
    """
    query = _SINGLE_WORD_PHRASE.sub(lambda m: m.group(1), query)
    return _SINGLE_TERM_GROUP.sub(lambda m: m.group(1), query)


def _seen(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


class GdeltCollector:
    kind = SourceKind.gdelt

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        if source.competitor is None:
            raise CollectorError("gdelt source has no competitor")
        url = build_gdelt_url(
            _gdelt_query(build_competitor_query(source.competitor)),
            source.config.get("timespan", "1d"),
        )
        # Documented API: robots.txt governs crawlers, not API clients.
        resp = await http.get(url, respect_robots=False)
        if not 200 <= resp.status < 300:
            raise CollectorError(f"HTTP {resp.status}")
        try:
            data = json.loads(resp.text)
        except ValueError:
            # GDELT answers 200 with plain text when rate-limited or the query is invalid.
            raise CollectorError(f"GDELT: {resp.text[:200].strip()}") from None
        items: list[RawItemIn] = []
        for a in data.get("articles") or []:
            article_url = a.get("url")
            if not article_url:
                continue
            published = _seen(a.get("seendate"))
            if since is not None and published is not None and published < since:
                continue
            items.append(
                RawItemIn(
                    external_id="gdelt:" + hashlib.sha1(article_url.encode()).hexdigest(),
                    url=article_url,
                    title=a.get("title"),
                    published_at=published,
                    raw_text=None,
                    payload={
                        "domain": a.get("domain"),
                        "language": a.get("language"),
                        "sourcecountry": a.get("sourcecountry"),
                    },
                )
            )
        return FetchResult(items=items)
