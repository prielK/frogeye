"""Page-change judge: a fast model decides whether a watched page's diff matters.

Per batch:

- Selection: snapshots of non-self competitors with `judged_at IS NULL`, a stored diff and
  not given up, the fewest failed days first, then oldest first. A diff whose added/removed
  content is under `settings.page_judge_min_diff_chars` (context lines and headers do not
  count) is closed without a model call (judged, not meaningful), so it is
  scanned once; the first snapshot of a page has no diff and is never judged. At most
  `limit` of the rest go to the model.
- One call per snapshot, `JUDGE_CONCURRENCY` at a time; the LLM-call cache key is the request, so
  the same diff on the same page is asked once. The answer is a strict
  `{meaningful, change_kind, summary}`. An `LLMError` or refusal leaves the snapshot
  unjudged, logged, with the failure recorded (`app.agents.retries`): it is tried
  again next run until its third failed day gives it up, and a diff that failed before
  queues behind every diff not yet asked, so one the model keeps refusing never takes the
  first slot day after day. A verdict clears its failures.
- The scraped diff reaches the model only through `untrusted()`, whole: the judge reads the
  stored diff up to its cap (`DIFF_CAP_CHARS`, 20,000, chosen over keeping an 8,000
  window: the fast tier reads a full one for about $0.0003), and a diff the store cut at that
  cap is marked "[…truncated]" so the judge knows the change goes on.
- A meaningful change of kind pricing, packaging or product becomes a Document (a raw item
  and a document row, linked to the competitor, with a unique `?frogeye_snapshot=` URL) that
  the daily pipeline triages like any article. New documents are embedded here, so `triage`
  sees them in the same run. The verdicts and documents are committed before, so an
  embedding failure (a budget stop or any other error) loses nothing: the batch
  reports it (`index_error`) and the documents wait for the next run's `index_new`.
- Short sessions: rows are read and closed before the first call, each verdict is
  written in its own short transaction. A budget stop is reported, never raised; snapshots
  not reached stay unjudged.
- no call is started once what is left of the day's (or the total) budget is
  under the newsroom's reserve (`reserve_reached`): the briefing keeps it. The diffs not
  started are counted in `reserved`, stay unjudged and record no failed day.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.retries import RetryColumns, cleared, eligible, record_failure
from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import TRUNCATED, load_prompt, untrusted
from app.agents.verified_llm import reserve_reached
from app.core.config import Settings
from app.intel.pages import DIFF_CAP_CHARS
from app.models import Competitor, Document, DocumentCompetitor, PageSnapshot, RawItem, Source
from app.models.enums import DocStatus, Scope
from app.processing.normalize import canonicalize_url, content_hash, url_hash
from app.rag.indexing import index_documents

logger = logging.getLogger(__name__)

TASK = "page_change_judge"
JUDGE_CONCURRENCY = 4
DEFAULT_LIMIT = 40  # snapshots judged per run when no limit is given
SUMMARY_CHARS = 300
DIFF_EXCERPT_CHARS = 3000  # of the diff kept in the document text
DIFF_PROMPT_CHARS = DIFF_CAP_CHARS  # the model reads the whole stored diff
DOCUMENT_KINDS = frozenset({"pricing", "packaging", "product"})

ChangeKind = Literal["pricing", "packaging", "product", "messaging", "company", "other"]
RETRIES = RetryColumns(
    PageSnapshot.judge_failures, PageSnapshot.judge_failed_at, PageSnapshot.judge_error
)


@dataclass(frozen=True)
class JudgeBatch:
    judged: int  # snapshots with a stored verdict
    meaningful: int  # of those, judged meaningful
    documents_created: int
    budget_stopped: bool
    index_error: str | None = None  # embedding the new documents failed (they wait)
    failed: int = 0  # left unjudged by an LLM error or refusal, tried again next run
    given_up: int = 0  # of the failed, those whose third failed day it was
    reserved: int = 0  # diffs not started, to keep the newsroom's reserve


class PageChangeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meaningful: bool
    change_kind: ChangeKind
    summary: str


def make_judge_model() -> type[PageChangeVerdict]:
    """The strict output schema."""
    return PageChangeVerdict


@dataclass(frozen=True)
class _Pick:
    snapshot_id: int
    source_id: int
    competitor_id: int
    competitor: str
    page: str
    url: str
    text_hash: str
    diff: str
    fetched_at: datetime


def prompt_diff(diff: str) -> str:
    """The diff as the judge reads it: whole, marked when the store cut it at its cap."""
    return diff + TRUNCATED if len(diff) >= DIFF_CAP_CHARS else diff


def changed_chars(diff: str) -> int:
    """Characters of added and removed line content in a unified diff: context lines,
    `@@` hunk headers and file headers do not count."""
    return sum(
        len(line) - 1
        for line in diff.splitlines()
        if line[:1] in ("+", "-") and not line.startswith(("+++ ", "--- "))
    )


async def _select(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, limit: int
) -> list[_Pick]:
    """The next `limit` diffs worth a model call, oldest first.

    Every unjudged diff of a non-self competitor is gate-checked here (only added and
    removed content counts). One that fails is closed without an LLM call: judged now,
    not meaningful, no kind or summary, so it is scanned once. Only ids and diffs are read for
    the scan, never the page text."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    PageSnapshot.id,
                    PageSnapshot.text_hash,
                    PageSnapshot.diff_from_prev,
                    PageSnapshot.fetched_at,
                    PageSnapshot.page_kind,
                    Source,
                    Competitor,
                )
                .join(Source, Source.id == PageSnapshot.source_id)
                .join(Competitor, Competitor.id == Source.competitor_id)
                .where(
                    PageSnapshot.judged_at.is_(None),
                    PageSnapshot.diff_from_prev.is_not(None),
                    eligible(RETRIES),
                    ~Competitor.is_self,
                )
                .order_by(PageSnapshot.judge_failures, PageSnapshot.id)
            )
        ).all()
        trivial = [r[0] for r in rows if changed_chars(r[2]) < settings.page_judge_min_diff_chars]
        if trivial:
            await session.execute(
                update(PageSnapshot)
                .where(PageSnapshot.id.in_(trivial), PageSnapshot.judged_at.is_(None))
                .values(judged_at=datetime.now(UTC), meaningful=False)
            )
            await session.commit()
        closed = set(trivial)
        return [
            _Pick(
                snapshot_id=sid,
                source_id=src.id,
                competitor_id=comp.id,
                competitor=comp.name,
                page=kind or str((src.config or {}).get("page") or "page"),
                url=str((src.config or {}).get("url") or src.url),
                text_hash=thash,
                diff=diff,
                fetched_at=fetched,
            )
            for sid, thash, diff, fetched, kind, src, comp in rows
            if sid not in closed
        ][:limit]


