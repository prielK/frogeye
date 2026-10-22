"""Triage node: a fast model classifies prefilter-passed documents."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt, untrusted
from app.agents.schemas import RunEnums, make_models
from app.core.config import Settings
from app.models import Competitor, Document, DocumentCompetitor, Source
from app.models.enums import DocStatus, StepKind

logger = logging.getLogger(__name__)

TASK = "triage"
CONFIDENCE_FLOOR = 0.7
WORDS = 400
LEGACY_SELF_REASON = "About JFrog itself, not a competitor or the industry (R120)."


@dataclass
class TriageDecision:
    doc_id: int
    out: Any  # an instance of the run's dynamic TriageOut
    needs_disambiguation: bool


def revive_triage_out(enums: RunEnums, data: dict[str, Any]) -> BaseModel:
    """Rebuild a checkpointed TriageOut JSON for the resume path.

    A run checkpointed by an older version can hold scope "jfrog" or the self slug, which today's
    schema rejects. Rather than fail the resume, such a result becomes not relevant, and
    slugs the run's enums no longer offer are dropped.
    """
    data = dict(data)
    if enums.competitor_slugs:
        allowed = set(enums.competitor_slugs)
        data["competitor_slugs"] = [s for s in data.get("competitor_slugs", []) if s in allowed]
    if data.get("scope") == "jfrog":
        data |= {"relevant": False, "scope": "industry", "reason": LEGACY_SELF_REASON}
    return make_models(enums).TriageOut.model_validate(data)


async def select_for_triage(session: AsyncSession, settings: Settings) -> list[int]:
    when = func.coalesce(Document.published_at, Document.created_at)
    cutoff = datetime.now(UTC) - timedelta(days=settings.analysis_lookback_days)
    stmt = (
        select(Document.id)
        .where(
            Document.status == DocStatus.new,
            Document.prefilter.is_not(None),
            when >= cutoff,
        )
        .order_by(when.desc(), Document.id.desc())
        .limit(settings.triage_cap)
    )
    return list((await session.execute(stmt)).scalars().all())


def first_words(text: str | None, n: int = WORDS) -> str:
    return " ".join((text or "").split()[:n])


async def load_doc_view(
    session_factory: async_sessionmaker[AsyncSession], doc_id: int
) -> dict[str, Any] | None:
    """Prompt variables for a document, read in one short session."""
    async with session_factory() as session:
        doc = await session.get(Document, doc_id)
        if doc is None:
            return None
        source = await session.get(Source, doc.source_id)
        terms = list(((doc.prefilter or {}).get("matched_terms")) or [])
        # The title is scraped text: it goes inside the untrusted block with the body.
        body = f"Title: {doc.title or '(untitled)'}\n\n{first_words(doc.clean_text or doc.summary)}"
        return {
            "source": source.name if source else "unknown",
            "terms": terms,
            "doc": untrusted(doc_id, body),
        }


async def triage_one(
    doc_id: int,
    *,
    llm: AccountedLLM,
    session_factory: async_sessionmaker[AsyncSession],
    enums: RunEnums,
    ctx: RunCtx,
) -> TriageDecision | None:
    """Classify one document.

    Returns None (callers must skip it) when the document is missing (warning logged) or the
    LLM refused/errored (node step logged); the document then stays `new` for the next run.
    BudgetExceeded propagates.
    """
    view = await load_doc_view(session_factory, doc_id)
    if view is None:
        logger.warning("triage: document %s not found", doc_id)
        return None
    try:
        out = await llm.structured(
            TASK,
            "fast",
            make_models(enums).TriageOut,
            prompt=load_prompt("triage"),
            variables={**view, "terms": ", ".join(view["terms"]) or "-"},
            ctx=ctx,
        )
    except (LLMRefusal, LLMError) as exc:
        logger.warning("triage failed for document %s: %s", doc_id, exc)
        await llm.log_step(
            ctx, kind=StepKind.node, args={"doc_id": doc_id}, result_summary=f"error: {exc}"
        )
        return None
    return TriageDecision(doc_id, out, _needs_disambiguation(out, view["terms"], enums))


def _needs_disambiguation(out: Any, terms: list[str], enums: RunEnums) -> bool:
    if not out.relevant:
        return False
    if out.confidence < CONFIDENCE_FLOOR:
        return True
    ambiguous = set(enums.ambiguous_aliases)  # lowercased once per run in build_enums
    return any(t.lower() in ambiguous for t in terms)


async def apply_triage(session: AsyncSession, doc_id: int, out: BaseModel) -> None:
    """Persist a decision: triage JSON, status, and REPLACE the document's competitor links.

    Flush-only contract: the caller opens a fresh short session, calls this, and
    commits. Never hold that session across an LLM await.
    """
    data = out.model_dump(mode="json")
    doc = await session.get(Document, doc_id)
    if doc is None:
        return
    doc.triage = data
    doc.status = DocStatus.relevant if data["relevant"] else DocStatus.irrelevant

    wanted = Competitor.slug.in_(data["competitor_slugs"])
    source = await session.get(Source, doc.source_id)
    if (
        data["relevant"]
        and source is not None
        and source.competitor_id is not None
        and (source.config or {}).get("auto_pass", True)
    ):
        # own-feed competitor survives a model that dropped it
        wanted = wanted | (Competitor.id == source.competitor_id)
    # JFrog itself is never a document's company, whatever the source or the model say.
    ids = set(
        (await session.execute(select(Competitor.id).where(wanted, Competitor.is_self.is_(False))))
        .scalars()
        .all()
    )

    await session.execute(
        delete(DocumentCompetitor).where(DocumentCompetitor.document_id == doc_id)
    )
    if ids:
        await session.execute(
            insert(DocumentCompetitor).values(
                [{"document_id": doc_id, "competitor_id": i} for i in sorted(ids)]
            )
        )
    await session.flush()
