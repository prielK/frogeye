"""Publish node: file the day's briefings, then announce them by event, email and Slack.

- Persist: `persist_briefings` in one short session, committed here. Briefings are
  filed under the run's local date in `settings.timezone`. Each carries the
  DAY's funnel: this run's counts plus those of every earlier run that filed a
  version of the same date (`runs` says how many), so a same-day v2 reads as one funnel.
- Event: `briefing.published {date, status}` for every filed briefing, so the UI refreshes
  even on a skipped or failed day (the UI shows those states).
- Email and Slack only for `published` and `quiet` briefings. Each edition is
  rendered once and emailed to every user of that role in a message of their own (no
  shared To: list); analysts get the master, and so does a role whose edition is missing.
  Slack gets the headline, the top three titles (a takeaway's with its summary's first
  sentence) and the app link. Every story, takeaway and watch note links to its full
  analysis page in the app (`/signals/<id>`, with `?edition=<role>` in a role's
  edition so it opens on that role's actions) and names the source it rests on; the
  app's address is `settings.app_url` (APP_URL). A first sentence (`first_sentence`, one
  rule with the frontend's `firstSentence`) never ends at "Oct.", "U.S.", "a.m."
  or "No.".
- Card text: a takeaway shows its summary, why it matters (labelled by
  its `angle`: "Why it matters for JFrog" on the master, or the edition role's own, e.g.
  "In your deals"), an exec card's risk level (an icon plus "Risk: high", never colour
  alone), the affected products and up to 3 actions (role-labelled on the master); a
  section row its title and the summary's first sentence. Briefings filed before that
 fall back to `so_what` as the body, `now_what` as the single action and
  an item's `why_it_matters`, and read `angle` "jfrog"; empty lines are hidden.
- A same-day re-run announces only what changed: an edition whose signals are the
  same as its last published/quiet version of the day files version N+1 but is not emailed
  again, and Slack is skipped when the master is unchanged.
- Delivery is best effort: mail, Slack and event failures are logged and reported in
  `errors`, never raised. The webhook URL is a secret and is never logged.
- Idempotent per agent run: a resumed publish that finds briefings already filed by
  its agent run neither files nor announces again.

Briefing text derives from scraped sources, so the email template autoescapes and the
Slack text uses Slack's own escaping (as route.py does).
"""

import asyncio
import logging
import re
import smtplib
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.briefing_schema import (
    ANGLE_LABEL,
    ROLE_LABEL,
    BriefingContent,
    ProductImpactRef,
    SectionItem,
    Takeaway,
    content_signal_ids,
)
from app.agents.funnel import day_funnel
from app.agents.graphs.newsroom import LIVE_STATUSES, NewsroomResult, persist_briefings
from app.agents.nodes.route import slack_escape
from app.collectors.attribution import source_label
from app.core.config import Settings
from app.core.events import publish as publish_event
from app.models import Competitor, Document, PipelineRun, RawItem, Source, User
from app.models.agents import AgentRun
from app.models.enums import BriefingEdition, Role
from app.models.gold import Briefing, SignalDocument

log = logging.getLogger(__name__)

APP_URL = "http://localhost:8080"  # the default of settings.app_url
TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
SMTP_TIMEOUT_S = 10
SLACK_TIMEOUT_S = 5.0
SLACK_TITLES = 3
EFFECT_LABEL = {"threat": "Threat", "opportunity": "Opportunity", "neutral": "Neutral"}
# a sentence ends at ".", "!" or "?" (with any closing quotes or brackets) that a
# new sentence follows, except a period that ends an abbreviation or an initial. The
# frontend's firstSentence (features/briefing/story.ts) applies the same rule and cases.
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*(?=\s+[\"'“‘(]?[A-Z0-9])")
_ABBREVIATION = re.compile(
    r"(?:^|[\s(\"'“‘])(?:[A-Z]|(?:[A-Za-z]\.)+[A-Za-z]|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"No|Nos|Inc|Corp|Ltd|Co|Mr|Mrs|Ms|Dr|St|vs|etc|approx)\.$"
)

_jinja = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=True,  # every briefing string derives from scraped text
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass
class PublishOutcome:
    briefing_ids: list[int]
    emails_sent: int = 0
    slack_sent: bool = False
    errors: list[str] = field(default_factory=list)


def briefing_url(day: date, app_url: str = APP_URL) -> str:
    return f"{app_url.rstrip('/')}/briefing/{day.isoformat()}"


