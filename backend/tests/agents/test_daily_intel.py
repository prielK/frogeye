"""Daily Intelligence graph end to end: ingest -> index -> triage -> disambiguate ->
apply_triage -> cluster -> analyze -> score -> deep_analyze -> route -> newsroom -> publish
(tag_postings, the job-posting tagger, sits right after index_new; judge_pages after it;
extract_facts, the company-facts extractor, right after deep_analyze).

Fake collectors, a scripted FakeBackend and an in-memory checkpointer: nothing here reaches
the network (respx blocks any unmocked HTTP where Slack is configured)."""

import asyncio
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select, text, update

from app.agents import runner as runner_mod
from app.agents.briefing_schema import EDITION_ROLES, BriefingContent
from app.agents.graphs import daily_intel as daily_mod
from app.agents.graphs.daily_intel import NODES, build_daily_graph, rank_for_analysis
from app.agents.graphs.newsroom import FAILED_HEADLINE, NewsroomResult
from app.agents.nodes import publish as publish_mod
from app.agents.nodes import route as route_mod
from app.agents.nodes.publish import publish_briefings, render_email, slack_text
from app.agents.nodes.signal_analysis import select_for_deep_analysis
from app.agents.runner import DailyResult, NothingToResume, run_daily_intel
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.agents.runtime.pricing import cost_usd
from app.agents.runtime.types import StructuredOut, Usage
from app.collectors.base import FetchResult, RawItemIn
from app.models import Document, PipelineRun, Source, User
from app.models.agents import AgentRun, AgentStep, LlmCall
from app.models.gold import Briefing, Signal, SignalAnalysis, SignalDocument
from app.processing import ingest as ingest_mod
from app.processing.ingest import INGEST_LOCK_KEY, RunAlreadyActive, ingestion_lock_held
from tests.agents.fakes import FakeBackend
from tests.agents.newsroom_script import SIGNAL_LINE, Newsroom, make_plan

HOOK = "https://hooks.slack.test/services/T000/B000/SECRETTOKEN"
QUOTE = "Sonatype announced a gateway that routes every AI model request through policy."
GATEWAY_A = "Sonatype launches an AI model gateway"
GATEWAY_B = "Sonatype AI model gateway is now generally available"
PRICING = "Sonatype revises its repository pricing"
GATEWAY_C = "Sonatype AI model gateway adds policy controls"
PERSONAS = {
    "analyst@frogeye.local": "master",
    "sales@frogeye.local": "sales",
    "pm@frogeye.local": "pm",
    "exec@frogeye.local": "exec",
    "devrel@frogeye.local": "pmm_devrel",
}
WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november "
    "oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu"
).split()


# ---- a test session factory that serialises sessions ------------------------------------


class SerialSessions:
    """The test `session_factory`, one session at a time.

    Every test session shares one connection (savepoint mode), so two sessions open at once
    would nest and release each other's savepoints. In production each session gets its own
    pooled connection and the graph's fan-out runs truly concurrently. A long wait for a
    session means one is being held across an LLM await: it fails the test instead
    of hanging it.
    """

    WAIT_S = 20

    def __init__(self, inner) -> None:
        self._inner = inner
        self.kw = inner.kw  # ingest's advisory lock reads the bind from here
        self._lock = asyncio.Lock()

    def __call__(self) -> "_SerialSession":
        return _SerialSession(self)


class _SerialSession:
    def __init__(self, owner: SerialSessions) -> None:
        self._owner = owner
        self._cm = None

    async def __aenter__(self):
        try:
            await asyncio.wait_for(self._owner._lock.acquire(), self._owner.WAIT_S)
        except TimeoutError:
            raise AssertionError("a session was held across an await for too long") from None
        try:
            self._cm = self._owner._inner()
            return await self._cm.__aenter__()
        except BaseException:
            self._owner._lock.release()
            raise

    async def __aexit__(self, *exc):
        try:
            return await self._cm.__aexit__(*exc)
        finally:
            self._owner._lock.release()


# ---- fake vendor items -------------------------------------------------------------------


def body(seed: int, *, cluster: str | None = None) -> str:
    # > 400 chars (no full-text fetch) and distinct per seed (no simhash duplicates); the
    # `#cluster:` tag makes the fake embedder put same-story documents next to each other.
    rot = WORDS[seed % 26 :] + WORDS[: seed % 26]
    tag = f"#cluster:{cluster} " if cluster else ""
    return f"{tag}{QUOTE} " + " ".join(f"{w}{seed}" for w in rot * 6)


def item(
    n: int,
    title: str,
    *,
    cluster: str | None = None,
    hours_ago: float = 2,
    before: datetime | None = None,
) -> RawItemIn:
    return RawItemIn(
        external_id=f"daily-{n}",
        url=f"https://news.test/sonatype-{n}",
        title=title,
        published_at=(before or datetime.now(UTC)) - timedelta(hours=hours_ago),
        raw_text=body(n, cluster=cluster),
        payload={},
    )


ITEMS = [
    item(1, GATEWAY_A, cluster="gateway", hours_ago=3),
    item(2, GATEWAY_B, cluster="gateway", hours_ago=2),
    item(3, PRICING, hours_ago=1),
]


# ---- scripted agents ---------------------------------------------------------------------


def _triage_out(user: str, *, confident: bool = False) -> dict:
    pricing = PRICING in user
    return {
        "relevant": True,
        "scope": "competitor",
        "competitor_slugs": ["sonatype"],
        "signal_type": "pricing_change" if pricing else "product_launch",
        "confidence": 0.5 if pricing and not confident else 0.9,  # the pricing doc is unsure
        "reason": "Sonatype's own product news",
    }


def triage(*, user, **_):
    return _triage_out(user)


def disambiguate(*, kind, user=None, **_):
    if kind == "tool_turn":
        return "No tools needed."
    return {**_triage_out(user, confident=True), "confidence": 0.95}


def story_judge(**_):
    return {"same_event": False, "reason": "different events"}


def analyst(*, kind, user=None, **_):
    if kind == "tool_turn":
        return "No tools needed."
    primary = int(user.split("Document ", 1)[1].split(" ", 1)[0])
    title = user.split(f'<document id="{primary}">Title: ', 1)[1].split("\n", 1)[0]
    pricing = title == PRICING
    return {
        "title": "Sonatype revises repository pricing"
        if pricing
        else "Sonatype ships an AI model gateway",
        "fact": "Sonatype changed its repository pricing."
        if pricing
        else "Sonatype launched a gateway that routes AI model requests through policy.",
        "event_date": None,
        "signal_type": "pricing_change" if pricing else "product_launch",
        "stance": "threat",
        "capability_slugs": [],
        "kit_slugs": [],
        "impact": "Puts pressure on JFrog's model governance story.",
        "impact_score": 4,
        "novelty_score": 3,
        "role_actions": {
            "sales": "Lead with JFrog's end-to-end model governance.",
            "pm": None,
            "exec": None,
            "pmm_devrel": None,
        },
        "quotes": [{"doc_id": primary, "quote": QUOTE}],
        "hedged": False,
    }


REFS_LINE = re.compile(r'Citable sources \(use exactly these refs in "cites"\): (.*)')
ITEM_ID = re.compile(r"^S(\d+) ", re.M)
SUMMARY = "Sonatype launched a gateway that routes AI model requests through policy."


def minimal_analysis(doc_ref: str, *, impact: bool = True) -> dict:
    """The smallest stage-B output that is stored: a cited summary and what_happened
    paragraph and one JFrog product rated (else the relevance multiplier lowers the score), the rest
    empty."""
    return {
        "summary": {"text": SUMMARY, "cites": [doc_ref]},
        "what_happened": [{"text": "Sonatype shipped the gateway.", "cites": [doc_ref]}],
        "background": [],
        "what_changed": [],
        "jfrog_impact": [
            {
                "product": "AI Catalog",
                "effect": "threat",
                "severity": "medium",
                "rationale": "The gateway governs model requests.",
                "cites": [doc_ref],
            }
        ]
        if impact
        else [],
        "competitive_implications": [],
        "recommended_actions": {
            "sales": {
                "talk_track": [],
                "discovery_questions": [],
                "objections": [],
                "landmines": [],
            },
            "pm": {"assessment": [], "follow_ups": []},
            "exec": {"implications": [], "decisions": [], "risk_level": None},
            "pmm_devrel": {"messaging": [], "content_ideas": []},
        },
        "risks_opportunities": [],
        "what_to_watch": [],
        "confidence_note": "",
    }


def signal_analysis(*, user, **_):
    refs = [r.strip() for r in REFS_LINE.search(user).group(1).split(",")]
    return minimal_analysis(next(r for r in refs if r.startswith("doc:")))


def analysis_fact_check(*, user, **_):
    return {
        "verdicts": [
            {"sentence_id": f"S{n}", "kind": "claim", "supported": True, "reason": "supported"}
            for n in ITEM_ID.findall(user)
        ]
    }


class DailyNewsroom(Newsroom):
    """The newsroom script, with a plan built from the signals the editor is shown."""

    THEMES = ("Product & releases", "Competitive moves", "Market & money")

    def __init__(self, **kw) -> None:
        super().__init__(plan=None, **kw)

    def editor(self, *, kind, user=None, **_):
        if kind == "tool_turn":
            return "No tools needed."
        ids = [int(x) for x in SIGNAL_LINE.findall(user)]
        return make_plan([(sid, self.THEMES[i % len(self.THEMES)]) for i, sid in enumerate(ids)])


def daily_script(newsroom: Newsroom | None = None, **overrides) -> dict:
    script = {
        "triage": triage,
        "disambiguate": disambiguate,
        "story_judge": story_judge,
        "analyst": analyst,
        "signal_analysis": signal_analysis,
        "analysis_fact_check": analysis_fact_check,
        **(newsroom or DailyNewsroom()).script(),
    }
    script.update(overrides)
    return script


# ---- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def serial(session_factory) -> SerialSessions:
    return SerialSessions(session_factory)


@pytest.fixture
def cfg(settings):
    """Run settings: no OpenAI key, no Slack (unless a test sets them)."""

    def _make(**overrides):
        base = {"openai_api_key": None, "slack_webhook_url": None}
        return settings.model_copy(update={**base, **overrides})

    return _make


