"""One idempotent, concurrency-safe ingestion run: collect -> normalize -> dedup -> prefilter.

`collect_and_prefilter` is the work itself; `run_ingestion` wraps it in the advisory lock and
the `pipeline_runs` bookkeeping. The daily intelligence graph (agents/runner.py) takes the
same lock and runs `collect_and_prefilter` as its first node.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.collectors import get_collector
from app.collectors.base import FetchResult
from app.collectors.daily_guard import ALREADY_FETCHED, claim_daily_fetches, once_a_day
from app.collectors.jobs.store import PostingSync, sync_postings
from app.collectors.store import mark_source_result, store_metrics, store_raw_items
from app.core.config import get_settings
from app.core.host_guard import DbHostGuard
from app.core.http import PoliteClient
from app.intel.pages import store_page_snapshot
from app.models import Document, PipelineRun, Source
from app.models.agents import AgentRun
from app.models.enums import AgentRunStatus, DocStatus, RunStatus
from app.processing.dedup import mark_duplicates
from app.processing.normalize import normalize_new_raw_items
from app.processing.prefilter import AliasMatcher, prefilter_documents

logger = logging.getLogger(__name__)

INGEST_LOCK_KEY = 42_001
COLLECT_CONCURRENCY = 8
ERROR_CHARS = 500
# Collectors get `since` a little before the last fetch: feeds publish late and clocks drift,
# and re-seeing an item is free (raw items are unique per source + external_id).
SINCE_LOOKBACK = timedelta(days=2)
INTERRUPTED_ERROR = "interrupted (worker stopped before finishing)"
# Every run kind that takes the lock; one holding it means any other "running" row is stale.
LOCKED_RUN_KINDS = ("ingestion", "daily")
DAILY_GRAPH = "daily_intel"

# pg_advisory_lock(bigint) appears in pg_locks with the key's high 32 bits in classid, its low
# 32 bits in objid and objsubid = 1 (the two-int4-key form uses objsubid = 2).
_LOCK_HELD = text(
    "select exists (select 1 from pg_locks"
    " where locktype = 'advisory' and granted and objsubid = 1"
    " and database = (select oid from pg_database where datname = current_database())"
    " and classid::bigint = :classid and objid::bigint = :objid)"
)


class RunAlreadyActive(Exception):
    """Another ingestion run holds the advisory lock."""


@dataclass
class IngestOutcome:
    stats: dict[str, int]
    doc_ids: list[int]  # documents that passed the prefilter in this run (still status new)


async def ingestion_lock_held(session: AsyncSession) -> bool:
    """Whether any Postgres session holds the ingestion lock, i.e. a run is really in progress.

    Reads pg_locks instead of trying the lock: a probe that briefly took it could make a
    worker that is just starting a run see it as taken and skip the run.
    """
    held = await session.scalar(
        _LOCK_HELD, {"classid": INGEST_LOCK_KEY >> 32, "objid": INGEST_LOCK_KEY & 0xFFFFFFFF}
    )
    return bool(held)


async def acquire_run_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[AsyncConnection, bool]:
    """Take the session-level advisory lock on a dedicated connection.

    Production: session_factory is bound to an engine, so we open a separate AUTOCOMMIT
    connection that lives for the whole run (no open transaction, so it never sits "idle in
    transaction" holding back vacuum). Tests: it is bound to one shared connection, which is
    used directly (advisory locks are per Postgres session; a different connection still
    conflicts). Returns (connection, owned) where owned means we must close it.
    """
    bind = session_factory.kw["bind"]
    if isinstance(bind, AsyncEngine):
        conn, owned = await bind.execution_options(isolation_level="AUTOCOMMIT").connect(), True
    else:
        conn, owned = bind, False
    try:
        got = (
            await conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": INGEST_LOCK_KEY})
        ).scalar_one()
    except BaseException:
        if owned:
            await conn.close()
        raise
    if not got:
        if owned:
            await conn.close()
        raise RunAlreadyActive("an ingestion run is already active")
    return conn, owned


async def release_run_lock(conn: AsyncConnection, owned: bool) -> None:
    """Best-effort unlock. If it fails, the connection is invalidated rather than returned to
    the pool: a pooled connection still holding the lock would make every later run skip."""
    released = False
    try:
        await conn.execute(text("select pg_advisory_unlock(:k)"), {"k": INGEST_LOCK_KEY})
        released = True
    except Exception:  # noqa: BLE001 - must not mask the run's own outcome
        logger.exception("releasing the ingestion lock failed; discarding its connection")
    finally:
        if owned:
            try:
                if not released:
                    # closes the DB session, which releases the lock server-side
                    await conn.invalidate()
            except Exception:  # noqa: BLE001
                logger.exception("invalidating the ingestion lock connection failed")
            finally:
                await conn.close()


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:ERROR_CHARS]


async def _collect_one(
    source: Source,
    session_factory: async_sessionmaker[AsyncSession],
    http: PoliteClient,
    sem: asyncio.Semaphore,
    db_lock: asyncio.Lock,
    counters: dict[str, int],
) -> None:
    result: FetchResult | None = None
    error: str | None = None
    try:
        collector = get_collector(source.kind)
    except KeyError:
        logger.warning("no collector for source %s (%s); skipped", source.name, source.kind)
        return
    counters["sources_attempted"] += 1
    since = source.last_fetched_at - SINCE_LOOKBACK if source.last_fetched_at else None
    async with sem:
        try:
            result = await collector.fetch(source, since, http)
        except Exception as e:  # noqa: BLE001 - a failing source must not abort the run
            logger.warning("source %s failed: %s", source.name, e)
            error = _err(e)

    # DB work is serialized (cheap, and avoids interleaved transactions on a shared connection);
    # the slow network fetches above stay concurrent. Each source commits in its own session.
    async with db_lock:
        if result is not None:
            try:
                async with session_factory() as session:
                    src = await session.get(Source, source.id)
                    new = await store_raw_items(session, src, result.items)
                    written = 0
                    if result.metrics:
                        if src.competitor_id is None:
                            logger.info("source %s has metrics but no competitor", src.name)
                        else:
                            written = await store_metrics(
                                session, src.competitor_id, result.metrics
                            )
                    synced: PostingSync | None = None
                    # an empty but complete listing is a board with no open roles: it closes
                    if result.postings or result.postings_complete:
                        if src.competitor_id is None:
                            logger.info("source %s has postings but no competitor", src.name)
                        else:
                            synced = await sync_postings(
                                session,
                                src,
                                result.postings,
                                complete=result.postings_complete,
                                now=datetime.now(UTC),
                            )
                    if result.page_text is not None:
                        if src.competitor_id is None:
                            logger.info("source %s has page text but no competitor", src.name)
                        else:
                            await store_page_snapshot(
                                session, src, result.page_text, now=datetime.now(UTC)
                            )
                    await mark_source_result(session, src, ok=True, result=result)
                    await session.commit()
                counters["fetched"] += len(result.items)
                counters["new_raw"] += new
                counters["metrics_written"] += written
                if synced is not None:
                    counters["postings_new"] += synced.new
                    counters["postings_closed"] += synced.closed
                return
            except Exception as e:  # noqa: BLE001
                logger.exception("storing results of source %s failed", source.name)
                error = _err(e)
        counters["sources_failed"] += 1
        async with session_factory() as session:
            src = await session.get(Source, source.id)
            await mark_source_result(session, src, ok=False, error=error)
            await session.commit()


async def _collect(
    session_factory: async_sessionmaker[AsyncSession],
    http: PoliteClient,
    source_ids: list[int] | None,
    *,
    allow_refetch_today: bool = False,
) -> dict[str, int]:
    """Fetch the run's sources: every enabled one, or those listed (a listed discovery
    placeholder, `config.missing`, names no board and is skipped). A job board or
    watched page already attempted today is skipped too, and counted in
    `sources_skipped_today` (`app.collectors.daily_guard`): its claim is committed
    before any request is sent."""
    counters = dict.fromkeys(
        (
            "sources_attempted",
            "sources_failed",
            "sources_skipped_today",
            "fetched",
            "new_raw",
            "metrics_written",
            "postings_new",
            "postings_closed",
        ),
        0,
    )
    async with session_factory() as session:
        query = select(Source).order_by(Source.id)
        if source_ids:
            query = query.where(Source.id.in_(source_ids))
        else:
            query = query.where(Source.enabled)
        sources = [
            s
            for s in (await session.execute(query)).scalars().all()
            if not (s.config or {}).get("missing")
        ]
        daily = [s.id for s in sources if once_a_day(s.kind)]
        claimed = await claim_daily_fetches(
            session, daily, now=datetime.now(UTC), allow_refetch_today=allow_refetch_today
        )
        await session.commit()
    if allow_refetch_today and daily:
        logger.warning(
            "--allow-refetch-today: %s job boards and watched pages are fetched again today, "
            "whether or not a run already read them (an operator's one-off; never scheduled)",
            len(claimed),
        )
    skipped = [s for s in sources if once_a_day(s.kind) and s.id not in claimed]
    for s in skipped:
        logger.info("source %s (%s) skipped: %s", s.name, s.kind, ALREADY_FETCHED)
    counters["sources_skipped_today"] = len(skipped)
    fetching = [s for s in sources if not once_a_day(s.kind) or s.id in claimed]
    sem = asyncio.Semaphore(COLLECT_CONCURRENCY)
    db_lock = asyncio.Lock()
    await asyncio.gather(
        *(_collect_one(s, session_factory, http, sem, db_lock, counters) for s in fetching)
    )
    return counters


async def _process(
    session_factory: async_sessionmaker[AsyncSession], http: PoliteClient
) -> tuple[dict[str, int], list[int]]:
    """Normalize, dedup and prefilter; returns (stats, ids of the documents that passed)."""
    async with session_factory() as session:
        norm = await normalize_new_raw_items(session, http)
        await session.commit()
        # Sweep: documents that were never screened (a run that stopped mid-processing, or
        # ones reset by migration 0003) go through dedup + prefilter with this run's new ones.
        pending = set(
            (
                await session.execute(
                    select(Document.id).where(
                        Document.status == DocStatus.new, Document.prefilter.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        new_ids = set(norm.document_ids)
        doc_ids = sorted(new_ids | pending)
        duplicates = await mark_duplicates(session, doc_ids)
        await session.commit()
        fresh = list(
            (
                await session.execute(
                    select(Document.id)
                    .where(Document.id.in_(doc_ids), Document.status == DocStatus.new)
                    .order_by(Document.id)
                )
            )
            .scalars()
            .all()
        )
        matcher = await AliasMatcher.from_db(session)
        pre = await prefilter_documents(session, fresh, matcher)
        await session.commit()
        passed = list(
            (
                await session.execute(
                    select(Document.id)
                    .where(
                        Document.id.in_(fresh),
                        Document.status == DocStatus.new,
                        Document.prefilter.is_not(None),
                    )
                    .order_by(Document.id)
                )
            )
            .scalars()
            .all()
        )
    stats = {
        "documents_created": norm.created,
        "reprocessed": len(pending - new_ids),
        "duplicates": duplicates,
        "prefilter_passed": pre.passed,
        "prefilter_rejected": pre.rejected,
    }
    return stats, passed


async def collect_and_prefilter(
    session_factory: async_sessionmaker[AsyncSession],
    http: PoliteClient,
    *,
    run_id: int,
    source_ids: list[int] | None = None,
    stats: dict[str, int] | None = None,
    allow_refetch_today: bool = False,
) -> IngestOutcome:
    """Collect every source, then normalize, dedup and prefilter: a run's work without the
    lock or the run bookkeeping (callers own both). `run_id` only labels the logs.

    `stats`, when given, is filled in place as each step finishes, so a caller still has the
    collect counters if a later step raises. `allow_refetch_today` lifts the once-a-day rule
    for job boards and watched pages: only `app.cli.run_ingestion
    --allow-refetch-today` passes it, never the scheduler.
    """
    stats = {} if stats is None else stats
    stats.update(
        await _collect(session_factory, http, source_ids, allow_refetch_today=allow_refetch_today)
    )
    processed, doc_ids = await _process(session_factory, http)
    stats.update(processed)
    logger.info(
        "run %s: fetched %s, %s documents passed the prefilter",
        run_id,
        stats.get("fetched", 0),
        len(doc_ids),
    )
    return IngestOutcome(stats=stats, doc_ids=doc_ids)


async def mark_interrupted_runs(session: AsyncSession) -> None:
    """Call holding the lock: any other "running" row then belongs to a worker that stopped
    mid-run (its lock died with its DB session). Pipeline runs of every lock-taking kind are
    marked failed and daily agent runs interrupted (a manual `--resume` can pick those up).
    Flush-only: the caller commits."""
    now = datetime.now(UTC)
    stale = (
        (
            await session.execute(
                update(PipelineRun)
                .where(
                    PipelineRun.kind.in_(LOCKED_RUN_KINDS),
                    PipelineRun.status == RunStatus.running.value,
                )
                .values(status=RunStatus.failed.value, error=INTERRUPTED_ERROR, finished_at=now)
                .returning(PipelineRun.id)
            )
        )
        .scalars()
        .all()
    )
    if stale:
        logger.warning("marked interrupted runs %s as failed", list(stale))
    agents = (
        (
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.graph == DAILY_GRAPH,
                    AgentRun.status == AgentRunStatus.running.value,
                )
                .values(
                    status=AgentRunStatus.interrupted.value,
                    error=INTERRUPTED_ERROR,
                    finished_at=now,
                )
                .returning(AgentRun.id)
            )
        )
        .scalars()
        .all()
    )
    if agents:
        logger.warning("marked agent runs %s as interrupted", list(agents))
    await session.flush()


async def _start_run(session_factory: async_sessionmaker[AsyncSession], trigger: str) -> int:
    """Record the run (holding the lock), after marking any stale running rows."""
    async with session_factory() as session:
        await mark_interrupted_runs(session)
        run = PipelineRun(kind="ingestion", trigger=trigger, status=RunStatus.running.value)
        session.add(run)
        await session.commit()
        return run.id


async def run_ingestion(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trigger: str = "schedule",
    source_ids: list[int] | None = None,
    http_factory: Callable[[], PoliteClient] | None = None,
    allow_refetch_today: bool = False,
) -> int:
    """One locked, recorded ingestion run; returns its pipeline run id. See
    `collect_and_prefilter` for `allow_refetch_today` (the operator's flag only)."""
    conn, owned = await acquire_run_lock(session_factory)
    try:
        run_id = await _start_run(session_factory, trigger)

        stats: dict[str, int] = {}
        status, error = RunStatus.succeeded, None
        exc: Exception | None = None
        try:
            # one shared client so per-host locks/intervals apply across concurrent sources
            http = (
                http_factory()
                if http_factory
                else PoliteClient(get_settings(), guard=DbHostGuard(session_factory))
            )
            async with http:
                await collect_and_prefilter(
                    session_factory,
                    http,
                    run_id=run_id,
                    source_ids=source_ids,
                    stats=stats,
                    allow_refetch_today=allow_refetch_today,
                )
        except Exception as e:  # noqa: BLE001 - recorded on the run, then re-raised
            logger.exception("ingestion run %s failed", run_id)
            status, error, exc = RunStatus.failed, _err(e), e

        async with session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            run.status = status.value
            run.stats = stats
            run.error = error
            run.finished_at = datetime.now(UTC)
            await session.commit()
        if exc is not None:
            raise exc
        return run_id
    finally:
        await release_run_lock(conn, owned)
