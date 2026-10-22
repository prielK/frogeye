"""Breaking-signal routing: SSE event (via the events bus) plus an optional Slack webhook.

Three steps, so no DB transaction stays open across network I/O: `plan_alerts`
reads what is due in a session, `deliver_alerts` sends with no session open, and
`mark_alerted` stamps `alerted_at` in a second short session. `route_signals` runs all three
on one session for callers that hold one anyway.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.scoring_config import BREAKING_THRESHOLD
from app.core.config import Settings
from app.core.events import publish
from app.models import Competitor
from app.models.enums import SignalStatus
from app.models.gold import Signal, SignalCompetitor

log = logging.getLogger(__name__)

ROUTABLE = (SignalStatus.auto.value, SignalStatus.approved.value)


def slack_escape(text: str) -> str:
    """Slack's required escaping, so LLM-derived titles cannot ping or inject links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class Alert:
    signal_id: int
    title: str
    importance: int
    competitors: list[str]


async def plan_alerts(session: AsyncSession, signal_ids: list[int]) -> list[Alert]:
    """Breaking signals among `signal_ids` (importance >= threshold, status auto/approved)
    not alerted yet. Read-only."""
    if not signal_ids:
        return []
    sigs = (
        (
            await session.execute(
                select(Signal).where(
                    Signal.id.in_(signal_ids),
                    Signal.status.in_(ROUTABLE),
                    Signal.importance >= BREAKING_THRESHOLD,
                )
            )
        )
        .scalars()
        .all()
    )
    alerts = []
    for sig in sigs:
        if (sig.score_breakdown or {}).get("alerted_at"):
            continue
        names = (
            (
                await session.execute(
                    select(Competitor.name)
                    .join(SignalCompetitor, SignalCompetitor.competitor_id == Competitor.id)
                    .where(SignalCompetitor.signal_id == sig.id, Competitor.is_self.is_(False))
                    .order_by(Competitor.name)
                )
            )
            .scalars()
            .all()
        )
        alerts.append(Alert(sig.id, sig.title, sig.importance, list(names)))
    return alerts


async def deliver_alerts(alerts: list[Alert], settings: Settings) -> list[int]:
    """Send each alert (event bus, then Slack); returns the ids at least one channel took.
    Never raises; the webhook URL is never logged."""
    delivered_ids = []
    for alert in alerts:
        delivered = await publish(
            "signal.breaking",
            {
                "signal_id": alert.signal_id,
                "title": alert.title,
                "importance": alert.importance,
                "competitors": alert.competitors,
            },
        )
        if settings.slack_webhook_url:
            who = ", ".join(alert.competitors) or "industry"
            text = slack_escape(f"{alert.title} (importance {alert.importance}) - {who}")
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(settings.slack_webhook_url, json={"text": text})
                    resp.raise_for_status()
                delivered = True
            except Exception as exc:
                # Class name only: httpx messages can embed the request URL (the secret).
                log.warning(
                    "slack alert failed for signal %s: %s", alert.signal_id, type(exc).__name__
                )
        if delivered:
            delivered_ids.append(alert.signal_id)
        else:
            log.warning("breaking alert for signal %s not delivered; will retry", alert.signal_id)
    return delivered_ids


async def mark_alerted(session: AsyncSession, signal_ids: list[int]) -> None:
    """Stamp `alerted_at` (only for alerts some channel delivered). Flush-only."""
    for sid in signal_ids:
        sig = await session.get(Signal, sid)
        if sig is not None:
            sig.score_breakdown = {
                **(sig.score_breakdown or {}),
                "alerted_at": datetime.now(UTC).isoformat(),
            }
    await session.flush()


async def route_signals(session: AsyncSession, signal_ids: list[int], settings: Settings) -> None:
    """Alert once per breaking signal (importance >= threshold, status auto/approved).

    Flush-only: the caller commits. `alerted_at` is set only once at least one channel delivered
    (event bus or Slack 2xx); if all fail it stays unset so the next run retries. Holds the
    caller's session across delivery: the daily graph uses the three steps instead.
    """
    alerts = await plan_alerts(session, signal_ids)
    await mark_alerted(session, await deliver_alerts(alerts, settings))