@pytest.fixture
def make_llm(serial, cfg):
    def _make(script: dict, settings=None) -> AccountedLLM:
        return AccountedLLM(FakeBackend(script), serial, settings or cfg())

    return _make


class Killed(BaseException):
    """Stands in for a worker dying mid-run (not an Exception, so no node swallows it)."""


@pytest.fixture
def smtp(monkeypatch):
    """Records every message 'sent'; `smtp.down = True` refuses connections and
    `smtp.die_after = n` kills the worker (Killed) on the message after the n-th."""
    box = SimpleNamespace(sent=[], down=False, hosts=[], die_after=None)

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            if box.down:
                raise ConnectionRefusedError(111, "Connection refused")
            box.hosts.append((host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send_message(self, msg):
            if box.die_after is not None and len(box.sent) >= box.die_after:
                raise Killed()
            box.sent.append(msg)

    monkeypatch.setattr(publish_mod.smtplib, "SMTP", FakeSMTP)
    return box


@pytest.fixture
def events(monkeypatch):
    got: list[tuple[str, dict]] = []

    async def fake_publish(event_type, payload):
        got.append((event_type, payload))
        return True

    monkeypatch.setattr(daily_mod, "publish_event", fake_publish)
    monkeypatch.setattr(publish_mod, "publish_event", fake_publish)
    monkeypatch.setattr(route_mod, "publish", fake_publish)
    return got


@pytest.fixture
async def blog_id(seeded) -> int:
    return (
        await seeded.execute(select(Source.id).where(Source.name == "Sonatype Blog"))
    ).scalar_one()


@pytest.fixture
def run_daily(serial, cfg, make_polite_client, blog_id, smtp, events):
    async def _run(llm=None, *, settings=None, checkpointer=None, **kw):
        return await run_daily_intel(
            serial,
            trigger="manual",
            source_ids=[blog_id],
            http_factory=make_polite_client,
            llm=llm,
            checkpointer=checkpointer or MemorySaver(),
            settings=settings or cfg(),
            **kw,
        )

    return _run


async def rows(serial, stmt) -> list:
    async with serial() as s:
        return list((await s.execute(stmt)).scalars().all())


async def one(serial, model, pk):
    async with serial() as s:
        return await s.get(model, pk)


def local_today(settings) -> str:
    return datetime.now(ZoneInfo(settings.timezone)).date().isoformat()


def tasks_of(llm) -> Counter:
    return Counter(task for task, _kind in llm.backend.calls)


FUNNEL_KEYS = (
    "fetched",
    "documents_created",
    "prefilter_passed",
    "triaged",
    "relevant",
    "signals_new",
    "signals_analyzed",
    "signals_deep_analyzed",
    "postings_tagged",
    "page_changes",
    "facts_extracted",
    "signals_breaking",
    "briefing_status",
)


def funnel_of(stats: dict) -> dict:
    return {k: stats.get(k) for k in FUNNEL_KEYS}


# ---- end to end --------------------------------------------------------------------------


async def test_a_daily_runs_stats_carry_the_postings_and_intel_counts(
    serial, fake_collectors, run_daily, make_llm
):
    """The runs page reads a daily run's job-board counts (postings_new,
    postings_closed: the ingest stats) beside the funnel's intel counts (postings_tagged,
    page_changes, facts_extracted), all from PipelineRun.stats, as RunOut.stats documents."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    result = await run_daily(make_llm(daily_script()))
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    keys = ("postings_new", "postings_closed", "postings_tagged", "page_changes", "facts_extracted")
    assert {k: run.stats.get(k) for k in keys} == dict.fromkeys(keys, 0)
    from app.schemas.runs import RunOut

    described = RunOut.model_fields["stats"].description or ""
    assert all(k in described for k in keys), described


async def test_daily_run_end_to_end_produces_signals_and_briefings(
    serial, fake_collectors, run_daily, make_llm, cfg, smtp, events
):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    settings = cfg(slack_webhook_url=HOOK)
    llm = make_llm(daily_script(), settings)
    with respx.mock:
        hook = respx.post(HOOK).mock(return_value=httpx.Response(200))
        result = await run_daily(llm, settings=settings)

    assert isinstance(result, DailyResult) and result.status == "succeeded"

    # 3 items, 2 of which are the same story -> 2 signals, analyzed, verified and scored
    signals = await rows(serial, select(Signal).order_by(Signal.id))
    assert len(signals) == 2
    for sig in signals:
        assert sig.status == "auto" and sig.analyzed_at is not None
        assert sig.importance > 0 and "components" in sig.score_breakdown
        assert sig.agent_run_id == result.agent_run_id
    members = Counter(await rows(serial, select(SignalDocument.signal_id)))
    assert sorted(members.values()) == [1, 2]
    docs = await rows(serial, select(Document))
    assert {d.status for d in docs} == {"relevant"} and all(d.story_id for d in docs)
    tasks = tasks_of(llm)
    assert tasks["triage"] == 3 and tasks["disambiguate"] == 2  # the unsure doc: turn + final
    assert tasks["story_judge"] == 0  # the shared story clears the join threshold outright

    # master + 4 role editions, all published, filed under the local date
    briefings = await rows(serial, select(Briefing).order_by(Briefing.id))
    assert sorted(b.edition for b in briefings) == ["exec", "master", "pm", "pmm_devrel", "sales"]
    assert {(b.status, b.version) for b in briefings} == {("published", 1)}
    assert {b.date.isoformat() for b in briefings} == {local_today(settings)}
    assert sorted(result.briefing_ids) == [b.id for b in briefings]
    master = next(b for b in briefings if b.edition == "master")

    expected = {
        "fetched": 3,
        "documents_created": 3,
        "prefilter_passed": 3,
        "triaged": 3,
        "relevant": 3,
        "signals_new": 2,
        "signals_analyzed": 2,
        "signals_deep_analyzed": 2,
        "postings_tagged": 0,
        "page_changes": 0,
        "facts_extracted": 0,
        "signals_breaking": 0,
        "briefing_status": "published",
    }
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.kind == "daily" and run.trigger == "manual" and run.status == "succeeded"
    assert run.finished_at is not None and funnel_of(run.stats) == expected
    assert run.stats["sources_attempted"] == 1  # the ingest stats are kept alongside
    assert funnel_of(master.funnel) == expected

    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert agent_run.graph == "daily_intel" and agent_run.thread_id.startswith("daily-")
    assert agent_run.status == "succeeded" and agent_run.notes is None
    assert agent_run.pipeline_run_id == run.id and agent_run.finished_at is not None
    async with serial() as s:
        tokens_in, cost = (
            await s.execute(
                select(func.sum(AgentStep.tokens_in), func.sum(AgentStep.cost_usd)).where(
                    AgentStep.agent_run_id == agent_run.id
                )
            )
        ).one()
    assert agent_run.tokens_in == tokens_in > 0 and agent_run.cost_usd == cost > 0

    # one email per edition; analysts get the master
    assert len(smtp.sent) == 5
    by_to = {m["To"]: m for m in smtp.sent}
    assert set(by_to) == set(PERSONAS)
    day = local_today(settings)
    assert by_to["analyst@frogeye.local"]["Subject"] == (
        f"FrogEye · {day} · {master.content['headline']}"
    )
    sales_html = by_to["sales@frogeye.local"].get_body(("html",)).get_content()
    assert "sales headline" in sales_html
    assert all(m["From"] == settings.briefing_from for m in smtp.sent)

    # Slack: headline, top titles and the app link
    assert hook.call_count == 1
    posted = hook.calls[0].request.content.decode()
    assert master.content["headline"] in posted and f"localhost:8080/briefing/{day}" in posted

    stages = [p["stage"] for t, p in events if t == "run.progress"]
    assert stages == list(NODES)
    progress = [p for t, p in events if t == "run.progress"]
    assert all(p["pipeline_run_id"] == run.id for p in progress)
    assert progress[-1]["counts"]["briefing_status"] == "published"
    assert ("briefing.published", {"date": day, "status": "published"}) in events


async def test_daily_run_without_key_only_ingests(
    serial, fake_collectors, run_daily, cfg, smtp, events
):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    result = await run_daily(None, settings=cfg(openai_api_key=None))

    assert result.status == "succeeded" and result.briefing_ids == []
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert agent_run.status == "succeeded" and agent_run.notes == "llm_disabled"
    docs = await rows(serial, select(Document))
    assert len(docs) == 3
    assert all(d.status == "new" and d.prefilter and d.embedding is None for d in docs)
    assert await rows(serial, select(Signal)) == []
    assert await rows(serial, select(Briefing)) == []
    assert await rows(serial, select(LlmCall)) == []
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.status == "succeeded"
    assert funnel_of(run.stats) == {
        "fetched": 3,
        "documents_created": 3,
        "prefilter_passed": 3,
        "triaged": 0,
        "relevant": 0,
        "signals_new": 0,
        "signals_analyzed": 0,
        "signals_deep_analyzed": 0,
        "postings_tagged": 0,
        "page_changes": 0,
        "facts_extracted": 0,
        "signals_breaking": 0,
        "briefing_status": None,
    }
    assert smtp.sent == []
    assert [p["stage"] for t, p in events if t == "run.progress"] == ["ingest", "publish"]


async def test_budget_exhaustion_mid_run_is_graceful(
    serial, fake_collectors, run_daily, make_llm, cfg, smtp
):
    """The budget covers indexing and triage but not analysis: the run still finishes, the
    signals wait (pending) for the next run and the briefing is skipped_budget."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    # Each triage call costs exactly $0.04 on gpt-5-nano; the third one reaches the cap.
    tight = cfg(model_fast="gpt-5-nano", llm_daily_budget_usd=0.12)

    def pricey_triage(*, user, model, **_):
        out = {**_triage_out(user), "confidence": 0.9}  # no disambiguation needed
        return StructuredOut(value=out, usage=Usage(800_000, 0, 0), model=model)

    llm = make_llm(daily_script(triage=pricey_triage), tight)
    result = await run_daily(llm, settings=tight)

    assert result.status == "succeeded"
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert agent_run.status == "succeeded" and "budget_stopped" in agent_run.notes
    tasks = tasks_of(llm)
    assert tasks["triage"] == 3
    assert not {"analyst", "editor", "section_writer", "fact_checker"} & set(tasks)

    signals = await rows(serial, select(Signal))
    assert len(signals) == 2
    assert all(s.status == "pending" and s.analyzed_at is None for s in signals)
    briefings = await rows(serial, select(Briefing))
    assert [(b.edition, b.status, b.published_at) for b in briefings] == [
        ("master", "skipped_budget", None)
    ]
    assert result.briefing_ids == [briefings[0].id]
    assert smtp.sent == []  # nothing is mailed for a skipped briefing
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.status == "succeeded"
    assert run.stats["briefing_status"] == "skipped_budget"
    assert run.stats["signals_analyzed"] == 0 and run.stats["triaged"] == 3
    notes = await rows(
        serial,
        select(AgentStep.result_summary).where(
            AgentStep.agent_run_id == result.agent_run_id,
            AgentStep.node == "analyze",
            AgentStep.kind == "node",
        ),
    )
    assert "budget: daily" in notes

    # The next run (budget available again) picks the carried-over signals up.
    llm2 = make_llm(daily_script())
    again = await run_daily(llm2)
    assert again.status == "succeeded" and tasks_of(llm2)["triage"] == 0
    signals = await rows(serial, select(Signal))
    assert all(s.status == "auto" and s.analyzed_at is not None for s in signals)
    latest = await rows(
        serial,
        select(Briefing.status)
        .where(Briefing.edition == "master")
        .order_by(Briefing.version.desc())
        .limit(1),
    )
    assert latest == ["published"]


async def test_budget_spent_before_triage_leaves_documents_new(
    serial, fake_collectors, run_daily, make_llm, cfg
):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    broke = cfg(llm_daily_budget_usd=0.0)
    llm = make_llm(daily_script(), broke)
    result = await run_daily(llm, settings=broke)

    assert result.status == "succeeded"
    assert llm.backend.calls == []  # the first budget check (indexing) stops everything
    docs = await rows(serial, select(Document))
    assert len(docs) == 3 and {d.status for d in docs} == {"new"}  # left for the next run
    assert await rows(serial, select(Signal)) == []
    assert [b.status for b in await rows(serial, select(Briefing))] == ["skipped_budget"]
    # After the stop, later LLM nodes are skipped outright: only index_new hit the budget.
    async with serial() as s:
        stops = (
            await s.execute(
                select(AgentStep.node, AgentStep.result_summary).where(
                    AgentStep.agent_run_id == result.agent_run_id,
                    AgentStep.kind == "node",
                    AgentStep.result_summary.like("budget%"),
                )
            )
        ).all()
    assert [tuple(r) for r in stops] == [("index_new", "budget: daily")]


async def test_budget_stop_in_triage_leaves_unjudged_documents_new(
    serial, fake_collectors, run_daily, make_llm, cfg, monkeypatch
):
    """An unsure (flagged) document triaged just before the stop is never disambiguated, so
    it is not persisted: it stays `new` and the next run triages and disambiguates it."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    monkeypatch.setattr(daily_mod, "TRIAGE_CONCURRENCY", 1)  # the newest (unsure) doc first
    tight = cfg(model_fast="gpt-5-nano", llm_daily_budget_usd=0.04)  # one triage call fits

    def pricey_triage(*, user, model, **_):
        return StructuredOut(value=_triage_out(user), usage=Usage(800_000, 0, 0), model=model)

    llm = make_llm(daily_script(triage=pricey_triage), tight)
    result = await run_daily(llm, settings=tight)

    assert result.status == "succeeded"
    tasks = tasks_of(llm)
    assert tasks["triage"] == 1 and tasks["disambiguate"] == 0
    docs = {d.title: d for d in await rows(serial, select(Document))}
    assert {d.status for d in docs.values()} == {"new"}
    assert docs[PRICING].triage is None  # flagged, never judged: not persisted

    llm2 = make_llm(daily_script())  # the next run, with budget again
    await run_daily(llm2)
    assert tasks_of(llm2)["disambiguate"] == 2  # turn + final, for the unsure doc
    priced = (await rows(serial, select(Document).where(Document.title == PRICING)))[0]
    assert priced.status == "relevant" and priced.triage["confidence"] == 0.95


async def test_briefing_filed_under_local_date_near_midnight(
    serial, fake_collectors, run_daily, make_llm, cfg, smtp, events
):
    """22:30 UTC is already the next day in Asia/Jerusalem: the briefing is filed under the
    local date, never the UTC one."""
    # Frozen 2-3 days before the real clock (still 22:30 UTC), so any real-clock read
    # (`date.today()`, `datetime.now(tz).date()`) lands on a different date than the
    # injected clock's local date, whatever time of day the suite runs.
    real = datetime.now(UTC)
    frozen = real.replace(hour=22, minute=30, second=0, microsecond=0) - timedelta(days=2)
    if frozen > real - timedelta(days=2):
        frozen -= timedelta(days=1)
    assert real - frozen >= timedelta(days=2)
    settings = cfg()
    local = frozen.astimezone(ZoneInfo(settings.timezone)).date()
    assert local == frozen.date() + timedelta(days=1)
    fake_collectors(
        {
            "Sonatype Blog": FetchResult(
                items=[
                    item(1, GATEWAY_A, cluster="gateway", hours_ago=3, before=frozen),
                    item(2, GATEWAY_B, cluster="gateway", hours_ago=2, before=frozen),
                    item(3, PRICING, hours_ago=1, before=frozen),
                ]
            )
        }
    )
    result = await run_daily(make_llm(daily_script()), settings=settings, now=lambda: frozen)

    briefings = await rows(serial, select(Briefing))
    assert len(briefings) == 5 and {b.status for b in briefings} == {"published"}
    assert {b.date for b in briefings} == {local}
    assert ("briefing.published", {"date": local.isoformat(), "status": "published"}) in events
    assert all(f"FrogEye · {local.isoformat()} · " in m["Subject"] for m in smtp.sent)
    assert result.status == "succeeded"


async def test_newsroom_crash_files_a_failed_briefing(
    serial, fake_collectors, run_daily, make_llm, smtp, monkeypatch
):
    """An unexpected (non-LLM) error in the newsroom node files the `failed` master row,
    so the briefing page shows the failure instead of yesterday's edition."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})

    async def broken(*a, **k):
        raise RuntimeError("selection query failed")

    monkeypatch.setattr(daily_mod, "run_newsroom", broken)
    result = await run_daily(make_llm(daily_script()))

    assert result.status == "succeeded"
    briefings = await rows(serial, select(Briefing))
    assert [(b.edition, b.status, b.published_at) for b in briefings] == [
        ("master", "failed", None)
    ]
    assert briefings[0].content["headline"] == FAILED_HEADLINE
    assert smtp.sent == []
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["briefing_status"] == "failed"
    notes = (await one(serial, AgentRun, result.agent_run_id)).notes
    assert "newsroom: RuntimeError: selection query failed" in notes


async def test_failed_ingest_still_records_its_stats(
    serial, fake_collectors, run_daily, make_llm, monkeypatch
):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})

    async def boom(*a, **k):
        raise RuntimeError("normalize exploded")

    monkeypatch.setattr(ingest_mod, "normalize_new_raw_items", boom)
    with pytest.raises(RuntimeError, match="normalize exploded"):
        await run_daily(make_llm(daily_script()))

    (run,) = await rows(serial, select(PipelineRun))
    assert run.status == "failed" and "normalize exploded" in run.error
    assert run.stats["fetched"] == 3 and run.stats["sources_attempted"] == 1  # collect done
    assert run.stats["triaged"] == 0 and run.stats["briefing_status"] is None
    assert run.stats["errors"] == ["ingest: RuntimeError: normalize exploded"]
    (agent_run,) = await rows(serial, select(AgentRun))
    assert agent_run.status == "failed"