async def _store_document(session: AsyncSession, pick: _Pick, summary: str) -> int | None:
    """Add the raw item and document for a meaningful change; None if it is already filed."""
    url = canonicalize_url(f"{pick.url}?frogeye_snapshot={pick.text_hash[:12]}")
    uhash = url_hash(url)
    if (await session.execute(select(Document.id).where(Document.url_hash == uhash))).first():
        return None  # the same text came back, or a rerun
    title = f"{pick.competitor} {pick.page} page changed: {summary}"
    text = f"{summary}\n\n{pick.diff[:DIFF_EXCERPT_CHARS]}"
    raw_id = (
        await session.execute(
            pg_insert(RawItem)
            .values(
                source_id=pick.source_id,
                url=url,
                external_id=f"page:{pick.text_hash[:12]}",
                title=title,
                published_at=pick.fetched_at,
                content_hash=content_hash(title, text),
                payload={"page": pick.page, "snapshot_id": pick.snapshot_id},
                raw_text=text,
                normalized_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["source_id", "external_id"])
            .returning(RawItem.id)
        )
    ).scalar_one_or_none()
    if raw_id is None:
        return None
    doc = Document(
        raw_item_id=raw_id,
        source_id=pick.source_id,
        canonical_url=url,
        url_hash=uhash,
        title=title,
        published_at=pick.fetched_at,
        lang="en",
        summary=summary,
        clean_text=text,
        content_hash=content_hash(title, text),
        status=DocStatus.new,
        scope_hint=str(Scope.competitor),
        # The page is the competitor's own, so the prefilter's own-source rule is applied here.
        prefilter={"matched_terms": [pick.competitor], "scope_hint": str(Scope.competitor)},
    )
    session.add(doc)
    await session.flush()
    await session.execute(
        pg_insert(DocumentCompetitor)
        .values(document_id=doc.id, competitor_id=pick.competitor_id)
        .on_conflict_do_nothing()
    )
    return doc.id


