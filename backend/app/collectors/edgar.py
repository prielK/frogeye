"""SEC EDGAR filings collector (data.sec.gov submissions API)."""

import json
from datetime import UTC, datetime, timedelta

from app.collectors.base import CollectorError, FetchResult, RawItemIn
from app.core.config import get_settings
from app.core.http import PoliteClient
from app.models import Source
from app.models.enums import SourceKind

FORMS = frozenset({"8-K", "10-Q", "10-K", "S-1", "DEF 14A"})
DEFAULT_LOOKBACK = timedelta(days=90)


def _col(recent: dict, key: str, i: int) -> str:
    col = recent.get(key) or []
    return (col[i] if i < len(col) else "") or ""


class EdgarCollector:
    kind = SourceKind.sec_edgar

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        ua = get_settings().sec_user_agent
        if not ua or "example.com" in ua.lower():
            raise CollectorError(
                "SEC_USER_AGENT not configured — set a real name and email in .env"
            )
        cik = str(source.config.get("cik", "")).strip()
        if not cik.isdigit():
            raise CollectorError("sec_edgar source has no valid cik")
        url = f"https://data.sec.gov/submissions/CIK{cik:0>10}.json"
        # Documented API: robots.txt governs crawlers, not API clients.
        resp = await http.get(url, headers={"User-Agent": ua}, respect_robots=False)
        if not 200 <= resp.status < 300:
            raise CollectorError(f"HTTP {resp.status}")
        try:
            data = json.loads(resp.text)
        except ValueError:
            raise CollectorError("EDGAR: invalid JSON") from None
        company = data.get("name") or (source.competitor.name if source.competitor else cik)
        recent = (data.get("filings") or {}).get("recent") or {}
        cutoff = (since or datetime.now(UTC) - DEFAULT_LOOKBACK).date()
        items: list[RawItemIn] = []
        for i, accession in enumerate(recent.get("accessionNumber") or []):
            form = _col(recent, "form", i)
            doc = _col(recent, "primaryDocument", i)
            filing_date = _col(recent, "filingDate", i)
            if form not in FORMS or not doc or not filing_date:
                continue
            try:
                filed = datetime.strptime(filing_date, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                continue
            if filed.date() < cutoff:
                continue
            description = _col(recent, "primaryDocDescription", i) or form
            items.append(
                RawItemIn(
                    external_id=accession,
                    url=(
                        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                        f"{accession.replace('-', '')}/{doc}"
                    ),
                    title=f"{company} {form}: {description} ({filing_date})",
                    published_at=filed,
                    raw_text=None,
                    payload={"form": form, "items": _col(recent, "items", i)},
                )
            )
        return FetchResult(items=items)
