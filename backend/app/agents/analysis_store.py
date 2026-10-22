"""Storage helpers for stage-B analyses. Flush-only: the caller commits.

The ONE place that decides what a stored analysis is worth to its readers (the
stage-B selector, the newsroom, the read API, search indexing and scoring):
- `analysis_content` is the safe parse (a row that no longer parses, from another
  prompt version, is no analysis, never an error) plus completeness (a row
  without a summary or without a what_happened item, as an incomplete fact-check once left
  behind, is no analysis either, so the next stage-B run writes it again);
- `is_current` holds when the latest version was written at or after the signal's latest
  stage-A analysis (`signals.analyzed_at`; a signal stage A never analysed counts as
  current);
- `stored_analyses` applies both to the latest version of each signal (the latest
  only, never an older parseable one).
"""

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import SignalAnalysisContent
from app.models.gold import Signal, SignalAnalysis

logger = logging.getLogger(__name__)


async def persist_analysis(
    session: AsyncSession,
    signal_id: int,
    content: SignalAnalysisContent,
    *,
    model: str,
    prompt_version: str,
    cost_usd: float,
) -> int:
    """Write the next version (max + 1) for the signal and return it.

    The version is computed inside the caller's transaction; UNIQUE(signal_id, version) is the
    guard when two writers race: the loser's flush raises IntegrityError and writes nothing.
    """
    current = await session.scalar(
        select(func.max(SignalAnalysis.version)).where(SignalAnalysis.signal_id == signal_id)
    )
    version = (current or 0) + 1
    session.add(
        SignalAnalysis(
            signal_id=signal_id,
            version=version,
            content=content.model_dump(mode="json"),
            model=model,
            prompt_version=prompt_version,
            cost_usd=cost_usd,
            # The clock, not the transaction start: "current" compares this with the
            # signal's analyzed_at, which a long transaction's now() would put in the past.
            created_at=func.clock_timestamp(),
        )
    )
    await session.flush()
    return version


async def latest_analyses(
    session: AsyncSession, signal_ids: Collection[int]
) -> dict[int, SignalAnalysis]:
    """The highest-version analysis row per signal (signals with none are absent)."""
    if not signal_ids:
        return {}
    rows = await session.scalars(
        select(SignalAnalysis)
        .ext(distinct_on(SignalAnalysis.signal_id))
        .where(SignalAnalysis.signal_id.in_(list(signal_ids)))
        .order_by(SignalAnalysis.signal_id, SignalAnalysis.version.desc())
    )
    return {a.signal_id: a for a in rows}


def analysis_content(row: SignalAnalysis | None) -> SignalAnalysisContent | None:
    """The row's content when it is a usable analysis, else None (see the module docstring)."""
    if row is None:
        return None
    try:
        content = SignalAnalysisContent.model_validate(row.content)
    except ValidationError as exc:
        logger.warning(
            "signal %s: analysis v%s does not parse (%s errors); treated as none",
            row.signal_id,
            row.version,
            exc.error_count(),
        )
        return None
    if not content.summary.text.strip():
        return None
    if not any(p.text.strip() for p in content.what_happened):
        return None  # an incomplete fact-check left only the summary
    return content


def is_current(row: SignalAnalysis, analyzed_at: datetime | None) -> bool:
    """Written at or after the signal's latest stage-A analysis."""
    return analyzed_at is None or row.created_at >= analyzed_at


@dataclass(frozen=True)
class StoredAnalysis:
    row: SignalAnalysis
    content: SignalAnalysisContent
    current: bool  # False: stale (still shown in full, under a note)


async def stored_analyses(
    session: AsyncSession, analyzed_at: Mapping[int, datetime | None]
) -> dict[int, StoredAnalysis]:
    """Each signal's usable latest analysis, current or stale; `analyzed_at` maps signal id
    to `signals.analyzed_at`. Signals without one are absent."""
    out: dict[int, StoredAnalysis] = {}
    for sid, row in (await latest_analyses(session, list(analyzed_at))).items():
        content = analysis_content(row)
        if content is not None:
            out[sid] = StoredAnalysis(row, content, is_current(row, analyzed_at[sid]))
    return out


async def needs_analysis(session: AsyncSession, signal_ids: Collection[int]) -> set[int]:
    """Signals (among those that exist) with no current, usable analysis: none at all, a
    stale one, or one `analysis_content` rejects (unparseable, or summary-only)."""
    if not signal_ids:
        return set()
    analyzed_at = dict(
        (
            await session.execute(
                select(Signal.id, Signal.analyzed_at).where(Signal.id.in_(list(signal_ids)))
            )
        ).all()
    )
    stored = await stored_analyses(session, analyzed_at)
    return {sid for sid in analyzed_at if sid not in stored or not stored[sid].current}