def story_url(signal_id: int, app_url: str = APP_URL, *, edition: str | None = None) -> str:
    """The story's full analysis page (one click to the analysis, not the drawer),
    opening on `edition`'s actions for a role's edition (its `?edition=` query)."""
    url = f"{app_url.rstrip('/')}/signals/{signal_id}"
    if edition and edition != BriefingEdition.master:
        url += f"?edition={edition}"
    return url


def _one_line(text: str) -> str:
    return " ".join(text.split())


def first_sentence(text: str) -> str:
    """The first sentence of `text`, on one line ("" for empty text); "Oct. 15", "U.S.",
    "9 a.m." and "No. 1" never end it."""
    flat = _one_line(text)
    for m in _SENTENCE_END.finditer(flat):
        if flat[m.start()] == "." and _ABBREVIATION.search(flat[: m.start() + 1]):
            continue
        return flat[: m.end()]
    return flat


def takeaway_body(t: Takeaway) -> str:
    """The card body: the copied summary, or a legacy briefing's so_what."""
    return t.summary or t.so_what


def item_gist(i: SectionItem) -> str:
    """A section row's line: the summary's first sentence, or a legacy why_it_matters."""
    return first_sentence(i.summary) if i.summary else i.why_it_matters


def product_label(p: ProductImpactRef) -> str:
    return f"{p.product} · {EFFECT_LABEL.get(p.effect, p.effect)} · {p.severity}"


def edition_label(edition: str) -> str:
    if edition == BriefingEdition.master:
        return "Master edition"
    return f"{ROLE_LABEL.get(edition, edition)} edition"


def render_email(
    content: BriefingContent,
    *,
    day: date,
    edition: str,
    names: dict[str, str],
    funnel: dict[str, Any] | None = None,
    app_url: str = APP_URL,
    sources: dict[int, str] | None = None,
) -> tuple[str, str, str]:
    """(subject, html, plain text) of one edition's email. `sources` names each signal's
    primary source (signal id -> label); every item links to its story in the app. The
    master labels each action with its role; an edition's are all its own."""
    subject = f"FrogEye · {day.isoformat()} · {_one_line(content.headline)}"
    variables = {
        "content": content,
        "day": day,
        "day_label": f"{day:%A} {day.day} {day:%B %Y}",
        "edition_label": edition_label(edition),
        "url": briefing_url(day, app_url),
        "story": lambda signal_id: story_url(signal_id, app_url, edition=edition),
        "sources": sources or {},
        "quiet": [names.get(slug, slug) for slug in content.quiet_competitors],
        "funnel": funnel or {},
        # every signal the briefing shows (takeaways, sections, watchlist): the app's count
        "stories": len(content_signal_ids(content)),
        "show_roles": edition == BriefingEdition.master,
        "role_label": ROLE_LABEL,
        "angle_label": ANGLE_LABEL,
        "body": takeaway_body,
        "gist": item_gist,
        "product_label": product_label,
    }
    html = _jinja.get_template("briefing_email.html").render(**variables)
    return subject, html, _plain_text(content, variables)


def _plain_text(content: BriefingContent, v: dict[str, Any]) -> str:
    story, sources = v["story"], v["sources"]

    def ref(signal_id: int) -> str:
        name = sources.get(signal_id)
        return f"{name}: {story(signal_id)}" if name else story(signal_id)

    lines = [f"FrogEye · {v['day_label']} · {v['edition_label']}", "", content.headline, ""]
    lines += [content.lead, ""]
    if content.top_takeaways:
        lines.append("Top takeaways")
        for t in content.top_takeaways:
            body = takeaway_body(t)
            lines.append(f"- {t.title}: {body}" if body else f"- {t.title}")
            if t.why_it_matters:
                lines.append(f"  {ANGLE_LABEL[t.angle]}: {t.why_it_matters}")
            if t.risk_level:
                lines.append(f"  Risk: {t.risk_level}")
            if t.products:
                products = ", ".join(f"{p.product} ({p.effect}, {p.severity})" for p in t.products)
                lines.append(f"  Products: {products}")
            if t.actions:
                lines.append("  What to do:")
                for a in t.actions:
                    label = f"{ROLE_LABEL[a.role]}: " if v["show_roles"] else ""
                    lines.append(f"    - {label}{a.text}")
            elif t.now_what.strip():
                lines.append(f"  Now what: {t.now_what}")
            lines += [f"  Source: {ref(sid)}" for sid in t.signal_ids]
        lines.append("")
    for section in content.sections:
        lines.append(section.theme)
        for i in section.items:
            gist = item_gist(i)
            lines.append(f"- {i.one_liner}: {gist}" if gist else f"- {i.one_liner}")
            lines.append(f"  Source: {ref(i.signal_id)}")
        lines.append("")
    if content.watchlist:
        lines.append("Watchlist")
        lines += [f"- {w.note} ({story(w.signal_id)})" for w in content.watchlist]
        lines.append("")
    if v["quiet"]:
        lines += [f"Quiet: {', '.join(v['quiet'])}", ""]
    lines.append(f"Open the briefing: {v['url']}")
    return "\n".join(lines)


