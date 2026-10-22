"""Daily Intelligence graph: the whole pipeline, one checkpointed LangGraph run a day.

    ingest ─┬▶ index_new ▶ tag_postings ▶ judge_pages ▶ triage ▶ disambiguate ▶ apply_triage
            │      ▶ cluster ▶ analyze ▶ score ▶ deep_analyze ▶ extract_facts ▶ route
            │      ▶ newsroom ▶ publish
            └▶ publish (no OpenAI key: ingest only)

- ingest: collect + normalize + dedup + prefilter (`collect_and_prefilter`). Its failure
  fails the run; every other node logs its error into `errors` and the run goes on.
- index_new: embeds documents that lack an embedding: this run's prefilter-passed ones, the
  triage backlog and relevant documents still waiting for a story.
- tag_postings: the fast model labels open job postings that have no tags yet with capability
  slugs (`tag_postings_batch`, at most `posting_tag_cap`, newest first). It is independent of
  the news pipeline, so a failure or a budget stop there costs nothing else; the postings it
  did not reach stay untagged for the next run.
- judge_pages: the fast model judges each unjudged page-watch diff (pricing, about, careers
  pages) of at least `page_judge_min_diff_chars`; a meaningful pricing, packaging or product
  change becomes a Document, embedded there so `triage` reads it in this same run. A failure or
  a budget stop leaves the diffs unjudged for the next run.
- triage: fast model per document (`select_for_triage`), ≤ 8 in flight. A document whose
  call fails stays `new` for the next run.
- disambiguate: the smart tool agent re-judges flagged (unsure / ambiguous-alias) decisions.
- apply_triage: persists decisions (status, triage JSON, competitor links) in one commit.
- cluster: relevant documents join or open a story (signal). Leftovers of a stopped run
  (relevant, no story yet) are picked up too.
- analyze: the analyst per signal, ≤ 4 in flight: new ∪ re-analysis ∪ pending signals
  never analyzed (a budget stop's carry-over), highest provisional importance first (impact
  = novelty = 3), at most `analysis_cap`.
- score: deterministic importance for every touched signal and every recent one,
  then embeds auto/approved signals that have no retrieval row yet. Each signal is
  scored on its own (`_score_each`): one that fails is logged, recorded and skipped.
- deep_analyze: the stage-B analyst per signal (`analyze_signal_deep`: a full, cited,
  fact-checked analysis), ≤ 4 in flight, for the `deep_analysis_cap` most important signals
  that have no current analysis (`select_for_deep_analysis`); runs after score so importance
  is final, and before the newsroom so the briefing can read the analyses. The signals it
  analysed are re-scored before it ends (an analysis that rates no JFrog product
  lowers the score of that day's briefing), on a resume those stored before the crash too.
  It starts no signal once the day's remaining
  budget is under `newsroom_reserve_usd`, so it never costs the day its briefing.
  Resuming it continues where it stopped: current analyses are never selected again.
- extract_facts: the smart model reads keyword-screened relevant documents and about/careers
  page snapshots for company facts (funding, headcount, revenue, customers, market share,
  acquisitions, leadership, partners, offices, layoffs), at most `fact_extract_cap` sources,
  newest first (`extract_facts_batch`, app/intel/facts.py). Only facts whose verbatim quote,
  figures and date check out against the source are stored; a source is read once. Like
  stage B it starts nothing once the day's remaining budget is the newsroom's reserve.
  A failure or a budget stop leaves the unread sources for the next run.
- route: breaking alerts. newsroom: the Morning Briefing subgraph.
- publish: files the briefings, emails the editions, posts to Slack (nodes/publish.py).

State is JSON-plain (ids, counts, flags, error strings, the briefing as JSON);
the dependencies (session factory, LLM, HTTP factory, settings) live in this module's
closures and are rebuilt by the runner on resume, never checkpointed. Fan-out uses an
asyncio.Semaphore inside a node, not LangGraph `Send`: per-node concurrency caps and a small
checkpoint. Resuming re-runs only unfinished nodes, and the DB keeps those idempotent:
triage only sees `new` documents, cluster skips documents with a story, analyze skips
signals analyzed since the run started, deep_analyze skips signals whose analysis is
current. The newsroom subgraph has no checkpointer, so a
resumed newsroom node re-runs whole (cheap through the LLM-call cache).

Budget: BudgetExceeded in any node sets `budget_stopped`; later LLM work is
skipped, unprocessed documents stay `new`, signals stay pending for the next run and the
briefing is filed as `skipped_budget`. Stage B's reserve stop is not one: it only
defers the remaining analyses. Sessions are short: every node opens its own,
commits what it wrote and never holds one across an LLM await.
"""