async def test_lock_released_when_a_run_cannot_start(session, run_daily, monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(runner_mod, "_start", broken)
    with pytest.raises(RuntimeError, match="db hiccup"):
        await run_daily(None)
    assert not await ingestion_lock_held(session)


async def test_second_run_same_day_upserts_briefing_and_reuses_stories(
    serial, fake_collectors, run_daily, make_llm
):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    first = await run_daily(make_llm(daily_script()))
    before = {s.id for s in await rows(serial, select(Signal))}
    assert len(before) == 2

    # Same feed later the same day: the old items again plus one more on the gateway story.
    later = [*ITEMS, item(4, GATEWAY_C, cluster="gateway", hours_ago=0.5)]
    fake_collectors({"Sonatype Blog": FetchResult(items=later)})
    llm2 = make_llm(daily_script())
    second = await run_daily(llm2)

    assert {s.id for s in await rows(serial, select(Signal))} == before  # no duplicate signals
    new_doc = (await rows(serial, select(Document).where(Document.title == GATEWAY_C)))[0]
    gateway = (
        await rows(
            serial,
            select(Document.story_id).where(Document.title == GATEWAY_A),
        )
    )[0]
    assert new_doc.story_id == gateway  # joined the existing story
    tasks = tasks_of(llm2)
    assert tasks["triage"] == 1 and tasks["analyst"] == 0  # only the new doc is new work

    async with serial() as s:
        pairs = (await s.execute(select(Briefing.edition, Briefing.version))).all()
    by_edition: dict[str, list[int]] = {}
    for edition, version in pairs:
        by_edition.setdefault(edition, []).append(version)
    assert {e: sorted(v) for e, v in by_edition.items()} == {
        e: [1, 2] for e in ("master", "sales", "pm", "exec", "pmm_devrel")
    }
    run2 = await one(serial, PipelineRun, second.pipeline_run_id)
    assert run2.stats["signals_new"] == 0 and run2.stats["relevant"] == 1
    assert first.agent_run_id != second.agent_run_id

    # each version carries its DAY's funnel, the sum over the runs that fed it.
    run1 = await one(serial, PipelineRun, first.pipeline_run_id)
    v1, v2 = await rows(
        serial,
        select(Briefing).where(Briefing.edition == "master").order_by(Briefing.version),
    )
    assert v1.funnel["runs"] == 1 and v2.funnel["runs"] == 2
    for key in (
        "fetched",
        "triaged",
        "relevant",
        "signals_new",
        "signals_analyzed",
        "signals_deep_analyzed",
    ):
        assert v1.funnel[key] == run1.stats[key]
        assert v2.funnel[key] == run1.stats[key] + run2.stats[key], key
    assert v2.funnel["signals_new"] == 2  # the morning's two, not the re-run's zero


async def test_run_record_carries_the_funnel_at_every_stage(
    serial, fake_collectors, run_daily, make_llm, monkeypatch
):
    """Every stage writes its counts to the run record before announcing them,
    so a poll of the run and the live event agree; the counts build up stage by stage."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    seen: list[tuple[str, dict, dict]] = []

    async def watching(event_type, payload):
        if event_type == "run.progress":
            async with serial() as s:
                run = await s.get(PipelineRun, payload["pipeline_run_id"])
                stored = {k: (run.stats or {}).get(k) for k in payload["counts"]}
            seen.append((payload["stage"], stored, dict(payload["counts"])))
        return True

    monkeypatch.setattr(daily_mod, "publish_event", watching)
    await run_daily(make_llm(daily_script()))

    assert [stage for stage, _, _ in seen] == list(NODES)
    for stage, stored, sent in seen:
        assert stored == sent, stage
    by_stage = {stage: stored for stage, stored, _ in seen}
    assert by_stage["ingest"]["fetched"] == 3 and by_stage["ingest"]["triaged"] == 0
    assert by_stage["triage"]["triaged"] == 3
    assert by_stage["cluster"]["signals_new"] == 2
    assert by_stage["analyze"]["signals_analyzed"] == 2
    assert by_stage["score"]["signals_deep_analyzed"] == 0
    assert by_stage["deep_analyze"]["signals_deep_analyzed"] == 2


async def test_steps_name_the_triage_disambiguation_and_analyst_agents(
    serial, fake_collectors, run_daily, make_llm
):
    """The busiest nodes' steps carry their agent, so the trace never says Pipeline."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    result = await run_daily(make_llm(daily_script()))
    async with serial() as s:
        found = (
            await s.execute(
                select(AgentStep.node, AgentStep.agent).where(
                    AgentStep.agent_run_id == result.agent_run_id
                )
            )
        ).all()
    agents: dict[str, set] = {}
    for node, agent in found:
        agents.setdefault(node, set()).add(agent)
    assert agents["triage"] == {"triage"}
    assert agents["disambiguate"] == {"disambiguate"}
    assert "analyst" in agents["analyze"] and agents["analyze"] <= {"analyst", "embed"}


async def test_every_run_refreshes_the_recency_of_recent_signals(
    serial, seeded, fake_collectors, run_daily, make_llm, make_signal
):
    """The score node re-scores auto/approved/needs_review signals first seen
    within the analysis lookback, so a days-old score decays and its breakdown says its age."""
    now = datetime.now(UTC)
    frozen = {"age_days": 0.0, "decay": 1.0, "importance": 80}

    async def stale(**kw):
        return await make_signal(
            importance=80, impact_score=5, novelty_score=5, score_breakdown=dict(frozen), **kw
        )

    aging = await stale(first_seen_at=now - timedelta(days=5), status="auto")
    review = await stale(first_seen_at=now - timedelta(days=4), status="needs_review")
    ancient = await stale(first_seen_at=now - timedelta(days=20))  # outside the lookback
    dropped = await stale(first_seen_at=now - timedelta(days=5), status="rejected")

    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    await run_daily(make_llm(daily_script()))

    for sig, days in ((aging, 5), (review, 4)):
        fresh = await one(serial, Signal, sig.id)
        assert abs(fresh.score_breakdown["age_days"] - days) < 0.1
        assert fresh.score_breakdown["decay"] < 0.4
        assert fresh.importance == fresh.score_breakdown["importance"] < 40
    for sig in (ancient, dropped):
        kept = await one(serial, Signal, sig.id)
        assert kept.importance == 80 and kept.score_breakdown == frozen


async def test_rescore_loop_survives_bad_signal(
    serial, seeded, fake_collectors, run_daily, make_llm, make_signal, caplog
):
    """The re-score is per signal: one old signal whose scoring raises (here a legacy
    score_breakdown that is a list, which the merge cannot spread) is logged and skipped,
    with nothing of it written, while the signals before and after it, and the run's own,
    are still scored and committed."""
    now = datetime.now(UTC)
    frozen = {"age_days": 0.0, "decay": 1.0, "importance": 80}

    async def recent(days, breakdown):
        return await make_signal(
            importance=80,
            impact_score=5,
            novelty_score=5,
            score_breakdown=breakdown,
            first_seen_at=now - timedelta(days=days),
            status="auto",
        )

    before = await recent(5, dict(frozen))
    bad = await recent(4, ["legacy"])
    after = await recent(5, dict(frozen))
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})

    with caplog.at_level("WARNING"):
        result = await run_daily(make_llm(daily_script()))

    assert result.status == "succeeded"
    for sig in (before, after):
        fresh = await one(serial, Signal, sig.id)
        assert abs(fresh.score_breakdown["age_days"] - 5) < 0.1, sig.id
        assert fresh.importance == fresh.score_breakdown["importance"] < 40, sig.id
    kept = await one(serial, Signal, bad.id)
    assert (kept.importance, kept.score_breakdown) == (80, ["legacy"])  # nothing half-written
    old = {before.id, bad.id, after.id}
    new = [s for s in await rows(serial, select(Signal)) if s.id not in old]  # the run's own
    assert new and all("components" in s.score_breakdown for s in new)
    assert any(f"signal {bad.id}" in r.getMessage() for r in caplog.records)
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert f"score signal {bad.id}: TypeError" in agent_run.notes


