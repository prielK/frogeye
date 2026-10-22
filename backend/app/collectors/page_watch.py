"""The `page_watch` collector: one request per watched company page per run.

The page is fetched politely (robots.txt respected, generic UA, PoliteClient pacing and its
23 h / 7 d website pauses) and reduced to its main text; `processing.ingest` stores a snapshot
when that text changed (`intel.pages.store_page_snapshot`). A page that robots.txt forbids,
that returns an error status or no text raises, so the source's health is recorded and the
last snapshot stays as it was.
"""

from datetime import datetime

from app.collectors.base import CollectorError, FetchResult
from app.core.http import PoliteClient
from app.models import Source
from app.models.enums import SourceKind
from app.processing.normalize import extract_text


class PageWatchCollector:
    kind = SourceKind.page_watch

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        competitor = source.competitor
        if competitor is not None and competitor.is_self:
            raise CollectorError("the self competitor gets no page watch")
        config = source.config or {}
        url = str(config.get("url") or source.url)
        resp = await http.get(
            url,
            respect_robots=True,
            browser_ua=bool(config.get("browser_ua", False)),
        )
        if not 200 <= resp.status < 300:
            raise CollectorError(f"HTTP {resp.status}")
        text = extract_text(resp.text, url).text.strip()
        if not text:
            raise CollectorError("the page has no text")
        return FetchResult(page_text=text)