import asyncio
import logging
import operator
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, TypedDict
from zoneinfo import ZoneInfo

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.analysis_store import needs_analysis
from app.agents.briefing_schema import BriefingContent
from app.agents.funnel import empty_funnel
from app.agents.graphs.newsroom import (
    FAILED_HEADLINE,
    FAILED_LEAD,
    SKIPPED_HEADLINE,
    SKIPPED_LEAD,
    NewsroomResult,
    run_newsroom,
)
from app.agents.nodes.analyst import analyze_and_verify
from app.agents.nodes.cluster import cluster_documents
from app.agents.nodes.disambiguate import disambiguate_one
from app.agents.nodes.page_changes import judge_page_changes
from app.agents.nodes.posting_tags import tag_postings_batch
from app.agents.nodes.publish import publish_briefings
from app.agents.nodes.route import ROUTABLE, deliver_alerts, mark_alerted, plan_alerts
from app.agents.nodes.score import score_signal
from app.agents.nodes.signal_analysis import analyze_signal_deep, select_for_deep_analysis
from app.agents.nodes.triage import (
    TriageDecision,
    apply_triage,
    revive_triage_out,
    select_for_triage,
    triage_one,
)
from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt
from app.agents.schemas import build_enums
from app.agents.scoring_config import BREAKING_THRESHOLD
from app.agents.tools.base import ToolDeps
from app.agents.verified_llm import reserve_reached
from app.core.config import Settings
from app.core.events import publish as publish_event
from app.core.http import PoliteClient
from app.intel.facts import extract_facts_batch
from app.models import CompanyFact, Document, PipelineRun
from app.models.enums import BriefingStatus, DocStatus, RetrievalKind, SignalStatus, StepKind
from app.models.gold import Signal, SignalAnalysis
from app.models.rag import RetrievalItem
from app.processing.ingest import collect_and_prefilter
from app.rag.indexing import index_documents, index_signal

logger = logging.getLogger(__name__)

GRAPH_NAME = "daily_intel"
NODES = (
    "ingest",
    "index_new",
    "tag_postings",
    "judge_pages",
    "triage",
    "disambiguate",
    "apply_triage",
    "cluster",
    "analyze",
    "score",
    "deep_analyze",
    "extract_facts",
    "route",
    "newsroom",
    "publish",
)
TRIAGE_CONCURRENCY = 8
DISAMBIGUATE_CONCURRENCY = 4
ANALYZE_CONCURRENCY = 4
DEEP_ANALYZE_CONCURRENCY = 4
SIGNAL_INDEX_LIMIT = 200  # catch-up per run
ERROR_CHARS = 300
# the agent behind each node's steps (the task names), so the trace and the cost
# table agree. Other nodes' agents are set where they run (story_judge, embed, newsroom).
NODE_AGENTS = {
    "tag_postings": "posting_tagger",
    "judge_pages": "page_change_judge",
    "triage": "triage",
    "disambiguate": "disambiguate",
    "analyze": "analyst",
    "deep_analyze": "signal_analyst",
    "extract_facts": "fact_extractor",
}
# the statuses whose importance is refreshed every run (recency decays daily).
RESCORED = (SignalStatus.auto, SignalStatus.approved, SignalStatus.needs_review)


def _merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {**(old or {}), **(new or {})}


class DailyState(TypedDict, total=False):
    # run ids
    pipeline_run_id: int
    agent_run_id: int
    run_started_at: str  # ISO; analyze skips signals analyzed since then (resume)
    llm_enabled: bool
    source_ids: list[int] | None
    # work lists
    doc_ids: list[int]  # prefilter-passed in this run
    triage_results: list[dict[str, Any]]  # {doc_id, out (TriageOut JSON), flag}
    relevant_doc_ids: list[int]
    new_signal_ids: list[int]
    grown_signal_ids: list[int]
    reanalyze_signal_ids: list[int]
    analyzed_signal_ids: list[int]
    touched_signal_ids: list[int]
    # results
    ingest_stats: dict[str, int]
    newsroom: dict[str, Any]  # the NewsroomResult as JSON
    briefing_date: str  # ISO local date
    briefing_ids: list[int]
    funnel: Annotated[dict[str, Any], _merge]
    budget_stopped: Annotated[bool, operator.or_]
    errors: Annotated[list[str], operator.add]


@dataclass(frozen=True)
class DailyDeps:
    """Per-process dependencies, captured by the node closures (never checkpointed)."""

    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    llm: AccountedLLM | None
    http_factory: Callable[[], PoliteClient]
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


Node = Callable[[DailyState], Awaitable[dict[str, Any]]]


def _error(name: str, exc: BaseException) -> str:
    return f"{name}: {type(exc).__name__}: {exc}"[:ERROR_CHARS]


def _ctx(state: DailyState, node: str) -> RunCtx:
    return RunCtx(agent_run_id=state.get("agent_run_id"), node=node, agent=NODE_AGENTS.get(node))


def _cutoff(deps: DailyDeps) -> datetime:
    return deps.now() - timedelta(days=deps.settings.analysis_lookback_days)


async def _score_each(
    session: AsyncSession, signal_ids: Iterable[int], *, now: datetime, node: str
) -> list[str]:
    """`score_signal` for each signal in the caller's transaction (deterministic, no LLM).
    One signal at a time under a savepoint, so a bad one (a malformed legacy breakdown, a
    database error) is rolled back alone, logged and skipped, and the others still commit:
    one old signal can no longer fail the node. A vanished signal is only a warning; any
    other failure also comes back as an error line for the run's notes."""
    errors = []
    for sid in signal_ids:
        try:
            async with session.begin_nested():
                await score_signal(session, sid, now=now)
        except LookupError:
            logger.warning("%s: signal %s vanished before its score", node, sid)
        except Exception as exc:  # noqa: BLE001 - skipped, retried by the next run's score
            logger.exception("%s: scoring signal %s failed; skipped", node, sid)
            errors.append(_error(f"{node} signal {sid}", exc))
    return errors