async def test_failed_re_scores_are_one_counted_line_in_the_notes(
    serial, seeded, fake_collectors, run_daily, make_llm, make_signal
):
    """Every signal whose re-score fails is logged by id, but the run's notes
    (capped at 2000 characters) get ONE line per node saying how many and the first, so a
    systematic scoring bug cannot crowd out the later nodes' errors."""
    now = datetime.now(UTC)
    bad = [
        await make_signal(
            importance=80,
            impact_score=5,
            novelty_score=5,
            score_breakdown=["legacy"],
            first_seen_at=now - timedelta(days=4),
            status="auto",
        )
        for _ in range(12)
    ]
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    result = await run_daily(make_llm(daily_script()))
    assert result.status == "succeeded"
    notes = (await one(serial, AgentRun, result.agent_run_id)).notes
    assert notes.count("score signal") == 1, notes
    assert f"score: {len(bad)} signals not scored" in notes
    assert f"first: score signal {bad[0].id}: TypeError" in notes


async def test_route_delivers_alerts_outside_any_session(
    serial, fake_collectors, run_daily, make_llm, monkeypatch
):
    """Breaking alerts go out (Redis, Slack: up to 5 s each) with no DB
    transaction open; alerted_at is marked afterwards in a short session of its own."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    monkeypatch.setattr(route_mod, "BREAKING_THRESHOLD", 0)  # every touched signal breaks
    monkeypatch.setattr(daily_mod, "BREAKING_THRESHOLD", 0)
    monkeypatch.setattr(SerialSessions, "WAIT_S", 2)  # a held session fails fast
    alerted: list[int] = []

    async def deliver(event_type, payload):
        if event_type == "signal.breaking":
            async with serial() as s:  # waits (and fails) while the route node holds one
                assert await s.get(Signal, payload["signal_id"]) is not None
            alerted.append(payload["signal_id"])
        return True

    monkeypatch.setattr(route_mod, "publish", deliver)
    result = await run_daily(make_llm(daily_script()))
    assert result.status == "succeeded" and len(alerted) == 2
    for sid in alerted:
        assert (await one(serial, Signal, sid)).score_breakdown["alerted_at"]


async def test_concurrent_daily_run_rejected(engine, serial, run_daily, make_llm):
    async with engine.connect() as other:
        await other.execute(text("select pg_advisory_lock(:k)"), {"k": INGEST_LOCK_KEY})
        try:
            with pytest.raises(RunAlreadyActive):
                await run_daily(make_llm(daily_script()))
        finally:
            await other.execute(text("select pg_advisory_unlock(:k)"), {"k": INGEST_LOCK_KEY})
            await other.rollback()
    assert await rows(serial, select(PipelineRun)) == []
    assert await rows(serial, select(AgentRun)) == []


async def test_publish_failures_do_not_fail_run(
    serial, fake_collectors, run_daily, make_llm, cfg, smtp, events, monkeypatch, caplog
):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    smtp.down = True

    async def bus_down(event_type, payload):
        return False

    monkeypatch.setattr(publish_mod, "publish_event", bus_down)
    settings = cfg(slack_webhook_url=HOOK)
    with respx.mock, caplog.at_level("DEBUG"):
        respx.post(HOOK).mock(side_effect=httpx.ConnectError("slack is down"))
        result = await run_daily(make_llm(daily_script(), settings), settings=settings)

    assert result.status == "succeeded"
    briefings = await rows(serial, select(Briefing))
    assert len(briefings) == 5 and {b.status for b in briefings} == {"published"}
    assert smtp.sent == []
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert "email" in agent_run.notes and "slack" in agent_run.notes
    assert "SECRETTOKEN" not in caplog.text and "hooks.slack.test" not in caplog.text
    assert "SECRETTOKEN" not in agent_run.notes


# ---- resume ------------------------------------------------------------------------


async def test_resume_interrupted_run_uses_same_thread(
    serial, fake_collectors, run_daily, make_llm
):
    fc = fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    saver = MemorySaver()

    def dies(**_):
        raise Killed()

    llm1 = make_llm(daily_script(fact_checker=dies))  # dies inside the newsroom
    with pytest.raises(Killed):
        await run_daily(llm1, checkpointer=saver)
    agent_run = (await rows(serial, select(AgentRun)))[0]
    assert agent_run.status == "interrupted"
    assert (await one(serial, PipelineRun, agent_run.pipeline_run_id)).status == "failed"
    assert ("editor", "tool_turn") in llm1.backend.calls
    assert await rows(serial, select(Briefing)) == []

    llm2 = make_llm(daily_script())
    result = await run_daily(llm2, checkpointer=saver, resume=True)

    assert result.agent_run_id == agent_run.id
    assert result.pipeline_run_id == agent_run.pipeline_run_id
    resumed = await one(serial, AgentRun, agent_run.id)
    assert resumed.status == "succeeded" and resumed.thread_id == agent_run.thread_id
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.status == "succeeded" and run.error is None
    # Finished nodes come back from the checkpoint: nothing is collected or triaged again.
    assert len(fc.seen) == 1
    tasks = tasks_of(llm2)
    assert tasks["triage"] == 0 and tasks["analyst"] == 0
    # The newsroom subgraph has no checkpointer of its own: it re-runs whole.
    assert ("editor", "tool_turn") in llm2.backend.calls
    briefings = await rows(serial, select(Briefing))
    assert len(briefings) == 5 and {b.version for b in briefings} == {1}
    assert len(await rows(serial, select(AgentRun))) == 1


async def test_resumed_publish_neither_refiles_nor_resends(
    serial, fake_collectors, run_daily, make_llm, smtp
):
    """The worker dies mid-publish (after filing, during email). The resumed publish
    finds this agent run's briefings and neither files a new version nor emails again."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    saver = MemorySaver()
    smtp.die_after = 1
    with pytest.raises(Killed):
        await run_daily(make_llm(daily_script()), checkpointer=saver)
    assert len(smtp.sent) == 1
    assert len(await rows(serial, select(Briefing))) == 5  # filed before the death

    smtp.die_after = None
    result = await run_daily(make_llm(daily_script()), checkpointer=saver, resume=True)

    assert result.status == "succeeded"
    briefings = await rows(serial, select(Briefing))
    assert len(briefings) == 5 and {b.version for b in briefings} == {1}
    assert sorted(result.briefing_ids) == sorted(b.id for b in briefings)
    assert len(smtp.sent) == 1  # announced at most once


