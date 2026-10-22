"""The `job_board` collector: one request per board per run, dispatched on `config.ats`."""

import json
from datetime import datetime

from app.collectors.base import CollectorError, FetchResult
from app.collectors.jobs.ashby import AshbyAdapter
from app.collectors.jobs.common import Adapter, BoardMissing, BoardPage
from app.collectors.jobs.greenhouse import GreenhouseAdapter
from app.collectors.jobs.lever import LeverAdapter
from app.core.config import get_settings
from app.core.http import PoliteClient
from app.models import Source
from app.models.enums import SourceKind

ADAPTERS: dict[str, Adapter] = {
    adapter.name: adapter for adapter in (GreenhouseAdapter(), LeverAdapter(), AshbyAdapter())
}
MISSING_STATUSES = frozenset({404, 410})


def adapter_for(ats: str) -> Adapter:
    try:
        return ADAPTERS[ats]
    except KeyError:
        raise CollectorError(f"unknown ATS {ats!r}") from None


def board_url(ats: str, token: str) -> str:
    """The public endpoint of ``token``'s board on ``ats``."""
    return adapter_for(ats).url(token)


async def fetch_board(ats: str, token: str, http: PoliteClient) -> BoardPage:
    """One request for a whole board.

    Raises BoardMissing when the ATS has no such board, CollectorError for any other bad
    answer, and lets PoliteClient's FetchError (timeouts, the size cap, a paused host)
    through. Any raise means the caller has no listing, so it closes no posting.
    """
    adapter = adapter_for(ats)
    # A documented public API: robots.txt governs crawlers, not API clients.
    resp = await http.get(
        adapter.url(token),
        headers={"Accept": "application/json"},
        respect_robots=False,
        max_bytes=get_settings().ats_max_bytes,
    )
    if resp.status in MISSING_STATUSES:
        raise BoardMissing(f"{adapter.label}: no board {token!r} (HTTP {resp.status})")
    if not 200 <= resp.status < 300:
        raise CollectorError(f"HTTP {resp.status}")
    try:
        data = json.loads(resp.text)
    except ValueError:
        raise CollectorError(f"{adapter.label}: invalid JSON") from None
    return adapter.parse(data)


class JobBoardCollector:
    kind = SourceKind.job_board

    async def fetch(
        self, source: Source, since: datetime | None, http: PoliteClient
    ) -> FetchResult:
        config = source.config or {}
        if config.get("missing"):
            raise CollectorError("no public job board found (a discovery placeholder)")
        ats, token = config.get("ats"), config.get("token")
        if not ats or not token:
            raise CollectorError("a job_board source needs config.ats and config.token")
        competitor = source.competitor
        if competitor is not None and competitor.is_self:
            raise CollectorError("the self competitor gets no job board")
        page = await fetch_board(str(ats), str(token), http)
        return FetchResult(postings=page.postings, postings_complete=page.complete)