def _score_summary(node: str, errors: list[str]) -> list[str]:
    """A node's failed re-scores as ONE line of the run's notes (capped at
    NOTES_CHARS), how many and the first; `_score_each` logs every one by id."""
    if not errors:
        return []
    noun = "signal" if len(errors) == 1 else "signals"
    line = f"{node}: {len(errors)} {noun} not scored (skipped, scored next run); first: {errors[0]}"
    return [line[:ERROR_CHARS]]


async def _fan_out[T, R](
    items: Iterable[T], fn: Callable[[T], Awaitable[R]], limit: int
) -> tuple[list[tuple[T, R]], str | None, list[tuple[T, Exception]]]:
    """Run fn over items, ≤ limit at a time. Returns (results, budget scope hit, failures).

    Every item finishes (calls after a budget stop fail fast at the budget check, before any
    backend call), so completed work is never thrown away. Non-Exception BaseExceptions
    (cancellation, a dying worker) propagate.
    """
    items = list(items)
    sem = asyncio.Semaphore(limit)

    async def run(item: T) -> R:
        async with sem:
            return await fn(item)

    outcomes = await asyncio.gather(*(run(i) for i in items), return_exceptions=True)
    done: list[tuple[T, R]] = []
    failed: list[tuple[T, Exception]] = []
    budget: str | None = None
    for item, out in zip(items, outcomes, strict=True):
        if isinstance(out, BudgetExceeded):
            budget = budget or out.scope
        elif isinstance(out, Exception):
            failed.append((item, out))
        elif isinstance(out, BaseException):
            raise out
        else:
            done.append((item, out))
    return done, budget, failed


@dataclass
class DeepBatch:
    """What one batch of stage-B analyses did (the node and the backfill CLI share it)."""

    selected: int
    analyzed: int = 0
    failed: list[tuple[int, str]] = field(default_factory=list)  # (signal id, why): nothing stored
    crashed: list[tuple[int, Exception]] = field(default_factory=list)  # failed with an exception
    budget: str | None = None  # the budget scope that stopped the batch
    reserved: int = 0  # signals not started, to keep the newsroom's reserve
    stored_ids: list[int] = field(default_factory=list)  # analysed now (current, usable)
    score_errors: list[str] = field(default_factory=list)  # re-scores that failed (skipped)

    @property
    def skipped(self) -> int:
        """Selected signals never attempted: refused at the budget check or kept back for
        the briefing's reserve."""
        return self.selected - self.analyzed - len(self.failed) - len(self.crashed)

    def summary(self) -> str:
        return (
            f"deep analysis: {self.analyzed} analysed, {len(self.failed) + len(self.crashed)} "
            f"failed, {self.skipped} skipped"
        )

    def reserve_note(self) -> str | None:
        if not self.reserved:
            return None
        return (
            f"deep analysis: {self.reserved} not started, the rest of today's budget is "
            "kept for the briefing; they stay due for the next run"
        )


async def deep_analyze_batch(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    llm: AccountedLLM,
    ctx: RunCtx,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> DeepBatch:
    """Stage-B analysis of the signals due one (`select_for_deep_analysis`: at most `limit`,
    default `settings.deep_analysis_cap`), `DEEP_ANALYZE_CONCURRENCY` at a time. A signal
    that fails keeps its stage-A text; a budget stop is reported in `budget` and the work
    done before it stays stored. No signal is started once the day's remaining budget
    is under `settings.newsroom_reserve_usd` (counted in `reserved`, not a budget stop), so
    the briefing can still be written. The signals it analysed are re-scored
    (`_score_each`, at `now`) in one transaction before it returns, so route and the
    newsroom read importance that reflects the analysis; a re-score that fails is skipped
    (`score_errors`). The daily node and `app.cli.analyze_signals` both run this."""
    ids = await select_for_deep_analysis(session_factory, settings, limit=limit)
    batch = DeepBatch(selected=len(ids))
    if not ids:
        return batch
    async with session_factory() as session:
        enums = await build_enums(session)
    tools = ToolDeps(session_factory=session_factory, llm=llm, settings=settings, ctx=ctx)

    async def one(sid: int):
        if await reserve_reached(llm, settings):
            batch.reserved += 1
            return None
        return await analyze_signal_deep(sid, llm=llm, deps=tools, enums=enums, ctx=ctx)

    done, batch.budget, crashed = await _fan_out(ids, one, DEEP_ANALYZE_CONCURRENCY)
    # What counts is what is stored: a budget stop while re-indexing keeps the analysis.
    async with session_factory() as session:
        due = await needs_analysis(session, ids)
        batch.stored_ids = [sid for sid in ids if sid not in due]
        moment = now or datetime.now(UTC)  # deterministic, no LLM: one transaction
        batch.score_errors = await _score_each(
            session, batch.stored_ids, now=moment, node="deep_analyze"
        )
        await session.commit()
    batch.analyzed = len(batch.stored_ids)
    batch.failed = [
        (sid, outcome.error or "not stored")
        for sid, outcome in done
        if outcome is not None and not outcome.ok and sid in due
    ]
    batch.crashed = [(sid, exc) for sid, exc in crashed if sid in due]
    return batch


async def rank_for_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    signal_ids: list[int],
    *,
    now: datetime | None = None,
) -> list[int]:
    """Signal ids by provisional importance (impact = novelty = 3), highest first.

    Uses the real scoring formula (`score_signal`, flush-only) inside a session that is
    rolled back, so nothing provisional is ever persisted. Missing signals are dropped.
    """
    now = now or datetime.now(UTC)
    scored: list[tuple[int, int]] = []
    async with session_factory() as session:
        for sid in signal_ids:
            sig = await session.get(Signal, sid)
            if sig is None:
                continue
            sig.impact_score, sig.novelty_score = 3, 3
            scored.append((await score_signal(session, sid, now=now), sid))
        await session.rollback()
    return [sid for _, sid in sorted(scored, key=lambda t: (-t[0], t[1]))]