async def test_resume_needs_a_recent_interrupted_run(
    serial, session, fake_collectors, run_daily, make_llm
):
    fake_collectors({"Sonatype Blog": FetchResult(items=[])})
    with pytest.raises(NothingToResume):
        await run_daily(make_llm(daily_script()), resume=True)

    run = PipelineRun(kind="daily", trigger="manual", status="failed", stats={})
    session.add(run)
    await session.flush()
    old = AgentRun(
        graph="daily_intel",
        thread_id="daily-old",
        trigger="manual",
        status="interrupted",
        pipeline_run_id=run.id,
        started_at=datetime.now(UTC) - timedelta(hours=7),
    )
    session.add(old)
    await session.flush()
    with pytest.raises(NothingToResume):  # older than the 6 h resume window
        await run_daily(make_llm(daily_script()), resume=True)
    await session.execute(
        update(AgentRun)
        .where(AgentRun.id == old.id)
        .values(status="succeeded", started_at=datetime.now(UTC) - timedelta(hours=1))
    )
    with pytest.raises(NothingToResume):  # recent, but it finished
        await run_daily(make_llm(daily_script()), resume=True)
    assert await rows(serial, select(PipelineRun).where(PipelineRun.kind == "daily")) != []
    assert len(await rows(serial, select(AgentRun))) == 1  # nothing new was started