async def judge_page_changes(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    settings: Settings,
    ctx: RunCtx,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> JudgeBatch:
    """Judge up to `limit` (default `DEFAULT_LIMIT`) unjudged page diffs, the fewest failed
    days first, then oldest first. `now` (default the clock) dates a failure's day."""
    moment = now or datetime.now(UTC)
    wanted = DEFAULT_LIMIT if limit is None else limit
    if wanted < 1:
        return JudgeBatch(0, 0, 0, False)
    picked = await _select(session_factory, settings, wanted)
    if not picked:
        return JudgeBatch(0, 0, 0, False)

    schema = make_judge_model()
    prompt = load_prompt(TASK)
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)
    counts = {
        "judged": 0,
        "meaningful": 0,
        "documents": 0,
        "failed": 0,
        "given_up": 0,
        "reserved": 0,
    }
    state = {"stopped": False}
    new_docs: list[int] = []

    async def one(pick: _Pick) -> None:
        async with sem:
            if await reserve_reached(llm, settings):  # the briefing keeps its reserve
                counts["reserved"] += 1
                return
            try:
                out = await llm.structured(
                    TASK,
                    "fast",
                    schema,
                    prompt=prompt,
                    variables={
                        "competitor": pick.competitor,
                        "page": pick.page,
                        "url": pick.url,
                        "diff": untrusted(
                            pick.text_hash[:12],
                            prompt_diff(pick.diff),
                            max_chars=DIFF_PROMPT_CHARS + len(TRUNCATED),
                        ),
                    },
                    ctx=ctx,
                )
            except BudgetExceeded:
                state["stopped"] = True
                return
            except (LLMError, LLMRefusal) as exc:
                logger.warning("page change %s left unjudged: %s", pick.snapshot_id, exc)
                counts["failed"] += 1
                async with session_factory() as session:
                    gave_up = await record_failure(
                        session,
                        PageSnapshot,
                        RETRIES,
                        PageSnapshot.id == pick.snapshot_id,
                        PageSnapshot.judged_at.is_(None),
                        reason=f"{type(exc).__name__}: {exc}",
                        now=moment,
                    )
                    await session.commit()
                if gave_up:
                    logger.warning(
                        "page change %s given up after its third failed day: %s",
                        pick.snapshot_id,
                        exc,
                    )
                counts["given_up"] += gave_up
                return
            summary = " ".join(out.summary.split())[:SUMMARY_CHARS]  # type: ignore[attr-defined]
            meaningful = bool(out.meaningful)  # type: ignore[attr-defined]
            kind = str(out.change_kind)  # type: ignore[attr-defined]
            async with session_factory() as session:
                snap = await session.get(PageSnapshot, pick.snapshot_id)
                if snap is None or snap.judged_at is not None:
                    return
                snap.meaningful, snap.change_kind = meaningful, kind
                snap.change_summary, snap.judged_at = summary, datetime.now(UTC)
                for column, value in cleared(RETRIES).items():
                    setattr(snap, column, value)
                doc_id = None
                if meaningful and kind in DOCUMENT_KINDS:
                    doc_id = await _store_document(session, pick, summary)
                await session.commit()
            counts["judged"] += 1
            counts["meaningful"] += int(meaningful)
            if doc_id is not None:
                counts["documents"] += 1
                new_docs.append(doc_id)

    results = await asyncio.gather(*(one(p) for p in picked), return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):  # a database error: log it, the rest of the batch stands
            logger.error("page changes: a snapshot failed: %r", res)
        elif isinstance(res, BaseException):
            raise res
    index_error = None
    if new_docs:
        try:
            await index_documents(session_factory, llm, sorted(new_docs), ctx=ctx)
        except BudgetExceeded:
            state["stopped"] = True  # the documents wait; index_new embeds them next run
        except Exception as exc:  # noqa: BLE001 - committed already: report, never lose them
            logger.exception("page changes: embedding %s new documents failed", len(new_docs))
            index_error = f"{type(exc).__name__}: {exc}"
    return JudgeBatch(
        counts["judged"],
        counts["meaningful"],
        counts["documents"],
        state["stopped"],
        index_error,
        counts["failed"],
        counts["given_up"],
        counts["reserved"],
    )