def _newsroom_json(result: NewsroomResult) -> dict[str, Any]:
    return {
        "status": str(result.status),
        "master": result.master.model_dump(mode="json"),
        "editions": {r: c.model_dump(mode="json") for r, c in result.editions.items()},
        "signal_ids": list(result.signal_ids),
        "model": result.model,
        "prompt_version": result.prompt_version,
        "cost_usd": result.cost_usd,
    }


def _newsroom_result(data: dict[str, Any]) -> NewsroomResult:
    return NewsroomResult(
        master=BriefingContent.model_validate(data["master"]),
        editions={r: BriefingContent.model_validate(c) for r, c in data["editions"].items()},
        signal_ids=list(data["signal_ids"]),
        status=data["status"],
        model=data.get("model"),
        prompt_version=data.get("prompt_version"),
        cost_usd=float(data.get("cost_usd") or 0.0),
    )


def _placeholder_result(
    settings: Settings, status: BriefingStatus, headline: str, lead: str
) -> NewsroomResult:
    """The placeholder briefing (budget stop or failure): one master row, no editions."""
    master = BriefingContent(
        headline=headline,
        lead=lead,
        top_takeaways=[],
        sections=[],
        quiet_competitors=[],
        watchlist=[],
    )
    return NewsroomResult(
        master=master,
        editions={},
        signal_ids=[],
        status=status,
        model=settings.model_smart,
        prompt_version=load_prompt("editor").version,
    )


