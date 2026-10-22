"""Find each competitor's public job board and record it as a `job_board` source.

Usage: python -m app.cli.discover_job_boards [--competitor SLUG] [--dry-run]

For every competitor except the self one that has no job_board source yet, it asks
Greenhouse, Lever and then Ashby for at most 4 candidate tokens each: the slug, the slug
without dashes, the name squeezed to letters and digits, and any `job_board_hints` from
competitors.yaml. That is one request per (competitor, ATS, candidate), through PoliteClient
(pacing, the persistent breaker, a generic UA). It stops at the first board that lists at
least one posting naming the company (its name or an alias).

- A found board becomes an enabled source, `config = {"ats", "token"}`, and the probe's
  listing is stored as its first snapshot. The probe is that day's one request for the
  board (`last_attempt_at`, app.collectors.daily_guard): the same day's runs skip it.
- A definite miss (404/410, a non-board answer, an empty board, or one that never names the
  company) is cached for 30 days in one disabled placeholder source per competitor
  (`discovery://job_board/<slug>`, `config = {"missing": true, "checked": {...}}`). Errors
  that may pass (5xx, timeouts, a paused host) are not cached.
- `--dry-run` still asks (cached misses excepted) but writes nothing.

Never part of the daily graph. Prints one JSON line
`{"checked": n, "found": [{"competitor", "ats", "token", "open"}], "cached_misses": n,
"not_recorded": [{"competitor", "ats", "token", "open", "reason"}]}`: `found` lists the boards
recorded (on a dry run, the boards it would record); a board whose URL already belongs to
another source is not recorded for the competitor and is listed under `not_recorded` instead. Exit 2
for an unknown competitor.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.base import CollectorError, PostingIn
from app.collectors.jobs.collector import ADAPTERS, fetch_board
from app.collectors.jobs.common import BoardMissing, BoardPage
from app.collectors.jobs.store import sync_postings
from app.collectors.store import mark_source_result
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.core.host_guard import DbHostGuard
from app.core.http import FetchError, HostPaused, PoliteClient
from app.core.logging import configure_logging
from app.models import Competitor, Source
from app.models.enums import SourceKind
from app.seed.loader import SEED_DIR, read_seed

logger = logging.getLogger(__name__)

ATS_ORDER = ("greenhouse", "lever", "ashby")
MAX_CANDIDATES = 4
MISS_TTL = timedelta(days=30)
MISS_URL = "discovery://job_board/{slug}"


@dataclass
class Discovery:
    checked: int = 0  # probe requests sent
    found: list[dict] = field(default_factory=list)  # recorded (a dry run: would be)
    cached_misses: int = 0  # candidates skipped because a miss is cached
    not_recorded: list[dict] = field(default_factory=list)  # found, but the insert conflicted

    def as_json(self) -> dict:
        return {
            "checked": self.checked,
            "found": self.found,
            "cached_misses": self.cached_misses,
            "not_recorded": self.not_recorded,
        }


@dataclass
class _Target:
    id: int
    slug: str
    name: str
    terms: list[str]
    checked: dict[str, str]  # "<ats>:<token>" -> when the miss was seen (ISO)
    miss_id: int | None


def candidate_tokens(slug: str, name: str, hints: Sequence[str] = ()) -> list[str]:
    """At most MAX_CANDIDATES distinct lower-case tokens, most likely first."""
    out: list[str] = []
    squeezed = re.sub(r"[^a-z0-9]", "", name.lower())
    for token in (slug, slug.replace("-", ""), squeezed, *hints):
        token = token.strip().lower()
        if token and token not in out:
            out.append(token)
    return out[:MAX_CANDIDATES]


def load_hints(seed_dir=SEED_DIR) -> dict[str, list[str]]:
    """competitors.yaml `job_board_hints`, by slug."""
    return {
        c["slug"]: [str(h) for h in c.get("job_board_hints") or []]
        for c in read_seed(seed_dir).competitors
    }


def _names_company(terms: Sequence[str], postings: Sequence[PostingIn]) -> bool:
    """Whether any posting's title or description names the company. URLs do not count:
    they contain the very token being probed."""
    words = [t for t in terms if t and t.strip()]
    if not words:
        return False
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(w.strip()) for w in words) + r")(?!\w)",
        re.IGNORECASE,
    )
    return any(pattern.search(f"{p.title}\n{p.description_text or ''}") for p in postings)


def _fresh(seen: str | None, now: datetime) -> bool:
    if not seen:
        return False
    try:
        when = datetime.fromisoformat(seen)
    except ValueError:
        return False
    return now - when < MISS_TTL


async def _targets(session: AsyncSession, slug: str | None) -> list[_Target]:
    query = select(Competitor).order_by(Competitor.tier, Competitor.slug)
    if slug is not None:
        query = query.where(Competitor.slug == slug)
    competitors = list((await session.scalars(query)).all())
    if slug is not None and not competitors:
        raise LookupError(f"no competitor with slug {slug!r}")
    boards = (
        await session.scalars(select(Source).where(Source.kind == SourceKind.job_board))
    ).all()
    has_board = {s.competitor_id for s in boards if not (s.config or {}).get("missing")}
    misses = {s.competitor_id: s for s in boards if (s.config or {}).get("missing")}
    targets = []
    for c in competitors:
        if c.is_self:
            logger.info("%s is the self competitor: no job board discovery", c.slug)
            continue
        if c.id in has_board:
            continue
        miss = misses.get(c.id)
        targets.append(
            _Target(
                id=c.id,
                slug=c.slug,
                name=c.name,
                terms=[c.name, *c.aliases],
                checked=dict((miss.config or {}).get("checked") or {}) if miss else {},
                miss_id=miss.id if miss else None,
            )
        )
    return targets


async def _probe(
    target: _Target, http: PoliteClient, out: Discovery, *, now: datetime, hints: Sequence[str]
) -> tuple[tuple[str, str, BoardPage] | None, dict[str, str]]:
    """Ask the candidates in order until a board is found; returns it and the new misses."""
    misses: dict[str, str] = {}
    for ats in ATS_ORDER:
        for token in candidate_tokens(target.slug, target.name, hints):
            key = f"{ats}:{token}"
            if _fresh(target.checked.get(key), now):
                out.cached_misses += 1
                continue
            try:
                page = await fetch_board(ats, token, http)
            except HostPaused as exc:  # no request was sent; ask on a later run
                logger.warning("%s: %s", key, exc)
                continue
            except BoardMissing:
                out.checked += 1
                misses[key] = now.isoformat()
                continue
            except (CollectorError, FetchError) as exc:  # may pass: not cached
                out.checked += 1
                logger.warning("%s for %s failed: %s", key, target.slug, exc)
                continue
            out.checked += 1
            if not page.postings or not _names_company(target.terms, page.postings):
                reason = "is empty" if not page.postings else "never names the company"
                logger.info("%s for %s %s: not counted", key, target.slug, reason)
                misses[key] = now.isoformat()
                continue
            return (ats, token, page), misses
    return None, misses


async def _record_board(
    session: AsyncSession, target: _Target, ats: str, token: str, page: BoardPage, now: datetime
) -> bool:
    adapter = ADAPTERS[ats]
    stmt = (
        pg_insert(Source)
        .values(
            kind=SourceKind.job_board.value,
            name=f"{target.name} · {adapter.label} jobs",
            url=adapter.url(token),
            competitor_id=target.id,
            reliability="A",
            enabled=True,
            config={"ats": ats, "token": token},
        )
        .on_conflict_do_nothing(index_elements=["kind", "url"])
        .returning(Source.id)
    )
    source_id = await session.scalar(stmt)
    if source_id is None:
        logger.warning("%s:%s is already a source; not recorded for %s", ats, token, target.slug)
        return False
    source = await session.get_one(Source, source_id)
    source.last_attempt_at = now  # the probe was today's request for this board
    await sync_postings(session, source, page.postings, complete=page.complete, now=now)
    await mark_source_result(session, source, ok=True)
    if target.miss_id is not None:
        await session.execute(delete(Source).where(Source.id == target.miss_id))
    return True


async def _record_misses(session: AsyncSession, target: _Target, misses: dict[str, str]) -> None:
    checked = {**target.checked, **misses}
    miss = await session.get(Source, target.miss_id) if target.miss_id is not None else None
    if miss is not None:
        miss.config = {**(miss.config or {}), "missing": True, "checked": checked}
        return
    session.add(
        Source(
            kind=SourceKind.job_board.value,
            name=f"{target.name} · job board (none found)",
            url=MISS_URL.format(slug=target.slug),
            competitor_id=target.id,
            reliability="A",
            enabled=False,
            config={"missing": True, "checked": checked},
        )
    )


async def discover(
    session_factory: async_sessionmaker[AsyncSession],
    http: PoliteClient,
    *,
    now: datetime,
    slug: str | None = None,
    dry_run: bool = False,
    hints: Mapping[str, Sequence[str]] | None = None,
) -> Discovery:
    """One bounded discovery pass (see the module docstring). No session spans a request."""
    hints = {} if hints is None else hints
    async with session_factory() as session:
        targets = await _targets(session, slug)
    out = Discovery()
    for target in targets:
        hit, misses = await _probe(target, http, out, now=now, hints=hints.get(target.slug, ()))
        entry: dict | None = None
        if hit is not None:
            ats, token, page = hit
            entry = {
                "competitor": target.slug,
                "ats": ats,
                "token": token,
                "open": len(page.postings),
            }
        if dry_run:
            if entry is not None:
                out.found.append(entry)
            continue
        if hit is None and not misses:
            continue
        recorded = False
        async with session_factory() as session:
            if hit is not None:
                recorded = await _record_board(session, target, *hit, now=now)
            else:
                await _record_misses(session, target, misses)
            await session.commit()
        if entry is not None:  # a conflicted insert recorded nothing
            if recorded:
                out.found.append(entry)
            else:
                out.not_recorded.append({**entry, "reason": "already a source"})
    return out


async def _run(slug: str | None, dry_run: bool) -> dict:
    try:
        async with PoliteClient(get_settings(), guard=DbHostGuard(SessionLocal)) as http:
            result = await discover(
                SessionLocal,
                http,
                now=datetime.now(UTC),
                slug=slug,
                dry_run=dry_run,
                hints=load_hints(),
            )
        return result.as_json()
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.discover_job_boards",
        description="Find competitors' public job boards (Greenhouse, Lever, Ashby).",
    )
    parser.add_argument("--competitor", metavar="SLUG", default=None, help="only this one")
    parser.add_argument("--dry-run", action="store_true", help="ask, but record nothing")
    args = parser.parse_args(argv)
    configure_logging()
    try:
        result = asyncio.run(_run(args.competitor, args.dry_run))
    except LookupError as exc:
        print(f"nothing checked: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
