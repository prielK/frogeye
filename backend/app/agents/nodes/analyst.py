"""Analyst node: a smart model with read-only tools turns a pending story into a
Fact -> Impact -> Action item, then deterministic verification checks it (one retry).

Sessions are short: load, close, run the agent, verify, persist, then index. No
session is open across an LLM await and no caller's session is committed.
"""

import dataclasses
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.agents.tools  # noqa: F401  (registers the built-in tools)
from app.agents.nodes.verify import Verification, verify_extraction
from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt, untrusted
from app.agents.runtime.tool_agent import run_tool_agent
from app.agents.schemas import RunEnums, make_extracted_signal_model
from app.agents.tools.base import ToolDeps, get_tools
from app.agents.tools.signals import SimilarSignalsArgs, get_similar_signals
from app.models import Capability, Competitor, Document, DocumentCompetitor, Kit, Source
from app.models.enums import SignalStatus, StepKind
from app.models.gold import (
    Evidence,
    Signal,
    SignalCapability,
    SignalCompetitor,
    SignalDocument,
    SignalKit,
)
from app.rag.indexing import index_signal
from app.rag.kb import load_jfrog_context

logger = logging.getLogger(__name__)

TASK = "analyst"
TOOLS = ["get_jfrog_context", "get_similar_signals", "get_kit"]
MAX_TOOL_CALLS = 4
MAX_DOCS = 4
DOC_WORDS = 1500
MAX_ATTEMPTS = 2
MAX_QUOTES = 3
PRIOR_K = 5
REVIEWABLE = {SignalStatus.pending, SignalStatus.auto, SignalStatus.needs_review}
DOC_CHARS = 20_000  # headroom over 1,500 words so `untrusted` never cuts a doc mid-sentence


@dataclass
class AnalysisOutcome:
    signal_id: int
    ok: bool
    attempts: int
    status: str
    error: str | None = None


class SignalNotFound(LookupError):
    pass


@dataclass
class _Members:
    docs: list[dict[str, Any]]  # shown documents, in prompt order
    competitors: list[Competitor]  # union over ALL member documents
    capabilities: list[tuple[str, str]]  # (slug, name) for the whole run
    kits: list[tuple[str, str]]  # (slug, question)


def _order_key(row: Any) -> tuple[Any, ...]:
    published = row.published_at.timestamp() if row.published_at else float("-inf")
    # primary first, then reliability A..F, then newest first
    return (not row.is_primary, row.reliability, -published, row.id)


async def _load_members(
    session_factory: async_sessionmaker[AsyncSession], signal_id: int
) -> _Members | None:
    async with session_factory() as session:
        if await session.get(Signal, signal_id) is None:
            return None
        rows = (
            await session.execute(
                select(
                    Document.id,
                    Document.title,
                    Document.published_at,
                    Document.clean_text,
                    Document.summary,
                    SignalDocument.is_primary,
                    Source.name.label("source"),
                    Source.reliability,
                )
                .join(SignalDocument, SignalDocument.document_id == Document.id)
                .join(Source, Source.id == Document.source_id)
                .where(SignalDocument.signal_id == signal_id)
            )
        ).all()
        comps = list(
            (
                await session.execute(
                    select(Competitor)
                    .join(DocumentCompetitor, DocumentCompetitor.competitor_id == Competitor.id)
                    .join(
                        SignalDocument,
                        SignalDocument.document_id == DocumentCompetitor.document_id,
                    )
                    .where(SignalDocument.signal_id == signal_id, ~Competitor.is_self)
                    .distinct()
                    .order_by(Competitor.tier, Competitor.slug)
                )
            ).scalars()
        )
        caps = [
            (r.slug, r.name)
            for r in (
                await session.execute(
                    select(Capability.slug, Capability.name).order_by(Capability.slug)
                )
            ).all()
        ]
        kits = [
            (r.slug, r.question)
            for r in (
                await session.execute(select(Kit.slug, Kit.question).order_by(Kit.slug))
            ).all()
        ]
    shown = sorted(rows, key=_order_key)[:MAX_DOCS]
    docs = [
        {
            "id": r.id,
            "source": r.source,
            "grade": r.reliability,
            "date": r.published_at.date().isoformat() if r.published_at else "unknown",
            "title": r.title or "(untitled)",
            "text": " ".join((r.clean_text or r.summary or "").split()[:DOC_WORDS]),
        }
        for r in shown
    ]
    return _Members(docs, comps, caps, kits)


def _doc_view(d: dict[str, Any]) -> str:
    header = (
        f"Document {d['id']} (source: {d['source']}, grade {d['grade']}, published {d['date']})"
    )
    body = f"Title: {d['title']}\n\n{d['text']}"
    return f"{header}\n{untrusted(d['id'], body, max_chars=DOC_CHARS)}"


