"""Live source check: fetch every enabled seed source once.

Uses the persistent host breaker when the DB is reachable (paused hosts are not probed and
403/429 are recorded); otherwise an in-memory breaker for the duration of the probe.

A job board or watched page is fetched at most once per local day, by any path
(app.collectors.daily_guard), so it counts against its database row: one already read
today is skipped ("Already fetched today; next fetch tomorrow"), and one checked here is
today's attempt (the day's run then skips it). One that is not in the database yet (the seed
not loaded), or any when the database does not answer, is skipped too: there is no row to
tell whether it was read today.

Usage: python -m app.cli.check_sources [--kind rss]
"""

import argparse
import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors import get_collector
from app.collectors.base import FetchSummary, summarize
from app.collectors.daily_guard import ALREADY_FETCHED, claim_daily_fetches, once_a_day
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.http import HostGuard, HostPaused, InMemoryHostGuard, PoliteClient
from app.models import Competitor, Source
from app.seed.loader import SourceSpec, read_seed

NOT_SEEDED = "not in the database yet: load the seed first (it is read once a day)"
NO_DATABASE = "the database does not answer: cannot tell whether it was read today"

CONCURRENCY = 8


def _build(spec: SourceSpec, competitors: dict[str, Competitor]) -> Source:
    return Source(
        competitor=competitors.get(spec.competitor_slug) if spec.competitor_slug else None,
        kind=spec.kind,
        name=spec.name,
        url=spec.url,
        reliability=spec.reliability,
        enabled=spec.enabled,
        config=dict(spec.config),
    )


def _competitor(row: dict) -> Competitor:
    fields = ("slug", "name", "aliases", "ambiguous_aliases", "negative_keywords")
    return Competitor(**{k: row.get(k, []) for k in fields})


async def _check(src: Source, client: PoliteClient, sem: asyncio.Semaphore) -> tuple:
    try:
        collector = get_collector(src.kind)
    except KeyError:
        return (src.kind, src.name, "no-collector", "", "")
    async with sem:
        try:
            res = await collector.fetch(src, None, client)
        except HostPaused as e:
            return (src.kind, src.name, "paused", "", str(e)[:160])
        except Exception as e:  # report any failure, keep checking the rest
            msg = str(e) or type(e).__name__
            return (src.kind, src.name, "FAIL", "", f"{type(e).__name__}: {msg}"[:120])
    status = "not-modified" if res.not_modified else "ok"
    return (src.kind, src.name, status, returned(summarize(src.kind, res)), "")


def returned(summary: FetchSummary) -> str:
    """The items column, in the source's own unit: "12", "3 postings", "1 posting
    (partial listing)", "1 page, 4512 chars", "2 readings" (a repository's stars and
    forks)."""
    if summary.metrics is not None:
        return f"{summary.metrics} {'reading' if summary.metrics == 1 else 'readings'}"
    if summary.postings is not None:
        noun = "posting" if summary.postings == 1 else "postings"
        partial = "" if summary.postings_complete else " (partial listing)"
        return f"{summary.postings} {noun}{partial}"
    if summary.page_text_chars is not None:
        return f"1 page, {summary.page_text_chars} chars" if summary.count else "no page text"
    return str(summary.count)


async def make_guard(connect_timeout: float = 3.0) -> HostGuard:
    """DB-backed guard if the database answers quickly, else an in-memory one."""
    from app.core.host_guard import DbHostGuard

    try:
        async with SessionLocal() as session:
            await asyncio.wait_for(
                session.execute(text("select 1 from host_blocks limit 1")), connect_timeout
            )
    except Exception:  # noqa: BLE001 - DB down / not migrated: probe without persistence
        return InMemoryHostGuard()
    return DbHostGuard(SessionLocal)


async def daily_gate(
    sources: Sequence[Source],
    session_factory: Callable[[], AsyncSession],
    *,
    now: datetime,
) -> tuple[list[tuple], list[Source]]:
    """(rows for the boards and pages skipped, the sources to check). Each board or page is
    matched to its database row by (kind, url) and claimed there (see the module docstring)."""
    daily = [s for s in sources if once_a_day(s.kind)]
    others = [s for s in sources if not once_a_day(s.kind)]
    if not daily:
        return [], others
    try:
        async with session_factory() as session:
            keys = {(s.kind, s.url) for s in daily}
            ids = {
                (kind, url): sid
                for sid, kind, url in (
                    await session.execute(
                        select(Source.id, Source.kind, Source.url).where(
                            Source.kind.in_({k for k, _ in keys}),
                            Source.url.in_({u for _, u in keys}),
                        )
                    )
                ).all()
            }
            claimed = await claim_daily_fetches(
                session, [ids[k] for k in keys if k in ids], now=now
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - no database: no board or page is fetched
        return [(s.kind, s.name, "skipped", "", NO_DATABASE) for s in daily], others
    skipped, checked = [], []
    for s in daily:
        sid = ids.get((s.kind, s.url))
        if sid is None:
            skipped.append((s.kind, s.name, "skipped", "", NOT_SEEDED))
        elif sid in claimed:
            checked.append(s)
        else:
            skipped.append((s.kind, s.name, "skipped", "", ALREADY_FETCHED))
    return skipped, [*others, *checked]


async def run(kind: str | None) -> list[tuple]:
    data = read_seed()
    competitors = {c["slug"]: _competitor(c) for c in data.competitors}
    sources = [
        _build(s, competitors)
        for s in data.sources
        if s.enabled and (kind is None or s.kind == kind)
    ]
    skipped, sources = await daily_gate(sources, SessionLocal, now=datetime.now(UTC))
    sem = asyncio.Semaphore(CONCURRENCY)
    guard = await make_guard()
    async with PoliteClient(get_settings(), guard=guard) as client:
        return skipped + list(await asyncio.gather(*(_check(s, client, sem) for s in sources)))


def format_table(rows: list[tuple]) -> str:
    header = ("kind", "name", "status", "items", "error")
    table = [header, *rows]
    widths = [max(len(str(r[i])) for r in table) for i in range(4)]
    lines = []
    for r in table:
        cells = [str(r[i]).ljust(widths[i]) for i in range(4)] + [str(r[4])]
        lines.append(" | ".join(cells).rstrip())
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch every enabled seed source once.")
    parser.add_argument("--kind", default=None, help="only check sources of this kind (e.g. rss)")
    args = parser.parse_args()
    rows = sorted(asyncio.run(run(args.kind)), key=lambda r: (r[0], r[2] == "ok", r[1]))
    print(format_table(rows))
    ok = sum(1 for r in rows if r[2] in ("ok", "not-modified"))
    failed = sum(1 for r in rows if r[2] == "FAIL")
    print(f"\n{ok} ok, {failed} failed, {len(rows) - ok - failed} skipped")


if __name__ == "__main__":
    main()