def slack_text(content: BriefingContent, *, day: date, app_url: str = APP_URL) -> str:
    """Headline, up to three titles (takeaways first, with their summary's first sentence,
    then stories), each linked to its story in the app, and the briefing's link."""
    titles = [
        (t.title, t.signal_ids[0] if t.signal_ids else None, first_sentence(takeaway_body(t)))
        for t in content.top_takeaways
    ]
    titles += [(i.one_liner, i.signal_id, "") for s in content.sections for i in s.items]
    lines = [f"*FrogEye · {day.isoformat()}*: {slack_escape(_one_line(content.headline))}"]
    for title, signal_id, gist in titles[:SLACK_TITLES]:
        text = slack_escape(_one_line(title))
        link = story_url(signal_id, app_url) if signal_id is not None else None
        line = f"• <{link}|{text}>" if link else f"• {text}"
        lines.append(f"{line} — {slack_escape(gist)}" if gist else line)
    lines.append(f"<{briefing_url(day, app_url)}|Open the briefing>")
    return "\n".join(lines)


async def _day_funnel(
    session: AsyncSession, day: date, agent_run_id: int | None, funnel: dict[str, Any] | None
) -> dict[str, Any] | None:
    """`funnel` (this run's) summed with the run record of every earlier run that
    filed a briefing for `day`. Nothing to add (no funnel, no earlier run) keeps it as is."""
    fed = select(Briefing.agent_run_id).where(
        Briefing.date == day, Briefing.agent_run_id.is_not(None)
    )
    stmt = (
        select(PipelineRun.stats)
        .join(AgentRun, AgentRun.pipeline_run_id == PipelineRun.id)
        .where(AgentRun.id.in_(fed))
        .order_by(AgentRun.id)
    )
    if agent_run_id is not None:
        stmt = stmt.where(AgentRun.id != agent_run_id)
    earlier = list((await session.execute(stmt)).scalars())
    if not funnel and not earlier:
        return funnel
    return day_funnel(earlier, funnel or {})


async def _sources(session: AsyncSession, signal_ids: set[int]) -> dict[int, str]:
    """signal id -> its primary source, named for readers (an outlet behind an aggregator)."""
    if not signal_ids:
        return {}
    rows = await session.execute(
        select(SignalDocument.signal_id, Source.kind, Source.name, RawItem.payload)
        .join(Document, Document.id == SignalDocument.document_id)
        .join(Source, Source.id == Document.source_id)
        .outerjoin(RawItem, RawItem.id == Document.raw_item_id)
        .where(SignalDocument.signal_id.in_(signal_ids), SignalDocument.is_primary)
    )
    return {sid: source_label(kind, name, payload) for sid, kind, name, payload in rows}


async def _last_live_signals(session: AsyncSession, day: date) -> dict[str, list[int]]:
    """edition -> the signal ids of its latest published/quiet version filed for `day`."""
    rows = await session.execute(
        select(Briefing.edition, Briefing.signal_ids)
        .where(Briefing.date == day, Briefing.status.in_(LIVE_STATUSES))
        .order_by(Briefing.version)
    )
    return {edition: list(ids or []) for edition, ids in rows}  # the latest version wins


async def _recipients(session: AsyncSession, editions: set[str]) -> dict[str, list[str]]:
    """edition -> emails. Analysts read the master; a role without an edition gets it too."""
    out: dict[str, list[str]] = {}
    for email, role in (await session.execute(select(User.email, User.role))).all():
        edition = str(role)
        if edition == Role.analyst or edition not in editions:
            edition = BriefingEdition.master.value
        out.setdefault(edition, []).append(email)
    return {e: sorted(emails) for e, emails in out.items()}


def _message(settings: Settings, to: str, subject: str, html: str, plain: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.briefing_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="frogeye.local")
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    return msg