def _competitor_lines(comps: list[Competitor]) -> str:
    if not comps:
        return "none identified"
    return "\n".join(f"- {c.name} (tier {c.tier}, relationship: {c.relationship})" for c in comps)


def _menu(items: list[tuple[str, str]]) -> str:
    return "\n".join(f"- {slug}: {text}" for slug, text in items)


async def _prior_signals(signal_id: int, deps: ToolDeps, ctx: RunCtx, llm: AccountedLLM) -> str:
    """Deterministic top-5 similar signals (the function behind get_similar_signals)."""
    deps = deps if deps.ctx is not None else dataclasses.replace(deps, ctx=ctx)
    try:
        result = await get_similar_signals(SimilarSignalsArgs(signal_id=signal_id, k=PRIOR_K), deps)
    except BudgetExceeded:
        raise
    except Exception as exc:  # a lookup problem must not sink the analysis
        logger.warning("prior signals lookup failed for %s: %s", signal_id, exc)
        return "none available"
    await llm.log_step(
        ctx,
        kind=StepKind.tool,
        tool_name="get_similar_signals",
        args={"signal_id": signal_id, "k": PRIOR_K, "prefetch": True},
        result_summary=result.content,
    )
    # Titles of other signals derive from scraped text: apply the same delimiters as documents.
    return untrusted("prior", result.content if result.data else "none found")


async def analyze_signal(
    signal_id: int,
    *,
    llm: AccountedLLM,
    deps: ToolDeps,
    enums: RunEnums,
    ctx: RunCtx,
    feedback: str | None = None,
) -> BaseModel:
    """One analyst attempt: returns the run's dynamic `ExtractedSignal`.

    Raises LookupError for a missing signal, LLMRefusal/LLMError for a failed attempt and
    BudgetExceeded when a budget is hit.
    """
    members = await _load_members(deps.session_factory, signal_id)  # session closed here
    if members is None:
        raise SignalNotFound(f"signal {signal_id} not found")
    schema = make_extracted_signal_model(enums, tuple(d["id"] for d in members.docs))
    prior = await _prior_signals(signal_id, deps, ctx, llm)
    return await run_tool_agent(
        llm,
        task=TASK,
        tier="smart",
        prompt=load_prompt(TASK),
        variables={
            "jfrog_context": load_jfrog_context(),
            "signal_id": signal_id,
            "capabilities": _menu(members.capabilities),
            "kits": _menu(members.kits),
            "prior": prior,
            "competitors": _competitor_lines(members.competitors),
            "docs": [_doc_view(d) for d in members.docs],
            "feedback": feedback or "",
        },
        tools=get_tools(TOOLS),
        output_schema=schema,
        max_tool_calls=MAX_TOOL_CALLS,
        deps=deps,
        ctx=ctx,
    )


async def _verify(
    session_factory: async_sessionmaker[AsyncSession], signal_id: int, out: Any
) -> Verification:
    async with session_factory() as session:
        return await verify_extraction(session, signal_id, out)


async def analyze_and_verify(
    signal_id: int,
    *,
    llm: AccountedLLM,
    deps: ToolDeps,
    enums: RunEnums,
    ctx: RunCtx,
) -> AnalysisOutcome:
    """Analyze, verify, retry once with feedback, persist the final attempt, index."""
    session_factory = deps.session_factory
    out: Any = None
    verdict: Verification | None = None
    error: str | None = None
    feedback: str | None = None
    attempts = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        try:
            candidate = await analyze_signal(
                signal_id, llm=llm, deps=deps, enums=enums, ctx=ctx, feedback=feedback
            )
        except SignalNotFound as exc:
            return AnalysisOutcome(signal_id, False, attempt - 1, "missing", str(exc))
        except (LLMRefusal, LLMError) as exc:  # includes schema-invalid output, e.g. unknown doc_id
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("analyst attempt %s failed for signal %s: %s", attempt, signal_id, exc)
            await llm.log_step(
                ctx,
                kind=StepKind.node,
                args={"signal_id": signal_id, "attempt": attempt},
                result_summary=f"error: {error}",
            )
            feedback = (  # the error can quote the model's output: it travels as data
                "Your previous attempt produced no valid answer; the error is below, as data.\n"
                f"{untrusted('error', error, max_chars=1000)}\nAnswer again; cite quotes only "
                "from the document ids shown, using exactly those ids."
            )
            continue
        out, error = candidate, None  # a later error below re-sets `error` (the mixed path)
        verdict = await _verify(session_factory, signal_id, out)
        await llm.log_step(
            ctx,
            kind=StepKind.node,
            args={"signal_id": signal_id, "attempt": attempt},
            result_summary="verified" if verdict.ok else "; ".join(verdict.failed),
        )
        if verdict.ok:
            break
        feedback = verdict.feedback

    if out is None:
        await _persist_error(session_factory, signal_id, error or "analysis failed")
        return AnalysisOutcome(signal_id, False, attempts, SignalStatus.needs_review, error)

    assert verdict is not None
    status = SignalStatus.auto if verdict.ok else SignalStatus.needs_review
    await _persist(
        session_factory,
        signal_id,
        out,
        verdict,
        status,
        error=None if verdict.ok else error,
        agent_run_id=ctx.agent_run_id,
    )
    try:
        await index_signal(session_factory, llm, signal_id, ctx=ctx)
    except (LLMRefusal, LLMError) as exc:  # BudgetExceeded propagates
        logger.warning("indexing signal %s failed: %s", signal_id, exc)
        await llm.log_step(
            ctx,
            kind=StepKind.node,
            args={"signal_id": signal_id},
            result_summary=f"index error: {exc}",
        )
    return AnalysisOutcome(signal_id, verdict.ok, attempts, status, None if verdict.ok else error)