async def test_finished_runs_checkpoints_are_pruned_after_the_resume_window(
    serial, fake_collectors, run_daily, make_llm
):
    """A run older than the 6 h resume window can never be resumed, so its LangGraph
    checkpoint thread is deleted by a later run; a recent run's thread is kept."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    saver = MemorySaver()
    first = await run_daily(make_llm(daily_script()), checkpointer=saver)
    first_thread = (await one(serial, AgentRun, first.agent_run_id)).thread_id
    assert await saver.aget_tuple({"configurable": {"thread_id": first_thread}}) is not None

    async with serial() as s:
        await s.execute(
            update(AgentRun)
            .where(AgentRun.id == first.agent_run_id)
            .values(started_at=datetime.now(UTC) - timedelta(hours=7))
        )
        await s.commit()
    second = await run_daily(make_llm(daily_script()), checkpointer=saver)
    second_thread = (await one(serial, AgentRun, second.agent_run_id)).thread_id
    assert await saver.aget_tuple({"configurable": {"thread_id": first_thread}}) is None
    assert await saver.aget_tuple({"configurable": {"thread_id": second_thread}}) is not None


async def test_new_run_marks_a_dead_workers_run_interrupted(
    serial, session, fake_collectors, run_daily
):
    fake_collectors({"Sonatype Blog": FetchResult(items=[])})
    stale_run = PipelineRun(kind="daily", trigger="schedule", status="running", stats={})
    session.add(stale_run)
    await session.flush()
    stale = AgentRun(
        graph="daily_intel",
        thread_id="daily-dead",
        trigger="schedule",
        status="running",
        pipeline_run_id=stale_run.id,
    )
    session.add(stale)
    await session.flush()
    await run_daily(None)
    assert (await one(serial, AgentRun, stale.id)).status == "interrupted"
    dead = await one(serial, PipelineRun, stale_run.id)
    assert dead.status == "failed" and "interrupted" in dead.error


# ---- pieces ------------------------------------------------------------------------------


def test_graph_topology():
    graph = build_daily_graph(None).get_graph()
    assert set(NODES) <= set(graph.nodes)
    edges = {(e.source, e.target) for e in graph.edges}
    chain = set(zip(NODES[1:-1], NODES[2:], strict=True))
    assert ("ingest", "index_new") in edges and ("ingest", "publish") in edges  # no-key path
    assert chain <= edges
    assert "newsroom" in graph.draw_mermaid()
    assert NODES[NODES.index("score") + 1 : NODES.index("score") + 4] == (
        "deep_analyze",
        "extract_facts",
        "route",
    )  # facts after the analyses, before the briefing
    assert ("score", "deep_analyze") in edges and ("deep_analyze", "extract_facts") in edges
    assert ("extract_facts", "route") in edges
    assert NODES[NODES.index("index_new") + 1] == "tag_postings"
    assert ("index_new", "tag_postings") in edges
    assert NODES[NODES.index("tag_postings") + 1 : NODES.index("tag_postings") + 3] == (
        "judge_pages",
        "triage",
    )
    assert ("tag_postings", "judge_pages") in edges and ("judge_pages", "triage") in edges


async def test_analysis_ranked_by_provisional_importance(seeded, serial, make_signal):
    weak = await make_signal(status="pending", signal_type="other", scope="industry")
    strong = await make_signal(
        status="pending", signal_type="acquisition", competitors=["sonatype"]
    )
    middle = await make_signal(status="pending", signal_type="feature_update", scope="industry")
    ranked = await rank_for_analysis(serial, [weak.id, strong.id, middle.id])
    assert ranked == [strong.id, middle.id, weak.id]
    async with serial() as s:  # ranking never persists the provisional scores
        assert (await s.get(Signal, strong.id)).impact_score is None


def _content(**kw) -> BriefingContent:
    data = {
        "headline": "Rivals move on <b>price</b>",
        "lead": "A calm lead & a plan.",
        "top_takeaways": [
            {
                "title": "Price gap",
                "so_what": "Deals get harder.",
                "now_what": "",
                "signal_ids": [1],
                "confidence": "high",
            },
            {
                "title": "Registry race",
                "so_what": "Models need governance.",
                "now_what": "Ask about model provenance.",
                "signal_ids": [2],
                "confidence": "medium",
            },
        ],
        "sections": [
            {
                "theme": "Competitive moves",
                "items": [
                    {"signal_id": 1, "one_liner": "<script>x</script>", "why_it_matters": "Y"}
                ],
            }
        ],
        "quiet_competitors": ["gitlab"],
        "watchlist": [],
    }
    data.update(kw)
    return BriefingContent.model_validate(data)


def test_email_is_escaped_and_hides_empty_now_what():

    subject, html, plain = render_email(
        _content(), day=date(2026, 10, 18), edition="sales", names={"gitlab": "GitLab"}
    )
    assert subject == "FrogEye · 2026-10-18 · Rivals move on <b>price</b>"
    assert "<script>" not in html and "&lt;script&gt;" in html  # autoescape on
    assert "&lt;b&gt;price&lt;/b&gt;" in html
    assert html.count("Now what") == 1  # the empty now_what is hidden
    assert "Ask about model provenance." in html and "GitLab" in html
    assert "http://localhost:8080/briefing/2026-10-18" in html
    assert "#40BE46" in html.upper() or "#1C6D26" in html.upper()
    assert "Ask about model provenance." in plain and "Now what: \n" not in plain


async def test_each_recipient_gets_their_own_email(seeded, serial, session, cfg, smtp, events):
    """No shared To: list exposing every address of a role."""
    session.add(
        User(
            name="Second Analyst",
            email="analyst2@frogeye.local",
            role="analyst",
            password_hash="not-a-hash",
        )
    )
    await session.flush()
    quiet = _content(headline="No significant moves", top_takeaways=[], sections=[])
    result = NewsroomResult(
        master=quiet,
        editions={r: quiet.model_copy(deep=True) for r in EDITION_ROLES},
        signal_ids=[],
        status="quiet",
    )
    out = await publish_briefings(
        serial, cfg(), result, day=date(2026, 10, 18), agent_run_id=None, funnel={}
    )
    assert out.emails_sent == 6 and out.errors == []
    assert sorted(m["To"] for m in smtp.sent) == sorted([*PERSONAS, "analyst2@frogeye.local"])
    assert all(m["Cc"] is None and m["Bcc"] is None for m in smtp.sent)
    analysts = [m for m in smtp.sent if m["To"].startswith("analyst")]
    assert len({m["Subject"] for m in analysts}) == 1  # both read the master


def test_email_subject_is_one_line():

    subject, _, _ = render_email(
        _content(headline="Two\nlines"), day=date(2026, 10, 18), edition="master", names={}
    )
    assert subject == "FrogEye · 2026-10-18 · Two lines"


def test_slack_text_escapes_and_links():

    msg = slack_text(_content(), day=date(2026, 10, 18))
    assert "&lt;b&gt;price&lt;/b&gt;" in msg and "<b>" not in msg
    assert "Price gap" in msg and "Registry race" in msg
    assert "<http://localhost:8080/briefing/2026-10-18|" in msg


def test_email_and_slack_link_every_story_and_name_its_source():
    """Every item in the delivered email and Slack post references its data: a link
    to that story in the app and the source it rests on. The link opens the story's
    full analysis page, in the edition's role (the master's and Slack's in the reader's
    own). The app address is a setting (APP_URL), not a constant."""
    app = "https://frogeye.example.test"
    sources = {1: "ET Now via Google News", 2: "GitHub Changelog"}
    _, html, plain = render_email(
        _content(), day=date(2026, 10, 18), edition="sales", names={}, app_url=app, sources=sources
    )
    story = f"{app}/signals/1?edition=sales"
    assert f'href="{story}"' in html and "ET Now via Google News" in html
    assert f"{app}/signals/2?edition=sales" in html  # the takeaway's cited signal
    assert "GitHub Changelog" in html
    assert story in plain and "ET Now via Google News" in plain
    _, master, _ = render_email(_content(), day=date(2026, 10, 18), edition="master", names={})
    assert 'href="http://localhost:8080/signals/1"' in master
    msg = slack_text(_content(), day=date(2026, 10, 18), app_url=app)
    assert f"<{app}/signals/1|Price gap>" in msg
    assert f"<{app}/briefing/2026-10-18|Open the briefing>" in msg


async def test_a_rerun_announces_only_a_briefing_that_changed(
    seeded, serial, cfg, smtp, events, monkeypatch
):
    """A same-day re-run whose briefing carries the same signals files version 2 but
    does not email everyone (or post to Slack) a near-identical briefing again."""
    posts: list[str] = []

    async def post(url, text):
        posts.append(text)

    monkeypatch.setattr(publish_mod, "_post_slack", post)
    settings = cfg(slack_webhook_url=HOOK)
    content = _content()
    same = NewsroomResult(
        master=content,
        editions={r: content.model_copy(deep=True) for r in EDITION_ROLES},
        signal_ids=[1, 2],
        status="published",
    )
    day = date(2026, 10, 18)
    first = await publish_briefings(serial, settings, same, day=day, agent_run_id=None, funnel={})
    sent_first = len(smtp.sent)
    again = await publish_briefings(serial, settings, same, day=day, agent_run_id=None, funnel={})
    assert first.emails_sent == sent_first == 5 and len(posts) == 1
    assert again.briefing_ids and again.emails_sent == 0 and len(smtp.sent) == sent_first
    assert len(posts) == 1 and not again.slack_sent
    assert sum(1 for t, _ in events if t == "briefing.published") == 2  # the UI still refreshes

    grown = _content(watchlist=[{"signal_id": 3, "note": "Watch this"}])
    changed = NewsroomResult(
        master=grown,
        editions={r: grown.model_copy(deep=True) for r in EDITION_ROLES},
        signal_ids=[1, 2, 3],
        status="published",
    )
    third = await publish_briefings(
        serial, settings, changed, day=day, agent_run_id=None, funnel={}
    )
    assert third.emails_sent == 5 and len(posts) == 2


# ---- deep_analyze (stage B) -------------------------------------------------------

THREE_STORIES = [
    item(1, GATEWAY_A, cluster="gateway-a", hours_ago=3),
    item(2, GATEWAY_B, cluster="gateway-b", hours_ago=2),
    item(3, PRICING, hours_ago=1),
]


async def analyses(serial) -> list[SignalAnalysis]:
    return await rows(serial, select(SignalAnalysis).order_by(SignalAnalysis.id))


async def test_deep_analyze_runs_after_score_and_feeds_newsroom(
    serial, fake_collectors, run_daily, make_llm
):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    llm = make_llm(daily_script())
    result = await run_daily(llm)

    signals = await rows(serial, select(Signal))
    found = await analyses(serial)
    assert sorted(a.signal_id for a in found) == sorted(s.id for s in signals)
    assert {a.version for a in found} == {1}
    assert all(a.content["summary"]["text"] == SUMMARY for a in found)

    # Every analysis exists before the newsroom's first call (the ledger is in call order).
    ledger = await rows(serial, select(LlmCall.task).order_by(LlmCall.id))
    first_editor = ledger.index("editor")
    deep = [
        i for i, task in enumerate(ledger) if task in ("signal_analysis", "analysis_fact_check")
    ]
    assert len(deep) == 4 and max(deep) < first_editor
    assert tasks_of(llm)["signal_analysis"] == 2 and tasks_of(llm)["analysis_fact_check"] == 2

    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["signals_deep_analyzed"] == 2
    async with serial() as s:
        agents = set(
            await s.scalars(
                select(AgentStep.agent).where(
                    AgentStep.agent_run_id == result.agent_run_id,
                    AgentStep.node == "deep_analyze",
                )
            )
        )
        summaries = list(
            await s.scalars(
                select(AgentStep.result_summary).where(
                    AgentStep.agent_run_id == result.agent_run_id,
                    AgentStep.node == "deep_analyze",
                    AgentStep.kind == "node",
                )
            )
        )
    assert "signal_analyst" in agents
    assert "deep analysis: 2 analysed, 0 failed, 0 skipped" in summaries


async def test_deep_analyze_resume_no_duplicates(serial, fake_collectors, run_daily, make_llm):
    """The worker dies after one of three analyses; the resumed node writes
    only the other two and no signal ends with a second version."""
    fake_collectors({"Sonatype Blog": FetchResult(items=THREE_STORIES)})
    saver = MemorySaver()
    calls = {"n": 0}

    def dies_after_one(*, user, **req):
        calls["n"] += 1
        if calls["n"] > 1:
            raise Killed()
        return signal_analysis(user=user, **req)

    llm1 = make_llm(daily_script(signal_analysis=dies_after_one))
    with pytest.raises(Killed):
        await run_daily(llm1, checkpointer=saver)
    assert len(await analyses(serial)) == 1
    agent_run = (await rows(serial, select(AgentRun)))[0]
    assert agent_run.status == "interrupted"

    llm2 = make_llm(daily_script())
    result = await run_daily(llm2, checkpointer=saver, resume=True)

    found = await analyses(serial)
    assert len(found) == 3 and {a.version for a in found} == {1}
    assert len({a.signal_id for a in found}) == 3
    assert tasks_of(llm2)["signal_analysis"] == 2  # the finished one is not redone
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["signals_deep_analyzed"] == 3  # counts the work before the crash too


async def _deep_steps(serial, agent_run_id) -> list[str]:
    return await rows(
        serial,
        select(AgentStep.result_summary).where(
            AgentStep.agent_run_id == agent_run_id,
            AgentStep.node == "deep_analyze",
            AgentStep.kind == "node",
        ),
    )


async def test_deep_analyze_budget_stop(
    serial, fake_collectors, run_daily, make_llm, cfg, monkeypatch
):
    """A signal whose own calls cross the cap stops stage B with BudgetExceeded: the work
    before it is kept and the briefing is filed as skipped_budget."""
    fake_collectors({"Sonatype Blog": FetchResult(items=THREE_STORIES)})
    monkeypatch.setattr(daily_mod, "DEEP_ANALYZE_CONCURRENCY", 1)  # a deterministic order
    tight = cfg(llm_daily_budget_usd=0.13)
    calls = {"n": 0}

    def spends(*, user, schema, model, **_):  # the second analysis alone spends past the cap
        calls["n"] += 1
        value = schema.model_validate(signal_analysis(user=user))
        usage = Usage(2_000_000, 0, 0) if calls["n"] == 2 else Usage(100, 0, 20)
        return StructuredOut(value=value, usage=usage, model=model)

    llm = make_llm(daily_script(signal_analysis=spends), tight)
    result = await run_daily(llm, settings=tight)

    assert result.status == "succeeded"
    found = await analyses(serial)
    assert len(found) == 1 and found[0].version == 1  # the first one is kept
    assert tasks_of(llm)["signal_analysis"] == 2  # the third was never started
    assert tasks_of(llm)["analysis_fact_check"] == 1  # the second's check was refused
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert "budget_stopped" in agent_run.notes
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["signals_deep_analyzed"] == 1
    steps = await _deep_steps(serial, result.agent_run_id)
    assert any(text and text.startswith("budget: daily") for text in steps)
    briefings = await rows(serial, select(Briefing))
    assert briefings and {b.status for b in briefings} == {"skipped_budget"}  # still filed


async def test_deep_analyze_keeps_a_reserve_for_the_briefing(
    serial, fake_collectors, run_daily, make_llm, cfg, monkeypatch
):
    """Stage B stops starting signals once the day's remaining budget is under
    NEWSROOM_RESERVE_USD, without a budget stop, so a busy stage B never costs the day's
    briefing; the deferred signals stay due for the next run."""
    fake_collectors({"Sonatype Blog": FetchResult(items=THREE_STORIES)})
    monkeypatch.setattr(daily_mod, "DEEP_ANALYZE_CONCURRENCY", 1)  # a deterministic order
    settings = cfg(llm_daily_budget_usd=0.13)
    assert settings.newsroom_reserve_usd == 0.05

    def spends(*, user, schema, model, **_):  # the first check leaves ~$0.03 of the cap
        value = schema.model_validate(analysis_fact_check(user=user))
        return StructuredOut(value=value, usage=Usage(1_000_000, 0, 0), model=model)  # $0.10

    llm = make_llm(daily_script(analysis_fact_check=spends), settings)
    result = await run_daily(llm, settings=settings)

    assert result.status == "succeeded"
    assert len(await analyses(serial)) == 1
    assert tasks_of(llm)["signal_analysis"] == 1  # the other two were not started
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert "budget_stopped" not in (agent_run.notes or "")
    steps = await _deep_steps(serial, result.agent_run_id)
    assert not any(text and text.startswith("budget:") for text in steps)
    assert any("kept for the briefing" in (text or "") for text in steps)
    assert "deep analysis: 1 analysed, 0 failed, 2 skipped" in steps
    briefings = await rows(serial, select(Briefing))
    assert briefings and {b.status for b in briefings} == {"published"}
    assert len(await select_for_deep_analysis(serial, settings)) == 2  # due for the next run


async def test_deep_analyze_rescores_what_it_wrote(serial, fake_collectors, run_daily, make_llm):
    """The signals stage B just analysed are re-scored before route and the
    newsroom, so an analysis that finds no JFrog product affected already lowers the score
    of the briefing that day (score runs before deep_analyze)."""
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})

    def no_product(*, user, **_):
        refs = [r.strip() for r in REFS_LINE.search(user).group(1).split(",")]
        return minimal_analysis(next(r for r in refs if r.startswith("doc:")), impact=False)

    llm = make_llm(daily_script(signal_analysis=no_product))
    await run_daily(llm)

    signals = await rows(serial, select(Signal))
    assert signals and len(await analyses(serial)) == len(signals)
    for sig in signals:
        assert sig.score_breakdown["product_impact"] == {"none": True, "multiplier": 0.7}
        assert sig.score_breakdown["importance"] == sig.importance


def no_product(*, user, **_):
    """A stored stage-B analysis that rates no JFrog product (the relevance multiplier lowers its
    score)."""
    refs = [r.strip() for r in REFS_LINE.search(user).group(1).split(",")]
    return minimal_analysis(next(r for r in refs if r.startswith("doc:")), impact=False)


async def test_resume_applies_no_product_multiplier(serial, fake_collectors, run_daily, make_llm):
    """The relevance multiplier on resume: the worker dies after stage B stored one of three
    analyses, before their re-score. The resumed deep_analyze selects only the two still
    due, yet re-scores every signal analysed in this run, so that day's briefing ranks the
    first one with its multiplier too."""
    fake_collectors({"Sonatype Blog": FetchResult(items=THREE_STORIES)})
    saver = MemorySaver()
    calls = {"n": 0}

    def dies_after_one(*, user, **req):
        calls["n"] += 1
        if calls["n"] > 1:
            raise Killed()
        return no_product(user=user, **req)

    with pytest.raises(Killed):
        await run_daily(make_llm(daily_script(signal_analysis=dies_after_one)), checkpointer=saver)
    assert len(await analyses(serial)) == 1

    llm = make_llm(daily_script(signal_analysis=no_product))
    result = await run_daily(llm, checkpointer=saver, resume=True)

    assert result.status == "succeeded" and tasks_of(llm)["signal_analysis"] == 2
    signals = await rows(serial, select(Signal))
    assert len(signals) == 3 and len(await analyses(serial)) == 3
    for sig in signals:
        assert sig.score_breakdown["product_impact"] == {"none": True, "multiplier": 0.7}, sig.id
        assert sig.score_breakdown["importance"] == sig.importance, sig.id


async def test_deep_analyze_failures_do_not_stop_the_run(
    serial, fake_collectors, run_daily, make_llm
):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})

    def broken(**_):
        raise RuntimeError("model exploded")

    llm = make_llm(daily_script(signal_analysis=broken))
    result = await run_daily(llm)
    assert result.status == "succeeded" and await analyses(serial) == []
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["signals_deep_analyzed"] == 0 and run.stats["briefing_status"] == "published"
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert "deep_analyze: 2 not stored" in agent_run.notes  # said once, the run goes on


async def test_deep_analyze_skips_without_llm(serial, fake_collectors, run_daily, cfg):
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    result = await run_daily(None, settings=cfg(openai_api_key=None))
    assert result.status == "succeeded" and await analyses(serial) == []


# ---- tag_postings ---------------------------------------------------------------


async def _open_posting(session, make_source, competitor_id: int) -> int:
    from app.models import JobPosting

    src = await make_source(kind="job_board", competitor_id=competitor_id, config={"ats": "lever"})
    post = JobPosting(
        source_id=src.id,
        competitor_id=competitor_id,
        ats="lever",
        external_id="p-1",
        title="Senior Security Engineer",
        department="Security",
        seniority="Senior",
        content_hash="h1",
        description_text="Work on software composition analysis.",
    )
    session.add(post)
    await session.commit()
    return post.id


async def test_tag_postings_runs_after_index_new_and_counts(
    seeded, serial, fake_collectors, run_daily, make_llm, make_source
):
    from app.models import Competitor, JobPosting

    comp_id = await seeded.scalar(select(Competitor.id).where(~Competitor.is_self).limit(1))
    post_id = await _open_posting(seeded, make_source, comp_id)
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    llm = make_llm(
        daily_script(posting_tagger=[{"focus": "f", "product_role": True, "tags": ["sca"]}])
    )
    result = await run_daily(llm)

    assert result.status == "succeeded"
    posting = await one(serial, JobPosting, post_id)
    assert posting.tags == ["sca"] and posting.tagged_at is not None
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["postings_tagged"] == 1
    ledger = await rows(serial, select(LlmCall.task).order_by(LlmCall.id))
    assert ledger.index("posting_tagger") < ledger.index("triage")  # runs before triage
    async with serial() as s:
        steps = await s.scalars(
            select(AgentStep.node).where(AgentStep.agent_run_id == result.agent_run_id)
        )
        assert "tag_postings" in set(steps)


async def test_tag_postings_failure_does_not_stop_the_run(
    seeded, serial, fake_collectors, run_daily, make_llm, make_source
):
    from app.models import Competitor, JobPosting

    comp_id = await seeded.scalar(select(Competitor.id).where(~Competitor.is_self).limit(1))
    post_id = await _open_posting(seeded, make_source, comp_id)
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    llm = make_llm(
        daily_script(
            posting_tagger=[{"focus": "f", "product_role": True, "tags": ["not-a-capability"]}]
        )
    )
    result = await run_daily(llm)
    assert result.status == "succeeded"
    assert (await one(serial, JobPosting, post_id)).tagged_at is None  # tomorrow's job
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["postings_tagged"] == 0 and run.stats["briefing_status"] == "published"


async def test_tag_postings_skips_without_llm(
    seeded, serial, fake_collectors, run_daily, cfg, make_source
):
    from app.models import Competitor, JobPosting

    comp_id = await seeded.scalar(select(Competitor.id).where(~Competitor.is_self).limit(1))
    post_id = await _open_posting(seeded, make_source, comp_id)
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    result = await run_daily(None, settings=cfg(openai_api_key=None))
    assert result.status == "succeeded"
    assert (await one(serial, JobPosting, post_id)).tagged_at is None


# ---- judge_pages ----------------------------------------------------------------


async def test_judge_pages_runs_before_triage_and_the_document_is_triaged_same_run(
    seeded, serial, fake_collectors, run_daily, make_llm
):
    from app.intel.pages import store_page_snapshot
    from app.models import Competitor, PageSnapshot

    comp = await seeded.scalar(select(Competitor).where(Competitor.slug == "sonatype"))
    src = await seeded.scalar(
        select(Source).where(Source.kind == "page_watch", Source.competitor_id == comp.id)
    )
    assert src is not None and src.config["page"] == "pricing"  # seeded
    base = chr(10).join(f"Plan feature {i}" for i in range(20))
    now = datetime.now(UTC)
    await store_page_snapshot(seeded, src, base + chr(10) + "Team: 49 dollars", now=now)
    note = "The Team plan now includes unlimited private repositories and audit logs for all seats."
    await store_page_snapshot(
        seeded, src, base + chr(10) + "Team: 79 dollars" + chr(10) + note * 2, now=now
    )
    await seeded.commit()
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    verdict = {"meaningful": True, "change_kind": "pricing", "summary": "Team rose to 79 dollars"}
    llm = make_llm(daily_script(page_change_judge=[verdict]))
    result = await run_daily(llm)

    assert result.status == "succeeded"
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["page_changes"] == 1
    snaps = await rows(serial, select(PageSnapshot).order_by(PageSnapshot.id))
    assert snaps[1].meaningful is True and snaps[1].judged_at is not None
    docs = await rows(serial, select(Document).where(Document.source_id == src.id))
    assert len(docs) == 1 and docs[0].embedding is not None
    assert docs[0].triage is not None  # triage saw it in the same run
    ledger = await rows(serial, select(LlmCall.task).order_by(LlmCall.id))
    assert ledger.index("page_change_judge") < ledger.index("triage")


async def test_an_indexing_error_in_judge_pages_is_recorded_and_the_run_goes_on(
    seeded, serial, fake_collectors, run_daily, make_llm, monkeypatch
):
    """Embedding the new page-change documents can fail with an error other
    than a budget stop. The verdicts and documents are already committed, so the node keeps
    its count, records the error in the run's notes and the run goes on to its briefing; the
    documents wait for the next run's index_new."""
    from app.agents.nodes import page_changes as judge_mod
    from app.intel.pages import store_page_snapshot
    from app.models import Competitor

    comp = await seeded.scalar(select(Competitor).where(Competitor.slug == "sonatype"))
    src = await seeded.scalar(
        select(Source).where(Source.kind == "page_watch", Source.competitor_id == comp.id)
    )
    base = chr(10).join(f"Plan feature {i}" for i in range(20))
    now = datetime.now(UTC)
    await store_page_snapshot(seeded, src, base + chr(10) + "Team: 49 dollars", now=now)
    note = "The Team plan now includes unlimited private repositories and audit logs for all seats."
    await store_page_snapshot(
        seeded, src, base + chr(10) + "Team: 79 dollars" + chr(10) + note * 2, now=now
    )
    await seeded.commit()

    async def broken(*args, **kwargs):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr(judge_mod, "index_documents", broken)
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    verdict = {"meaningful": True, "change_kind": "pricing", "summary": "Team rose to 79 dollars"}
    result = await run_daily(make_llm(daily_script(page_change_judge=[verdict])))

    assert result.status == "succeeded"
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["page_changes"] == 1 and run.stats["briefing_status"] == "published"
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert "judge_pages: indexing 1 new document failed" in agent_run.notes
    assert "RuntimeError: embedding service down" in agent_run.notes
    docs = await rows(serial, select(Document).where(Document.source_id == src.id))
    assert len(docs) == 1 and docs[0].embedding is None  # index_new embeds it next run


