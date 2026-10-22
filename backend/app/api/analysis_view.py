"""Reader-side view of stage-B analyses: state, card summaries and the numbered Sources list.

An analysis stores citations as refs ("doc:<id>", "kb:<id>", "signal:<id>"). The API shows them
as 1-based source numbers into `SignalDetail.sources`: member documents first (given order,
primary first), then the KB docs and prior signals the analysis actually cites, each in
first-cited order. Which stored analysis counts, and whether it is current, is decided once,
in `analysis_store`: one that no longer parses or that an incomplete
fact-check left summary-only is no analysis, never an error.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import SignalAnalysisContent
from app.agents.analysis_store import StoredAnalysis, stored_analyses
from app.agents.nodes.score import AnalysisBlocker, analysis_blockers
from app.api.visibility import visible_signal_clause
from app.collectors.attribution import display_title
from app.core.config import get_settings
from app.models import JfrogKbDoc, Signal, User
from app.rag.kb import kb_title
from app.schemas.signals import (
    AnalysisState,
    CitedSourceOut,
    CitedTextOut,
    ProductImpactOut,
    RiskOpportunityOut,
    SignalAnalysisOut,
    SignalDocumentOut,
    WatchItemOut,
)

UNAVAILABLE_TITLE = "Signal not available"


def analysis_state(stored: StoredAnalysis | None, blocker: AnalysisBlocker | None) -> AnalysisState:
    """Current or stale when there is a usable analysis, else why there is none: the
    stage-B rule's blocker ("thin", "below_threshold"), or "pending" (eligible, not written
    yet: queued, over the run cap, awaiting approval, or failed and retried next run)."""
    if stored is not None:
        return "current" if stored.current else "stale"
    return blocker or "pending"


@dataclass(frozen=True)
class CardAnalysis:
    stored: StoredAnalysis | None
    state: AnalysisState

    @property
    def summary(self) -> str | None:
        return self.stored.content.summary.text.strip() if self.stored else None

    @property
    def content(self) -> SignalAnalysisContent | None:
        return self.stored.content if self.stored else None


async def card_analyses(
    session: AsyncSession, signals: Sequence[Signal]
) -> dict[int, CardAnalysis]:
    """Each signal's usable latest analysis (current or stale) and its state, batch-loaded
    (three queries for any number of signals)."""
    stored = await stored_analyses(session, {s.id: s.analyzed_at for s in signals})
    blockers = await analysis_blockers(
        session,
        {s.id: s.importance for s in signals if s.id not in stored},
        get_settings().analysis_min_importance,
    )
    return {
        s.id: CardAnalysis(stored.get(s.id), analysis_state(stored.get(s.id), blockers.get(s.id)))
        for s in signals
    }


def _refs(content: SignalAnalysisContent) -> list[str]:
    """Every cited ref, in reading order (with repeats)."""
    out: list[str] = list(content.summary.cites)
    for section in (content.what_happened, content.background, content.what_changed):
        for item in section:
            out += item.cites
    for impact in content.jfrog_impact:
        out += impact.cites
    for item in content.competitive_implications:
        out += item.cites
    for risk in content.risks_opportunities:
        out += risk.cites
    for watch in content.what_to_watch:
        out += watch.cites
    return out


def _parse_ref(ref: str) -> tuple[str, int] | None:
    kind, _, raw = ref.partition(":")
    if kind in ("doc", "kb", "signal") and raw.isdecimal() and len(raw) < 10:
        return kind, int(raw)
    return None


async def build_sources_and_analysis(
    session: AsyncSession,
    user: User,
    documents: Sequence[SignalDocumentOut],
    content: SignalAnalysisContent | None,
) -> tuple[list[CitedSourceOut], SignalAnalysisOut | None]:
    sources = [
        CitedSourceOut(
            n=i,
            kind="document",
            ref=f"doc:{d.id}",
            title=display_title(d.title, d.url),  # never a bare URL
            url=d.url,
            outlet=d.source_name,
            published_at=d.published_at,
            is_primary=d.is_primary,
        )
        for i, d in enumerate(documents, start=1)
    ]
    numbers = {s.ref: s.n for s in sources}
    if content is None:
        return sources, None

    wanted: dict[str, list[int]] = {"kb": [], "signal": []}
    for ref in dict.fromkeys(_refs(content)):
        parsed = _parse_ref(ref)
        if parsed and parsed[0] in wanted:
            wanted[parsed[0]].append(parsed[1])

    kb_rows = {
        k.id: k
        for k in await session.scalars(select(JfrogKbDoc).where(JfrogKbDoc.id.in_(wanted["kb"])))
    }
    sig_titles = {
        sid: title
        for sid, title in await session.execute(
            select(Signal.id, Signal.title).where(Signal.id.in_(wanted["signal"]))
        )
    }
    visible = set(
        await session.scalars(
            select(Signal.id).where(Signal.id.in_(wanted["signal"]), visible_signal_clause(user))
        )
    )
    for kb_id in dict.fromkeys(wanted["kb"]):
        if (kb := kb_rows.get(kb_id)) is None:
            continue
        numbers[f"kb:{kb_id}"] = len(sources) + 1
        sources.append(
            CitedSourceOut(
                n=len(sources) + 1,
                kind="kb",
                ref=f"kb:{kb_id}",
                title=kb_title(kb.title, kb.url),  # the product, not the URL
                url=kb.url,
                outlet="JFrog documentation",
                published_at=None,
                is_primary=False,
            )
        )
    for sid in dict.fromkeys(wanted["signal"]):
        if sid not in sig_titles:
            continue
        shown = sid in visible
        numbers[f"signal:{sid}"] = len(sources) + 1
        sources.append(
            CitedSourceOut(
                n=len(sources) + 1,
                kind="signal",
                ref=f"signal:{sid}",
                title=sig_titles[sid] if shown else UNAVAILABLE_TITLE,
                url=f"/signals/{sid}" if shown else None,
                outlet=None,
                published_at=None,
                is_primary=False,
            )
        )

    def nums(cites: list[str]) -> list[int]:
        return list(dict.fromkeys(numbers[c] for c in cites if c in numbers))

    def cited(items) -> list[CitedTextOut]:
        return [CitedTextOut(text=i.text, cites=nums(i.cites)) for i in items]

    out = SignalAnalysisOut(
        summary=CitedTextOut(text=content.summary.text, cites=nums(content.summary.cites)),
        what_happened=cited(content.what_happened),
        background=cited(content.background),
        what_changed=cited(content.what_changed),
        jfrog_impact=[
            ProductImpactOut(
                product=i.product,
                effect=i.effect,
                severity=i.severity,
                rationale=i.rationale,
                cites=nums(i.cites),
            )
            for i in content.jfrog_impact
        ],
        competitive_implications=cited(content.competitive_implications),
        recommended_actions=content.recommended_actions,
        risks_opportunities=[
            RiskOpportunityOut(kind=r.kind, text=r.text, cites=nums(r.cites))
            for r in content.risks_opportunities
        ],
        what_to_watch=[
            WatchItemOut(text=w.text, date=w.date, cites=nums(w.cites))
            for w in content.what_to_watch
        ],
        confidence_note=content.confidence_note,
    )
    return sources, out
