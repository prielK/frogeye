"""Signal selection and the newsroom's view of each selected signal.

- Selection: auto/approved signals that have been analysed, were first published
  (first_seen_at) in the last 7 days, clear `briefing_min_importance`, are not scope jfrog
  (a backstop: those rows are no longer produced), and are not
  already in a published/quiet master briefing dated BEFORE the run's local date.
  Eligibility goes by what was briefed, not by the clock, so a story collected or analysed
  late (a lagging feed, a source that recovered, the analysis cap) still reaches the next
  briefing, once. A briefing dated today never excludes anything, so a same-day re-run
  rebuilds the whole day from the same pool (v2 = the morning's signals + new ones).
- Views: the stage-A record (title, fact, impact, role actions), the thin flag (by
  raw evidence length without the first-party exemption) and the signal's
  CURRENT stage-B analysis, from which the cards are copied. A stale
  analysis, or one `analysis_store` does not count (unparseable; without a summary or
  a what_happened item) is none: the card falls back to the stage-A text, never an
  error.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, any_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import SignalAnalysisContent
from app.agents.analysis_store import stored_analyses
from app.agents.briefing_schema import EDITION_ROLES
from app.agents.nodes.score import thin_signal_ids
from app.core.config import Settings
from app.models import Competitor
from app.models.enums import LIVE_BRIEFING_STATUSES, BriefingEdition, Scope, SignalStatus
from app.models.gold import Briefing, Signal, SignalCompetitor

WINDOW = timedelta(days=7)  # how far back an unbriefed signal stays eligible
SELECTABLE = (SignalStatus.auto, SignalStatus.approved)
LIVE_STATUSES = LIVE_BRIEFING_STATUSES  # what they cite counts as briefed


def _eligible(settings: Settings, *, now: datetime):
    """The eligibility rule, as a WHERE clause over Signal (importance aside)."""
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    briefed = (
        select(Briefing.id)
        .where(
            Briefing.edition == BriefingEdition.master,
            Briefing.status.in_(LIVE_STATUSES),
            Briefing.date < today,  # today's own versions never narrow today's re-run
            Signal.id == any_(Briefing.signal_ids),
        )
        .exists()
    )
    return and_(
        Signal.status.in_(SELECTABLE),
        Signal.scope != Scope.jfrog,  # backstop
        Signal.analyzed_at.is_not(None),
        Signal.first_seen_at >= now - WINDOW,  # no upper bound: today's run files its own
        ~briefed,
    )


async def select_signals(session: AsyncSession, settings: Settings, *, now: datetime) -> list[int]:
    """Briefing candidates: eligible signals at or above `briefing_min_importance`,
    importance desc, at most `briefing_max_signals`. Skipped and failed briefings never
    count as briefed: their signals roll over to the next run."""
    stmt = (
        select(Signal.id)
        .where(
            _eligible(settings, now=now),
            Signal.importance >= settings.briefing_min_importance,
        )
        .order_by(Signal.importance.desc(), Signal.id.desc())
        .limit(settings.briefing_max_signals)
    )
    return list((await session.execute(stmt)).scalars())


async def quiet_competitors(
    session: AsyncSession, settings: Settings, *, now: datetime
) -> list[str]:
    """Tier-1 competitors with no eligible signal of any importance."""
    active = (
        select(SignalCompetitor.competitor_id)
        .join(Signal, Signal.id == SignalCompetitor.signal_id)
        .where(_eligible(settings, now=now))
    )
    stmt = (
        select(Competitor.slug)
        .where(Competitor.tier == 1, Competitor.is_self.is_(False), Competitor.id.not_in(active))
        .order_by(Competitor.slug)
    )
    return list((await session.execute(stmt)).scalars())


@dataclass(frozen=True)
class SignalView:
    id: int
    title: str
    fact: str
    impact: str
    signal_type: str | None
    stance: str | None
    importance: int
    hedged: bool
    competitors: tuple[str, ...]
    thin: bool = False  # every member document is a headline or a few lines
    role_actions: tuple[tuple[str, str], ...] = ()  # stage A: (role, text), EDITION_ROLES order
    analysis: SignalAnalysisContent | None = field(default=None, compare=False)  # current

    @property
    def exec_risk(self) -> str | None:
        """The current analysis's exec risk level (Exec's edition ranking uses it)."""
        return self.analysis.recommended_actions.exec.risk_level if self.analysis else None


def _stage_a_actions(raw: Any) -> tuple[tuple[str, str], ...]:
    """The analyst's one line per role (`signals.role_actions`); null and "" are skipped."""
    data = raw if isinstance(raw, dict) else {}
    out = []
    for role in EDITION_ROLES:
        text = data.get(role)
        if isinstance(text, str) and text.strip():
            out.append((role, text.strip()))
    return tuple(out)


async def current_analyses(
    session: AsyncSession, signals: dict[int, Signal]
) -> dict[int, SignalAnalysisContent]:
    """Each signal's current, usable stage-B analysis (`stored_analyses`)."""
    stored = await stored_analyses(session, {sid: s.analyzed_at for sid, s in signals.items()})
    return {sid: a.content for sid, a in stored.items() if a.current}


async def _load_views(session: AsyncSession, ids: list[int]) -> dict[int, SignalView]:
    if not ids:
        return {}
    sigs = {
        s.id: s for s in (await session.execute(select(Signal).where(Signal.id.in_(ids)))).scalars()
    }
    names: dict[int, list[str]] = {}
    rows = await session.execute(
        select(SignalCompetitor.signal_id, Competitor.name)
        .join(Competitor, Competitor.id == SignalCompetitor.competitor_id)
        .where(SignalCompetitor.signal_id.in_(ids), Competitor.is_self.is_(False))
        .order_by(Competitor.tier, Competitor.name)
    )
    for sid, name in rows:
        names.setdefault(sid, []).append(name)
    # takeaway eligibility goes by raw evidence length, like stage B: a
    # competitor's own short post is authoritative for scoring, not a top story.
    thin = await thin_signal_ids(session, ids, first_party_exempt=False)
    analyses = await current_analyses(session, sigs)
    return {
        i: SignalView(
            id=i,
            title=sigs[i].title,
            fact=sigs[i].fact or "",
            impact=sigs[i].impact or "",
            signal_type=sigs[i].signal_type,
            stance=sigs[i].stance,
            importance=sigs[i].importance,
            hedged=sigs[i].hedged,
            competitors=tuple(names.get(i, [])),
            thin=i in thin,
            role_actions=_stage_a_actions(sigs[i].role_actions),
            analysis=analyses.get(i),
        )
        for i in ids  # keeps the selection order (importance desc)
        if i in sigs
    }