# ---- extract_facts --------------------------------------------------------------

GITLAB_ABOUT = chr(10).join(
    (
        "GitLab is the most comprehensive AI-powered DevSecOps platform.",
        "GitLab has more than 2,100 employees in over 60 countries.",
        "Our values guide how we work.",
    )
)
HEADCOUNT = {
    "competitor": "gitlab",
    "kind": "headcount",
    "value_num": 2100,
    "unit": "employees",
    "currency": None,
    "label": "employees",
    "as_of": datetime.now(UTC).date().isoformat(),  # the page's present tense: its day
    "quote": "GitLab has more than 2,100 employees in over 60 countries.",
}


async def _gitlab_about(seeded) -> int:
    from app.intel.pages import store_page_snapshot
    from app.models import Competitor

    comp = await seeded.scalar(select(Competitor).where(Competitor.slug == "gitlab"))
    src = await seeded.scalar(
        select(Source).where(
            Source.kind == "page_watch",
            Source.competitor_id == comp.id,
            Source.config["page"].astext == "about",
        )
    )
    assert src is not None  # seeded
    snap = await store_page_snapshot(seeded, src, GITLAB_ABOUT, now=datetime.now(UTC))
    await seeded.commit()
    return snap.id


async def test_extract_facts_runs_after_deep_analyze_and_counts(
    seeded, serial, fake_collectors, run_daily, make_llm
):
    from app.models import CompanyFact, PageSnapshot

    snap_id = await _gitlab_about(seeded)
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    llm = make_llm(daily_script(fact_extractor=[{"facts": [HEADCOUNT]}]))
    result = await run_daily(llm)

    assert result.status == "succeeded"
    (row,) = await rows(serial, select(CompanyFact))
    assert (row.kind, row.value_num, row.source_kind, row.source_id) == (
        "headcount",
        2100,
        "page_snapshot",
        snap_id,
    )
    assert (await one(serial, PageSnapshot, snap_id)).facts_extracted_at is not None
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["facts_extracted"] == 1 and run.stats["briefing_status"] == "published"
    ledger = await rows(serial, select(LlmCall.task).order_by(LlmCall.id))
    last_analysis = max(i for i, t in enumerate(ledger) if t == "signal_analysis")
    assert last_analysis < ledger.index("fact_extractor") < ledger.index("editor")
    async with serial() as s:
        steps = (
            await s.execute(
                select(AgentStep.node, AgentStep.agent).where(
                    AgentStep.agent_run_id == result.agent_run_id,
                    AgentStep.node == "extract_facts",
                )
            )
        ).all()
    assert steps and {agent for _, agent in steps} == {"fact_extractor"}