def build_daily_graph(deps: DailyDeps | None, *, checkpointer: BaseCheckpointSaver | None = None):
    """Compile the daily graph over `deps` (None only to draw it: no node may run then)."""

    def llm_of() -> AccountedLLM:
        assert deps is not None and deps.llm is not None
        return deps.llm

    async def note(state: DailyState, node: str, summary: str, **args: Any) -> None:
        if deps is not None and deps.llm is not None:
            await deps.llm.log_step(
                _ctx(state, node), kind=StepKind.node, args=args or None, result_summary=summary
            )

    # ---- nodes ---------------------------------------------------------------------------

    def ingest_funnel(stats: dict[str, int]) -> dict[str, int]:
        return {k: stats.get(k, 0) for k in ("fetched", "documents_created", "prefilter_passed")}

    async def ingest(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        stats: dict[str, int] = {}
        try:
            async with deps.http_factory() as http:
                outcome = await collect_and_prefilter(
                    deps.session_factory,
                    http,
                    run_id=state["pipeline_run_id"],
                    source_ids=state.get("source_ids"),
                    stats=stats,
                )
        except Exception as exc:
            # The run fails (the runner re-raises); keep what was counted, like run_ingestion.
            funnel = _merge(state.get("funnel", {}), ingest_funnel(stats))
            async with deps.session_factory() as session:
                run = await session.get(PipelineRun, state["pipeline_run_id"])
                run.stats = {**stats, **funnel, "errors": [_error("ingest", exc)]}
                await session.commit()
            raise
        return {
            "doc_ids": outcome.doc_ids,
            "ingest_stats": outcome.stats,
            "funnel": ingest_funnel(outcome.stats),
        }

    async def index_new(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        sf, settings = deps.session_factory, deps.settings
        async with sf() as session:
            triage_ids = await select_for_triage(session, settings)
            when = func.coalesce(Document.published_at, Document.created_at)
            waiting = and_(
                Document.status == DocStatus.relevant,
                Document.story_id.is_(None),
                when >= _cutoff(deps),
            )
            wanted = sorted(set(state.get("doc_ids", [])) | set(triage_ids))
            ids = list(
                (
                    await session.execute(
                        select(Document.id)
                        .where(
                            Document.embedding.is_(None),
                            or_(Document.id.in_(wanted), waiting),
                        )
                        .order_by(Document.id)
                    )
                )
                .scalars()
                .all()
            )
        if ids:
            await index_documents(sf, llm_of(), ids, ctx=_ctx(state, "index_new"))
        return {}

    async def tag_postings(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        ctx = _ctx(state, "tag_postings")
        batch = await tag_postings_batch(deps.session_factory, llm_of(), deps.settings, ctx)
        await note(
            state,
            "tag_postings",
            f"posting tags: {batch.tagged} tagged, {batch.failed} failed",
        )
        if batch.reserved:  # not a budget stop, the briefing goes on
            await note(
                state,
                "tag_postings",
                f"posting tags: {batch.reserved} not started, the rest of today's budget is "
                "the briefing's reserve",
                reserved=batch.reserved,
            )
        update: dict[str, Any] = {"funnel": {"postings_tagged": batch.tagged}}
        if batch.failed:
            given_up = f"; {batch.given_up} given up" if batch.given_up else ""
            update["errors"] = [f"tag_postings: {batch.failed} postings left untagged{given_up}"]
        if batch.budget_stopped:
            await note(state, "tag_postings", "budget stop")
            update["budget_stopped"] = True
        return update

    async def judge_pages(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        ctx = _ctx(state, "judge_pages")
        batch = await judge_page_changes(deps.session_factory, llm_of(), deps.settings, ctx)
        await note(
            state,
            "judge_pages",
            f"page changes: {batch.judged} judged, {batch.meaningful} meaningful, "
            f"{batch.documents_created} documents",
        )
        if batch.reserved:  # not a budget stop, the briefing goes on
            noun = "diff" if batch.reserved == 1 else "diffs"
            await note(
                state,
                "judge_pages",
                f"page changes: {batch.reserved} {noun} not started, the rest of today's budget "
                "is the briefing's reserve",
                reserved=batch.reserved,
            )
        update: dict[str, Any] = {"funnel": {"page_changes": batch.documents_created}}
        if batch.index_error is not None:  # recorded, the documents wait for index_new
            noun = "document" if batch.documents_created == 1 else "documents"
            line = (
                f"judge_pages: indexing {batch.documents_created} new {noun} failed, "
                f"embedded next run: {batch.index_error}"
            )
            update["errors"] = [line[:ERROR_CHARS]]
        if batch.failed:  # bounded like the tagger's and the extractor's
            given_up = f" ({batch.given_up} given up)" if batch.given_up else ""
            noun = "diff" if batch.failed == 1 else "diffs"
            update.setdefault("errors", []).append(
                f"judge_pages: {batch.failed} {noun} left unjudged, tried again next run{given_up}"
            )
        if batch.budget_stopped:
            await note(state, "judge_pages", "budget stop")
            update["budget_stopped"] = True
        return update

    async def triage(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        sf = deps.session_factory
        async with sf() as session:
            ids = await select_for_triage(session, deps.settings)
            enums = await build_enums(session)
        ctx = _ctx(state, "triage")

        async def one(doc_id: int) -> TriageDecision | None:
            return await triage_one(doc_id, llm=llm_of(), session_factory=sf, enums=enums, ctx=ctx)

        done, budget, failed = await _fan_out(ids, one, TRIAGE_CONCURRENCY)
        results = [
            {
                "doc_id": d.doc_id,
                "out": d.out.model_dump(mode="json"),
                "flag": d.needs_disambiguation,
            }
            for _, d in done
            if d is not None
        ]
        update: dict[str, Any] = {
            "triage_results": results,
            "funnel": {"triaged": len(results)},
            "errors": [_error(f"triage doc {i}", e) for i, e in failed],
        }
        if budget:
            await note(state, "triage", f"budget: {budget}", triaged=len(results))
            update["budget_stopped"] = True
        return update

    async def disambiguate(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        results = list(state.get("triage_results", []))
        flagged = [r for r in results if r["flag"]]
        if not flagged:
            return {}
        sf = deps.session_factory
        async with sf() as session:
            enums = await build_enums(session)
        ctx = _ctx(state, "disambiguate")
        tools = ToolDeps(session_factory=sf, llm=llm_of(), settings=deps.settings, ctx=ctx)

        async def one(r: dict[str, Any]) -> Any:
            decision = TriageDecision(r["doc_id"], revive_triage_out(enums, r["out"]), True)
            return await disambiguate_one(decision, llm=llm_of(), deps=tools, enums=enums, ctx=ctx)

        done, budget, failed = await _fan_out(flagged, one, DISAMBIGUATE_CONCURRENCY)
        judged = {r["doc_id"]: out.model_dump(mode="json") for r, out in done}
        kept = []
        for r in results:
            if not r["flag"]:
                kept.append(r)
            elif r["doc_id"] in judged:
                kept.append({**r, "out": judged[r["doc_id"]], "flag": False})
            elif not budget:  # an unexpected failure: keep the triage-level decision
                kept.append({**r, "flag": False})
            # else: budget stop before its judgement -> the document stays `new`
        update: dict[str, Any] = {
            "triage_results": kept,
            "errors": [_error(f"disambiguate doc {r['doc_id']}", e) for r, e in failed],
        }
        if budget:
            await note(state, "disambiguate", f"budget: {budget}")
            update["budget_stopped"] = True
        return update

    async def apply_triage_node(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        # Still flagged = never judged (disambiguate clears the flag on every result it
        # decides, and is skipped after a budget stop): those documents stay `new`.
        results = [r for r in state.get("triage_results", []) if not r["flag"]]
        relevant: list[int] = []
        if results:
            async with deps.session_factory() as session:  # no LLM here: one transaction
                enums = await build_enums(session)
                for r in results:
                    # an older checkpoint's scope "jfrog" revives as not relevant
                    out = revive_triage_out(enums, r["out"])
                    await apply_triage(session, r["doc_id"], out)
                    if out.relevant:
                        relevant.append(r["doc_id"])
                await session.commit()
        return {"relevant_doc_ids": relevant, "funnel": {"relevant": len(relevant)}}

    async def cluster(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        sf = deps.session_factory
        async with sf() as session:
            when = func.coalesce(Document.published_at, Document.created_at)
            waiting = (
                (
                    await session.execute(
                        select(Document.id).where(
                            Document.status == DocStatus.relevant,
                            Document.story_id.is_(None),
                            Document.embedding.is_not(None),
                            when >= _cutoff(deps),
                        )
                    )
                )
                .scalars()
                .all()
            )
        ids = sorted(set(state.get("relevant_doc_ids", [])) | set(waiting))
        result = await cluster_documents(sf, llm_of(), ids, ctx=_ctx(state, "cluster"))
        return {
            "new_signal_ids": result.new_signal_ids,
            "grown_signal_ids": result.grown_signal_ids,
            "reanalyze_signal_ids": result.reanalyze_signal_ids,
            "funnel": {"signals_new": len(result.new_signal_ids)},
        }

    async def analyze(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        sf, settings = deps.session_factory, deps.settings
        started = datetime.fromisoformat(state["run_started_at"])
        async with sf() as session:
            carried = (
                (
                    await session.execute(
                        select(Signal.id).where(
                            Signal.status == SignalStatus.pending,
                            Signal.analyzed_at.is_(None),
                            Signal.first_seen_at >= _cutoff(deps),
                        )
                    )
                )
                .scalars()
                .all()
            )
            candidates = list(
                dict.fromkeys(
                    [
                        *state.get("new_signal_ids", []),
                        *state.get("reanalyze_signal_ids", []),
                        *sorted(carried),
                    ]
                )
            )
            # Resume: signals this run already analyzed are done.
            already = set(
                (
                    await session.execute(
                        select(Signal.id).where(
                            Signal.id.in_(candidates), Signal.analyzed_at >= started
                        )
                    )
                )
                .scalars()
                .all()
            )
            enums = await build_enums(session)
        todo = [sid for sid in candidates if sid not in already]
        ranked = await rank_for_analysis(sf, todo, now=deps.now())
        if len(ranked) > settings.analysis_cap:
            logger.info("analysis capped: %s of %s signals", settings.analysis_cap, len(ranked))
        ranked = ranked[: settings.analysis_cap]
        ctx = _ctx(state, "analyze")
        tools = ToolDeps(session_factory=sf, llm=llm_of(), settings=settings, ctx=ctx)

        async def one(sid: int):
            return await analyze_and_verify(sid, llm=llm_of(), deps=tools, enums=enums, ctx=ctx)

        done, budget, failed = await _fan_out(ranked, one, ANALYZE_CONCURRENCY)
        # Analyzed = persisted since the run started (also counts a signal whose analysis was
        # saved before a budget stop hit its indexing, and those done before a resume).
        async with sf() as session:
            persisted = set(
                (
                    await session.execute(
                        select(Signal.id).where(
                            Signal.id.in_(candidates), Signal.analyzed_at >= started
                        )
                    )
                )
                .scalars()
                .all()
            )
        errored = {sid for sid, outcome in done if outcome.status == SignalStatus.needs_review}
        analyzed = [sid for sid in candidates if sid in persisted or sid in errored]
        update: dict[str, Any] = {
            "analyzed_signal_ids": analyzed,
            "funnel": {"signals_analyzed": len(analyzed)},
            "errors": [_error(f"analyze signal {sid}", e) for sid, e in failed],
        }
        if budget:
            await note(state, "analyze", f"budget: {budget}", analyzed=len(analyzed))
            update["budget_stopped"] = True
        return update

    async def score(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        sf = deps.session_factory
        touched = list(
            dict.fromkeys(
                [
                    *state.get("new_signal_ids", []),
                    *state.get("grown_signal_ids", []),
                    *state.get("reanalyze_signal_ids", []),
                    *state.get("analyzed_signal_ids", []),
                ]
            )
        )
        now = deps.now()
        async with sf() as session:  # deterministic, no LLM: one transaction
            scored = await _score_each(session, touched, now=now, node="score")
            # recency decays every day, so every recent live or reviewable signal is
            # re-scored (no LLM): the Explorer's order and "Why ranked here" stay current.
            # They are not routed: decay only ever lowers a score. One that fails to score
            # is skipped, not the node.
            recent = (
                await session.execute(
                    select(Signal.id)
                    .where(
                        Signal.status.in_(RESCORED),
                        Signal.first_seen_at >= _cutoff(deps),
                        Signal.id.not_in(touched),
                    )
                    .order_by(Signal.id)
                )
            ).scalars()
            scored += await _score_each(session, list(recent), now=now, node="score")
            await session.commit()
        update: dict[str, Any] = {
            "touched_signal_ids": touched,
            "errors": _score_summary("score", scored),
        }
        if state.get("budget_stopped") or deps.llm is None:
            return update
        # auto/approved signals whose retrieval row never got written (an indexing
        # error after analysis) are embedded now.
        async with sf() as session:
            has_row = exists().where(
                RetrievalItem.kind == RetrievalKind.signal, RetrievalItem.ref_id == Signal.id
            )
            missing = (
                (
                    await session.execute(
                        select(Signal.id)
                        .where(Signal.status.in_(ROUTABLE), ~has_row)
                        .order_by(Signal.id)
                        .limit(SIGNAL_INDEX_LIMIT)
                    )
                )
                .scalars()
                .all()
            )
        ctx = _ctx(state, "score")
        errors = list(update["errors"])
        for sid in missing:
            try:
                await index_signal(sf, llm_of(), sid, ctx=ctx)
            except BudgetExceeded:
                await note(state, "score", "budget: signal indexing stopped")
                return {**update, "budget_stopped": True, "errors": errors}
            except Exception as exc:  # noqa: BLE001 - retried next run
                logger.warning("indexing signal %s failed: %s", sid, exc)
                errors.append(_error(f"index signal {sid}", exc))
        return {**update, "errors": errors}

    async def deep_analyze(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        sf = deps.session_factory
        started = datetime.fromisoformat(state["run_started_at"])
        ctx = _ctx(state, "deep_analyze")
        batch = await deep_analyze_batch(sf, deps.settings, llm_of(), ctx, now=deps.now())
        # Counted from the table: the signals this run analysed, also before a resume.
        # Those stored before a crash were never re-scored and are no longer selected, so
        # they are re-scored here: the relevance multiplier still reaches that day's briefing.
        async with sf() as session:
            written = list(
                await session.scalars(
                    select(SignalAnalysis.signal_id)
                    .where(SignalAnalysis.created_at >= started)
                    .distinct()
                )
            )
            missed = sorted(set(written) - set(batch.stored_ids))
            scored = await _score_each(session, missed, now=deps.now(), node="deep_analyze")
            await session.commit()
        await note(state, "deep_analyze", batch.summary(), selected=batch.selected)
        if reserve := batch.reserve_note():  # not a budget stop, the briefing goes on
            await note(state, "deep_analyze", reserve, reserved=batch.reserved)
        update: dict[str, Any] = {
            "funnel": {"signals_deep_analyzed": len(written)},
            "errors": [
                *(_error(f"deep_analyze signal {sid}", e) for sid, e in batch.crashed),
                *_score_summary("deep_analyze", [*batch.score_errors, *scored]),
            ],
        }
        if batch.failed:  # soft failures keep stage A; one line says how many and why
            sid, why = batch.failed[0]
            first = f"deep_analyze: {len(batch.failed)} not stored (first {sid}: {why})"
            update["errors"].append(first[:ERROR_CHARS])
        if batch.budget:
            await note(state, "deep_analyze", f"budget: {batch.budget}")
            update["budget_stopped"] = True
        return update

    async def extract_facts(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        sf = deps.session_factory
        started = datetime.fromisoformat(state["run_started_at"])
        ctx = _ctx(state, "extract_facts")
        batch = await extract_facts_batch(sf, llm_of(), deps.settings, ctx)
        # Counted from the table, as deep_analyze: the facts this run stored, also before a
        # resume; a resumed node never re-reads a source it closed.
        async with sf() as session:
            stored = await session.scalar(
                select(func.count())
                .select_from(CompanyFact)
                .where(CompanyFact.extracted_at >= started)
            )
        await note(state, "extract_facts", batch.summary())
        if reserve := batch.reserve_note():  # not a budget stop, the briefing goes on
            await note(state, "extract_facts", reserve, reserved=batch.reserved)
        update: dict[str, Any] = {"funnel": {"facts_extracted": int(stored or 0)}}
        if batch.failed:
            given_up = f" ({batch.given_up} given up)" if batch.given_up else ""
            update["errors"] = [
                f"extract_facts: {batch.failed} failed, tried again next run{given_up}"
            ]
        if batch.budget_stopped:
            await note(state, "extract_facts", "budget stop")
            update["budget_stopped"] = True
        return update

    async def route(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        touched = state.get("touched_signal_ids", [])
        if not touched:
            return {"funnel": {"signals_breaking": 0}}
        sf = deps.session_factory
        async with sf() as session:  # read, then close: delivery is network I/O
            alerts = await plan_alerts(session, touched)
        delivered = await deliver_alerts(alerts, deps.settings)
        async with sf() as session:
            await mark_alerted(session, delivered)
            await session.commit()
            breaking = await session.scalar(
                select(func.count())
                .select_from(Signal)
                .where(
                    Signal.id.in_(touched),
                    Signal.status.in_(ROUTABLE),
                    Signal.importance >= BREAKING_THRESHOLD,
                )
            )
        return {"funnel": {"signals_breaking": int(breaking or 0)}}

    async def newsroom(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        sf, settings = deps.session_factory, deps.settings
        now = deps.now()
        day: date = now.astimezone(ZoneInfo(settings.timezone)).date()  # local date
        errors: list[str] = []
        if state.get("budget_stopped"):
            result = _placeholder_result(
                settings, BriefingStatus.skipped_budget, SKIPPED_HEADLINE, SKIPPED_LEAD
            )
        else:
            try:
                async with sf() as session:
                    enums = await build_enums(session)
                ctx = _ctx(state, "newsroom")  # the briefing cost is this node's steps
                tools = ToolDeps(session_factory=sf, llm=llm_of(), settings=settings, ctx=ctx)
                result = await run_newsroom(
                    sf, llm_of(), tools, enums, settings=settings, now=now, ctx=ctx
                )
            except BudgetExceeded as exc:  # run_newsroom handles its own; belt and braces
                await note(state, "newsroom", f"budget: {exc.scope}")
                result = _placeholder_result(
                    settings, BriefingStatus.skipped_budget, SKIPPED_HEADLINE, SKIPPED_LEAD
                )
            except Exception as exc:  # not an LLM failure (the newsroom handles those itself)
                logger.exception("daily run: the newsroom crashed")
                await note(state, "newsroom", f"error: {type(exc).__name__}: {exc}")
                errors.append(_error("newsroom", exc))
                result = _placeholder_result(
                    settings, BriefingStatus.failed, FAILED_HEADLINE, FAILED_LEAD
                )
        update: dict[str, Any] = {
            "newsroom": _newsroom_json(result),
            "briefing_date": day.isoformat(),
            "funnel": {"briefing_status": str(result.status)},
            "errors": errors + [f"newsroom: {e}"[:ERROR_CHARS] for e in result.errors],
        }
        if result.status == BriefingStatus.skipped_budget:
            update["budget_stopped"] = True
        return update

    async def publish(state: DailyState) -> dict[str, Any]:
        assert deps is not None
        data = state.get("newsroom")
        if not data:
            return {}
        funnel = dict(state.get("funnel", {}))
        outcome = await publish_briefings(
            deps.session_factory,
            deps.settings,
            _newsroom_result(data),
            day=date.fromisoformat(state["briefing_date"]),
            agent_run_id=state.get("agent_run_id"),
            funnel=funnel,
        )
        return {
            "briefing_ids": outcome.briefing_ids,
            "errors": [f"publish: {e}"[:ERROR_CHARS] for e in outcome.errors],
        }

    # ---- wrapping: budget skips, error capture, progress events --------------------------

    async def record_progress(state: DailyState, stats: dict[str, Any]) -> None:
        """The run record carries the funnel so far, written before the stage is
        announced, so polling the run and the live event agree (the runner writes the
        final counts again when the run finishes). Best effort: never fails the run."""
        if deps is None or state.get("pipeline_run_id") is None:
            return
        try:
            async with deps.session_factory() as session:
                run = await session.get(PipelineRun, state["pipeline_run_id"])
                if run is not None:
                    run.stats = stats
                    await session.commit()
        except Exception:  # noqa: BLE001 - progress bookkeeping only
            logger.warning("daily run: could not record progress", exc_info=True)

    llm_nodes = {
        "index_new",
        "tag_postings",
        "judge_pages",
        "triage",
        "disambiguate",
        "cluster",
        "analyze",
        "deep_analyze",
        "extract_facts",
    }

    def wrap(name: str, fn: Node, *, fatal: bool = False) -> Node:
        async def node(state: DailyState) -> dict[str, Any]:
            skip = name in llm_nodes and (
                state.get("budget_stopped") or deps is None or deps.llm is None
            )
            update: dict[str, Any] = {}
            if not skip:
                try:
                    update = await fn(state)
                except BudgetExceeded as exc:
                    logger.warning("daily run: %s stopped by the %s budget", name, exc.scope)
                    await note(state, name, f"budget: {exc.scope}")
                    update = {"budget_stopped": True}
                except Exception as exc:
                    if fatal:
                        raise
                    logger.exception("daily run: node %s failed", name)
                    await note(state, name, f"error: {type(exc).__name__}: {exc}")
                    update = {"errors": [_error(name, exc)]}
            counts = _merge(state.get("funnel", {}), update.get("funnel", {}))
            ingest_stats = update.get("ingest_stats") or state.get("ingest_stats") or {}
            await record_progress(state, {**ingest_stats, **counts})
            await publish_event(
                "run.progress",
                {
                    "pipeline_run_id": state.get("pipeline_run_id"),
                    "agent_run_id": state.get("agent_run_id"),
                    "stage": name,
                    "counts": counts,
                },
            )
            return update

        node.__name__ = name
        return node

    fns: dict[str, Node] = {
        "ingest": ingest,
        "index_new": index_new,
        "tag_postings": tag_postings,
        "judge_pages": judge_pages,
        "triage": triage,
        "disambiguate": disambiguate,
        "apply_triage": apply_triage_node,
        "cluster": cluster,
        "analyze": analyze,
        "score": score,
        "deep_analyze": deep_analyze,
        "extract_facts": extract_facts,
        "route": route,
        "newsroom": newsroom,
        "publish": publish,
    }
    g = StateGraph(DailyState)
    for name in NODES:
        g.add_node(name, wrap(name, fns[name], fatal=name == "ingest"))
    g.add_edge(START, "ingest")
    g.add_conditional_edges(
        "ingest",
        lambda s: "index_new" if s.get("llm_enabled") else "publish",
        ["index_new", "publish"],
    )
    for a, b in zip(NODES[1:-1], NODES[2:], strict=True):
        g.add_edge(a, b)
    g.add_edge("publish", END)
    return g.compile(name=GRAPH_NAME, checkpointer=checkpointer)


def initial_state(
    *,
    pipeline_run_id: int,
    agent_run_id: int,
    run_started_at: datetime,
    llm_enabled: bool,
    source_ids: list[int] | None,
) -> DailyState:
    return {
        "pipeline_run_id": pipeline_run_id,
        "agent_run_id": agent_run_id,
        "run_started_at": run_started_at.isoformat(),
        "llm_enabled": llm_enabled,
        "source_ids": list(source_ids) if source_ids else None,
        "funnel": empty_funnel(),
        "budget_stopped": False,
        "errors": [],
    }