async def _persist_error(
    session_factory: async_sessionmaker[AsyncSession], signal_id: int, error: str
) -> None:
    async with session_factory() as session:
        signal = await session.get(Signal, signal_id)
        if signal is None:
            return
        if signal.status in REVIEWABLE:  # keep human-decided and terminal states
            signal.status = SignalStatus.needs_review
        signal.score_breakdown = {**(signal.score_breakdown or {}), "error": error}
        await session.commit()


async def _ids(session: AsyncSession, model: Any, slugs: list[str]) -> list[int]:
    if not slugs:
        return []
    rows = await session.execute(select(model.id).where(model.slug.in_(slugs)))
    return sorted(set(rows.scalars()))


async def _replace_links(
    session: AsyncSession, link: Any, column: str, signal_id: int, ids: list[int]
) -> None:
    await session.execute(delete(link).where(link.signal_id == signal_id))
    if ids:
        await session.execute(
            insert(link).values([{"signal_id": signal_id, column: i} for i in ids])
        )


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    signal_id: int,
    out: Any,
    verdict: Verification,
    status: str,
    *,
    error: str | None = None,
    agent_run_id: int | None = None,
) -> None:
    data = out.model_dump(mode="json")
    async with session_factory() as session:
        signal = await session.get(Signal, signal_id)
        if signal is None:
            return
        signal.title = data["title"]
        signal.fact = data["fact"]
        signal.event_date = date.fromisoformat(data["event_date"]) if data["event_date"] else None
        signal.signal_type = data["signal_type"]
        signal.stance = data["stance"]
        signal.impact = data["impact"]
        signal.impact_score = data["impact_score"]
        signal.novelty_score = data["novelty_score"]
        signal.role_actions = data["role_actions"]
        signal.hedged = data["hedged"]
        if signal.status in REVIEWABLE:  # never resurrect an approved/rejected/suppressed item
            signal.status = status
        elif not verdict.ok and signal.status == SignalStatus.approved:
            signal.status = SignalStatus.needs_review  # a failed re-analysis reopens review
        if agent_run_id is not None:
            signal.agent_run_id = agent_run_id
        signal.analyzed_at = datetime.now(UTC)
        signal.extraction_version = load_prompt(TASK).version
        breakdown = dict(signal.score_breakdown or {})
        breakdown.pop("error", None)  # a good final attempt clears an earlier attempt's error
        breakdown.pop("verification", None)
        if not verdict.ok:
            breakdown["verification"] = verdict.failed[:5]
            if error:
                breakdown["error"] = error
        signal.score_breakdown = breakdown

        cap_ids = await _ids(session, Capability, data["capability_slugs"])
        await _replace_links(session, SignalCapability, "capability_id", signal_id, cap_ids)
        kit_ids = await _ids(session, Kit, data["kit_slugs"])
        await _replace_links(session, SignalKit, "kit_id", signal_id, kit_ids)
        # JFrog itself is never a signal's company, even via a stale document link.
        comp_ids = (
            await session.execute(
                select(DocumentCompetitor.competitor_id)
                .join(SignalDocument, SignalDocument.document_id == DocumentCompetitor.document_id)
                .join(Competitor, Competitor.id == DocumentCompetitor.competitor_id)
                .where(SignalDocument.signal_id == signal_id, ~Competitor.is_self)
            )
        ).scalars()
        await _replace_links(
            session, SignalCompetitor, "competitor_id", signal_id, sorted(set(comp_ids))
        )

        await session.execute(delete(Evidence).where(Evidence.signal_id == signal_id))
        kept: list[tuple[int, str, float]] = []
        for doc_id, quote, score in verdict.verified_quotes:
            if all((doc_id, quote) != (d, q) for d, q, _ in kept):  # no duplicate evidence
                kept.append((doc_id, quote, score))
        kept = kept[:MAX_QUOTES]
        session.add_all(
            [
                Evidence(
                    signal_id=signal_id,
                    document_id=doc_id,
                    quote=quote,
                    verified=True,
                    match_score=score,
                )
                for doc_id, quote, score in kept
            ]
        )
        await session.commit()