async def test_extract_facts_failure_does_not_stop_the_run_and_skips_without_llm(
    seeded, serial, fake_collectors, run_daily, make_llm, cfg
):
    from app.models import PageSnapshot

    snap_id = await _gitlab_about(seeded)
    fake_collectors({"Sonatype Blog": FetchResult(items=ITEMS)})
    result = await run_daily(None, settings=cfg(openai_api_key=None))
    assert result.status == "succeeded"
    assert (await one(serial, PageSnapshot, snap_id)).facts_extracted_at is None

    bad = {**HEADCOUNT, "kind": "rumour"}
    result = await run_daily(make_llm(daily_script(fact_extractor=[{"facts": [bad]}])))
    assert result.status == "succeeded"
    assert (await one(serial, PageSnapshot, snap_id)).facts_extracted_at is None  # tomorrow
    run = await one(serial, PipelineRun, result.pipeline_run_id)
    assert run.stats["facts_extracted"] == 0 and run.stats["briefing_status"] == "published"
    agent_run = await one(serial, AgentRun, result.agent_run_id)
    assert "extract_facts: 1 failed, tried again next run" in agent_run.notes


# ---- cost -------------------------------------------------------

PROSE = (
    "The vendor said the new release adds policy controls for every model request and keeps "
    "an audit trail of each decision. Customers can roll the feature out gradually, starting "
    "with a single team, and administrators review the results before wider adoption. "
    "Analysts noted that the move follows a series of announcements about governance, "
    "supply chain security and pricing for enterprise subscriptions across the market. "
)


def prose(words: int) -> str:
    text = PROSE * (words // len(PROSE.split()) + 1)
    return " ".join(text.split()[:words])


def realistic_usage() -> tuple[Usage, Usage]:
    """Token counts (tiktoken o200k_base) of the REAL rendered prompts for a representative
    signal: 4 member documents at the real truncation (1,500 words), the JFrog context pack,
    6 knowledge-base chunks of up to 600 tokens, 5 prior signals and 30 checked items. The
    system message is the stable prefix, so it counts as cached (a warm provider cache) and
    the user message as uncached. The outputs are the estimate: ~2,500 tokens for a 600 to
    1,100 word analysis and ~25 tokens per verdict. Returns (writer, checker) usage."""
    from app.agents.nodes import signal_analysis as sa
    from app.agents.nodes.analyst import DOC_CHARS, DOC_WORDS
    from app.agents.runtime.prompts import load_prompt, render, untrusted
    from app.rag.chunking import chunk_text, count_tokens
    from app.rag.kb import load_jfrog_context

    brief = load_jfrog_context()
    docs = [
        sa._Source(
            ref=f"doc:{i}",
            header=f"doc:{i} (source: Sonatype Newsroom, grade A2, published 2026-10-15)",
            body=f"Title: Sonatype launches a model firewall {i}\n\n{prose(DOC_WORDS)}",
        )
        for i in range(1, 5)
    ]
    assert max(len(d.body) for d in docs) < DOC_CHARS  # no cut beyond the word cap
    chunk = chunk_text(prose(900), max_tokens=600)[0]
    assert 500 <= count_tokens(chunk) <= 600
    kb = [
        sa._Source(
            ref=f"kb:{i}",
            header=f"kb:{i} (JFrog knowledge base: https://jfrog.com/p{i}/; excerpts)",
            body=f"Title: JFrog page {i}\n\n{chunk}\n\n[...]\n\n{chunk}",
        )
        for i in range(1, 4)
    ]
    prior = [
        sa._Source(
            ref=f"signal:{i}",
            header=f"signal:{i} (FrogEye signal first seen 2026-10-01; companies: Sonatype)",
            body=f"Title: Earlier move {i}\nFact: {prose(25)}\nImpact: {prose(20)}",
        )
        for i in range(1, 6)
    ]
    inputs = sa._Inputs(
        stage_a=untrusted("stage-a", prose(110), max_chars=sa.STAGE_A_CHARS),
        profile="Evidence: grade A2, 4 sources, not hedged.",
        docs=docs,
        kb=kb,
        prior=prior,
    )
    variables = {
        "jfrog_context": brief,
        "today": "2026-10-19",
        "refs": ", ".join(inputs.refs),
        "signal": inputs.stage_a,
        "documents": [s.view() for s in inputs.docs],
        "kb": [s.view() for s in inputs.kb],
        "prior": [s.view() for s in inputs.prior],
    }
    system, user = render(load_prompt(sa.TASK), **variables)
    smart = Usage(count_tokens(system) + count_tokens(user), count_tokens(system), 2500)

    items = [
        sa._Item("what_happened", i, "what_happened", prose(35), ("doc:1", "doc:2"))
        for i in range(30)
    ]
    _schema, check_vars = sa._fact_check_request(items, inputs, brief)
    check_system, check_user = render(load_prompt(sa.CHECK_PROMPT), **check_vars)
    fast = Usage(
        count_tokens(check_system) + count_tokens(check_user),
        count_tokens(check_system),
        len(items) * 25,
    )
    return smart, fast


class UsageByTask(FakeBackend):
    """A FakeBackend that reports a fixed usage per task, whatever the script."""

    def __init__(self, script, usage_by_task: dict[str, Usage]) -> None:
        super().__init__(script)
        self.usage_by_task = usage_by_task

    async def structured(self, *, task, **kw):
        self.usage = self.usage_by_task.get(task, self.usage)
        return await super().structured(task=task, **kw)


async def test_deep_analyze_cost_accounting(seeded, serial, cfg, make_signal, make_document):
    """60 signals (the cap) at the measured token counts stay inside the $0.25 bound, which
    the daily cap guards anyway (it replaces the earlier $0.15)."""
    settings = cfg()
    writer, check = realistic_usage()
    # both stage-B calls run on the smart tier (the checker since the eval experiments)
    per_signal = cost_usd(
        settings.model_smart, writer.input_tokens, writer.cached_tokens, writer.output_tokens
    ) + cost_usd(settings.model_smart, check.input_tokens, check.cached_tokens, check.output_tokens)
    print(f"measured per-signal estimate: ${per_signal:.5f}; writer {writer}; check {check}")

    for n in range(settings.deep_analysis_cap):
        doc = await make_document(
            title=f"Cost story {n}", clean_text=prose(300) + f" {n}", status="relevant"
        )
        await make_signal(importance=50, status="auto", documents=[doc])
    backend = UsageByTask(
        {"signal_analysis": signal_analysis, "analysis_fact_check": analysis_fact_check},
        {"signal_analysis": writer, "analysis_fact_check": check},
    )
    llm = AccountedLLM(backend, serial, settings)

    batch = await daily_mod.deep_analyze_batch(serial, settings, llm, RunCtx(None, "deep_analyze"))

    assert batch.analyzed == 60 and batch.failed == [] and batch.budget is None
    async with serial() as s:
        calls = (
            await s.execute(select(LlmCall.task, func.sum(LlmCall.cost_usd)).group_by(LlmCall.task))
        ).all()
        rows_cost = await s.scalar(select(func.sum(SignalAnalysis.cost_usd)))
    by_task = {task: float(total) for task, total in calls}
    total = by_task["signal_analysis"] + by_task["analysis_fact_check"]
    assert total == pytest.approx(60 * per_signal, rel=1e-3)
    assert float(rows_cost) == pytest.approx(total, rel=1e-3)  # the per-row cost adds up
    assert total <= 0.25
