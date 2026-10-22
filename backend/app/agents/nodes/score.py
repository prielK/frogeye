"""Deterministic importance scoring and Admiralty-style grading (no LLM)."""

from collections.abc import Collection, Mapping
from datetime import UTC, datetime, time
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import scoring_config as cfg
from app.agents.analysis_store import stored_analyses
from app.models import Competitor, Document, Kit, Source
from app.models.gold import Signal, SignalCompetitor, SignalDocument, SignalKit


def grade_credibility(reliability: str | None, corroboration_count: int, hedged: bool) -> int:
    """1: >=2 distinct hosts and best reliability <= B; 2: one host <= B; 3: else; 4: hedged."""
    if hedged:
        return 4
    good = reliability is not None and reliability <= "B"
    if good and corroboration_count >= 2:
        return 1
    if good:
        return 2
    return 3


async def thin_signal_ids(
    session: AsyncSession, signal_ids: Collection[int], *, first_party_exempt: bool = True
) -> set[int]:
    """The signals among `signal_ids` whose member documents ALL have less than
    `THIN_BODY_CHARS` of body text (clean_text, else summary; surrounding blanks ignored).
    A signal with no member documents is not thin: nothing says its evidence is shallow.
    For scoring only, nor is one with a first-party member (a source of
    `FIRST_PARTY_RELIABILITY`): a competitor's own short statement is authoritative.
    `first_party_exempt=False` drops that exemption: a full analysis needs text to analyse,
    whoever wrote it, and the briefing's thin rule reads the raw evidence the same
    way (`newsroom/selection.py` passes False)."""
    if not signal_ids:
        return set()
    body = func.coalesce(func.nullif(Document.clean_text, ""), Document.summary, "")
    having = [func.max(func.char_length(func.btrim(body))) < cfg.THIN_BODY_CHARS]
    if first_party_exempt:
        first_party = func.bool_or(Source.reliability == cfg.FIRST_PARTY_RELIABILITY)
        having.append(first_party.is_not(True))
    rows = await session.execute(
        select(SignalDocument.signal_id)
        .join(Document, Document.id == SignalDocument.document_id)
        .join(Source, Source.id == Document.source_id)
        .where(SignalDocument.signal_id.in_(list(signal_ids)))
        .group_by(SignalDocument.signal_id)
        .having(*having)
    )
    return set(rows.scalars())


AnalysisBlocker = Literal["thin", "below_threshold"]


async def analysis_blockers(
    session: AsyncSession, importance: Mapping[int, int], min_importance: int
) -> dict[int, AnalysisBlocker]:
    """Why stage B will not write a full analysis for these signals (signal id ->
    importance), ONE rule for `select_for_deep_analysis` and the read side's analysis state. Signals
    absent from the result are eligible.
    - "thin": every member document is headline-only or a few lines (the thin-evidence measure,
    without
      the first-party exemption, which is for scoring only);
    - "below_threshold": importance under `min_importance` (`analysis_min_importance`)."""
    shallow = await thin_signal_ids(session, list(importance), first_party_exempt=False)
    out: dict[int, AnalysisBlocker] = {}
    for sid, imp in importance.items():
        if sid in shallow:
            out[sid] = "thin"
        elif imp < min_importance:
            out[sid] = "below_threshold"
    return out


def _age_days(sig: Signal, now: datetime) -> float:
    if sig.event_date is not None:
        start = datetime.combine(sig.event_date, time.min, tzinfo=UTC)
    else:
        start = sig.first_seen_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
    return max(0.0, (now - start).total_seconds() / 86400)


async def score_signal(session: AsyncSession, signal_id: int, *, now: datetime) -> int:
    """Score one signal, store importance/reliability/credibility and merge the breakdown.

    Flush-only: the caller owns the transaction and commits. Existing score_breakdown keys
    (error, verification, alerted_at, ...) are preserved; only the score components are replaced.
    A NULL impact or novelty score counts as 0. A signal whose documents are all thin
    is multiplied by `THIN_EVIDENCE_MULTIPLIER`, recorded as `evidence_depth`; one whose
    current stage-B analysis rates no JFrog product by `NO_PRODUCT_IMPACT_MULTIPLIER`,
    recorded as `product_impact`.
    """
    sig = await session.get(Signal, signal_id)
    if sig is None:
        raise LookupError(f"signal {signal_id} not found")

    rels = (
        (
            await session.execute(
                select(Source.reliability)
                .join(Document, Document.source_id == Source.id)
                .join(SignalDocument, SignalDocument.document_id == Document.id)
                .where(SignalDocument.signal_id == signal_id)
            )
        )
        .scalars()
        .all()
    )
    reliability = min(rels) if rels else None
    credibility = grade_credibility(reliability, sig.corroboration_count, sig.hedged)

    tiers = (
        (
            await session.execute(
                select(Competitor.tier)
                .join(SignalCompetitor, SignalCompetitor.competitor_id == Competitor.id)
                .where(SignalCompetitor.signal_id == signal_id)
            )
        )
        .scalars()
        .all()
    )
    tier_w = max(cfg.TIER_W[t] for t in tiers) if tiers else cfg.INDUSTRY_W

    prios = (
        (
            await session.execute(
                select(Kit.priority)
                .join(SignalKit, SignalKit.kit_id == Kit.id)
                .where(SignalKit.signal_id == signal_id)
            )
        )
        .scalars()
        .all()
    )
    kit_w = max(cfg.KIT_PRIORITY_W[p] for p in prios) if prios else 0.0

    values = {
        "impact": (sig.impact_score or 0) / 5,
        "novelty": (sig.novelty_score or 0) / 5,
        "competitor_tier": tier_w,
        "type": cfg.TYPE_W.get(sig.signal_type or "other", cfg.TYPE_W["other"]),
        "kit_priority": kit_w,
        "credibility": cfg.CREDIBILITY_W[credibility],
        "corroboration": min(max(sig.corroboration_count - 1, 0), 3) / 3,
    }
    components = {
        name: {"value": v, "weight": cfg.WEIGHTS[name], "contribution": v * cfg.WEIGHTS[name]}
        for name, v in values.items()
    }
    age = _age_days(sig, now)
    decay = 0.5 ** (age / cfg.HALF_LIFE_DAYS)
    thin = signal_id in await thin_signal_ids(session, [signal_id])
    multiplier = cfg.THIN_EVIDENCE_MULTIPLIER if thin else 1.0
    stored = (await stored_analyses(session, {signal_id: sig.analyzed_at})).get(signal_id)
    no_product = stored is not None and stored.current and not stored.content.jfrog_impact
    relevance = cfg.NO_PRODUCT_IMPACT_MULTIPLIER if no_product else 1.0
    raw = sum(c["contribution"] for c in components.values()) * decay * multiplier * relevance
    importance = max(0, min(100, round(100 * raw)))

    sig.reliability = reliability
    sig.credibility = credibility
    sig.importance = importance
    sig.score_breakdown = {
        **(sig.score_breakdown or {}),
        "components": components,
        "decay": decay,
        "age_days": age,  # in 24-hour days, at this scoring
        "age_from": "event_date" if sig.event_date is not None else "first_seen",
        "evidence_depth": {"thin": thin, "multiplier": multiplier},
        "product_impact": {"none": no_product, "multiplier": relevance},
        "importance": importance,
    }
    await session.flush()
    return importance