def _send_all(
    host: str, port: int, messages: list[tuple[str, EmailMessage]]
) -> tuple[int, list[str]]:
    """Blocking SMTP delivery of (edition, message) pairs, run in a thread.
    Returns (sent, errors); errors name the edition, never a recipient."""
    sent, errors = 0, []
    try:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_S) as smtp:
            for edition, msg in messages:
                try:
                    smtp.send_message(msg)
                    sent += 1
                except (smtplib.SMTPException, OSError) as exc:
                    errors.append(f"email of the {edition} edition failed ({type(exc).__name__})")
    except (smtplib.SMTPException, OSError) as exc:
        errors.append(f"email failed: SMTP {host}:{port} unreachable ({type(exc).__name__})")
    return sent, errors


async def _post_slack(url: str, text: str) -> str | None:
    """None on success, else an error note (class name only: httpx messages can embed the
    request URL, which is the secret)."""
    try:
        async with httpx.AsyncClient(timeout=SLACK_TIMEOUT_S) as client:
            resp = await client.post(url, json={"text": text})
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - delivery never fails the run
        return f"slack post failed ({type(exc).__name__})"
    return None


async def publish_briefings(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    result: NewsroomResult,
    *,
    day: date,
    agent_run_id: int | None,
    funnel: dict[str, Any] | None,
) -> PublishOutcome:
    """Persist the newsroom's result, then announce it (see the module docstring).

    Idempotent per agent run: if this agent run already filed briefings (a resumed run
    whose worker died mid-publish), they are returned as they are, with no new version and
    no second round of email, Slack or event.
    """
    async with session_factory() as session:
        filed: list[int] = []
        if agent_run_id is not None:
            filed = list(
                (
                    await session.execute(
                        select(Briefing.id)
                        .where(Briefing.agent_run_id == agent_run_id)
                        .order_by(Briefing.id)
                    )
                )
                .scalars()
                .all()
            )
        if filed:
            log.warning(
                "agent run %s already filed briefings %s: not filing or announcing again",
                agent_run_id,
                filed,
            )
            return PublishOutcome(briefing_ids=filed)
        funnel = await _day_funnel(session, day, agent_run_id, funnel)
        announced = await _last_live_signals(session, day)
        ids = await persist_briefings(
            session, result, date=day, agent_run_id=agent_run_id, funnel=funnel
        )
        await session.commit()
    outcome = PublishOutcome(briefing_ids=ids)
    status = str(result.status)

    if not await publish_event("briefing.published", {"date": day.isoformat(), "status": status}):
        outcome.errors.append("briefing.published event not delivered")
    if result.status not in LIVE_STATUSES:
        log.info("briefing %s is %s: no email or Slack", day, status)
        return outcome

    contents = {BriefingEdition.master.value: result.master, **result.editions}
    # an edition carrying the same signals as its last announced version is filed
    # but not announced again.
    unchanged = {
        e
        for e, c in contents.items()
        if e in announced and sorted(announced[e]) == sorted(content_signal_ids(c))
    }
    if unchanged:
        log.info("briefing %s: unchanged editions not announced again: %s", day, sorted(unchanged))
    cited = {sid for c in contents.values() for sid in content_signal_ids(c)}
    async with session_factory() as session:  # short and read-only; closed before any I/O
        recipients = await _recipients(session, set(contents))
        names = dict((await session.execute(select(Competitor.slug, Competitor.name))).all())
        sources = await _sources(session, cited)
    messages = []
    for edition, emails in recipients.items():
        if edition in unchanged:
            continue
        subject, html, plain = render_email(
            contents[edition],
            day=day,
            edition=edition,
            names=names,
            funnel=funnel,
            app_url=settings.app_url,
            sources=sources,
        )
        # One message per recipient: a shared To: would show every colleague's address.
        messages += [(edition, _message(settings, to, subject, html, plain)) for to in emails]
    if messages:
        sent, errors = await asyncio.to_thread(
            _send_all, settings.smtp_host, settings.smtp_port, messages
        )
        outcome.emails_sent = sent
        outcome.errors += errors

    if settings.slack_webhook_url and BriefingEdition.master.value not in unchanged:
        text = slack_text(result.master, day=day, app_url=settings.app_url)
        error = await _post_slack(settings.slack_webhook_url, text)
        outcome.slack_sent = error is None
        if error:
            outcome.errors.append(error)
    for error in outcome.errors:
        log.warning("publish %s: %s", day, error)
    return outcome
