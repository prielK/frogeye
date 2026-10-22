"""CLIs for the daily graph: run_daily (--max-usd, --resume), analyze_signals, export_graphs,
the backfills tag_postings and extract_facts, and the weekly generate_briefs."""

import json

import pytest

from app.agents.runner import DailyResult, NothingToResume
from app.cli import (
    analyze_signals,
    export_graphs,
    extract_facts,
    generate_briefs,
    run_daily,
    tag_postings,
)
from app.processing.ingest import RunAlreadyActive


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = 0

    async def dispose(self) -> None:
        self.disposed += 1


@pytest.fixture
def fake_run(monkeypatch, settings):
    seen: dict = {}
    engine = FakeEngine()

    async def _run(factory, *, trigger, resume, settings):
        seen.update(trigger=trigger, resume=resume, settings=settings)
        if seen.get("raise"):
            raise seen["raise"]
        return DailyResult(pipeline_run_id=3, agent_run_id=4, briefing_ids=[5, 6], status="ok")

    monkeypatch.setattr(run_daily, "run_daily_intel", _run)
    monkeypatch.setattr(run_daily, "engine", engine)
    monkeypatch.setattr(run_daily, "configure_logging", lambda: None)
    seen["engine"] = engine
    return seen


def test_run_daily_prints_the_result_and_raises_the_cap_for_this_process(
    fake_run, settings, capsys
):
    before = settings.llm_daily_budget_usd
    run_daily.main(["--max-usd", "2"])
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "pipeline_run_id": 3,
        "agent_run_id": 4,
        "briefing_ids": [5, 6],
        "status": "ok",
    }
    used = fake_run["settings"]
    assert used.llm_daily_budget_usd == 2.0
    assert used.llm_total_budget_usd == settings.llm_total_budget_usd  # still applies
    assert settings.llm_daily_budget_usd == before  # the cached settings are untouched
    assert fake_run["trigger"] == "manual" and fake_run["resume"] is False
    assert fake_run["engine"].disposed == 1


def test_run_daily_max_usd_never_lowers_the_cap(fake_run, settings, capsys):
    below = settings.llm_daily_budget_usd / 2
    run_daily.main(["--max-usd", str(below)])
    assert fake_run["settings"].llm_daily_budget_usd == settings.llm_daily_budget_usd
    assert "keeps" in capsys.readouterr().err


def test_run_daily_resume_and_nothing_to_resume_exits_1(fake_run, capsys):
    fake_run["raise"] = NothingToResume("the latest daily run is not interrupted")
    with pytest.raises(SystemExit) as exc:
        run_daily.main(["--resume"])
    assert exc.value.code == 1 and fake_run["resume"] is True
    assert "not interrupted" in capsys.readouterr().err


def test_run_daily_rejects_a_non_positive_cap(fake_run):
    with pytest.raises(SystemExit):
        run_daily.main(["--max-usd", "0"])


def test_export_graphs_writes_both_diagrams(tmp_path):
    out = tmp_path / "agents.md"
    export_graphs.main(["--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert text.count("```mermaid") == 2
    for node in (
        "ingest",
        "triage",
        "analyze",
        "deep_analyze",
        "newsroom",
        "publish",
        "editor",
        "role_adapt",
    ):
        assert node in text


# ---- analyze_signals (the stage-B backfill) -----------------------------------------------


@pytest.fixture
def fake_analysis(monkeypatch, settings):
    seen: dict = {}
    engine = FakeEngine()

    async def _run(run_settings, limit):
        seen.update(settings=run_settings, limit=limit)
        if seen.get("raise"):
            raise seen["raise"]
        return {"analyzed": 3, "failed": 1, "budget_stopped": True, "cost_usd": 0.0123}

    monkeypatch.setattr(analyze_signals, "_run", _run)
    monkeypatch.setattr(analyze_signals, "configure_logging", lambda: None)
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": "k"})
    )
    seen["engine"] = engine
    return seen


def test_analyze_signals_cli_json_and_max_usd(fake_analysis, settings, capsys):
    analyze_signals.main(["--max-usd", "2", "--limit", "5"])
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1  # exactly one JSON line, exit 0 (also after a budget stop)
    assert json.loads(lines[0]) == {
        "analyzed": 3,
        "failed": 1,
        "budget_stopped": True,
        "cost_usd": 0.0123,
    }
    used = fake_analysis["settings"]
    assert used.llm_daily_budget_usd == 2.0 and fake_analysis["limit"] == 5
    assert used.llm_total_budget_usd == settings.llm_total_budget_usd  # still applies


def test_analyze_signals_max_usd_never_lowers_the_cap(fake_analysis, settings, capsys):
    analyze_signals.main(["--max-usd", str(settings.llm_daily_budget_usd / 2)])
    assert fake_analysis["settings"].llm_daily_budget_usd == settings.llm_daily_budget_usd
    assert "keeps" in capsys.readouterr().err
    assert fake_analysis["limit"] is None


def test_analyze_signals_exits_1_while_a_run_holds_the_lock(fake_analysis, capsys):
    fake_analysis["raise"] = RunAlreadyActive("an ingestion run is already active")
    with pytest.raises(SystemExit) as exc:
        analyze_signals.main([])
    assert exc.value.code == 1 and "already active" in capsys.readouterr().err


def test_analyze_signals_without_a_key_exits_2_and_calls_nothing(
    fake_analysis, settings, monkeypatch, capsys
):
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": None})
    )
    with pytest.raises(SystemExit) as exc:
        analyze_signals.main([])
    assert exc.value.code == 2 and "settings" not in fake_analysis
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("args", [["--max-usd", "0"], ["--limit", "0"], ["--limit", "x"]])
def test_analyze_signals_rejects_bad_numbers(fake_analysis, args):
    with pytest.raises(SystemExit):
        analyze_signals.main(args)


async def test_analyze_reports_the_ledger_cost_of_its_own_batch(
    session_factory, settings, monkeypatch
):
    from app.agents.graphs.daily_intel import DeepBatch
    from app.models.agents import LlmCall

    async def _old():
        async with session_factory() as s:
            s.add(
                LlmCall(
                    task="triage",
                    model="m",
                    request_hash="old",
                    cost_usd=5,
                    latency_ms=1,
                    status="ok",
                )
            )
            await s.commit()

    async def batch(factory, run_settings, llm, ctx, *, limit=None):
        assert ctx.agent_run_id is None and ctx.agent == "signal_analyst" and limit == 7
        async with factory() as s:
            s.add(
                LlmCall(
                    task="signal_analysis",
                    model="m",
                    request_hash="new",
                    cost_usd=0.25,
                    latency_ms=1,
                    status="ok",
                )
            )
            await s.commit()
        return DeepBatch(
            selected=4, analyzed=2, failed=[(9, "no sources")], budget=None, reserved=1
        )

    monkeypatch.setattr(analyze_signals, "deep_analyze_batch", batch)
    await _old()
    out = await analyze_signals.analyze(session_factory, None, settings, 7)
    # one signal kept back for the briefing's reserve is reported, not a budget stop;
    # the re-scores that failed are reported too
    assert out == {
        "analyzed": 2,
        "failed": 1,
        "score_failed": 0,
        "budget_stopped": False,
        "reserve_stopped": True,
        "cost_usd": 0.25,
    }


# ---- tag_postings (the job-posting tagging backfill) --------------------------------------


@pytest.fixture
def fake_tagging(monkeypatch, settings):
    seen: dict = {}

    async def _run(run_settings, limit):
        seen.update(settings=run_settings, limit=limit)
        if seen.get("raise"):
            raise seen["raise"]
        return {"tagged": 7, "failed": 1, "budget_stopped": False, "cost_usd": 0.0042}

    monkeypatch.setattr(tag_postings, "_run", _run)
    monkeypatch.setattr(tag_postings, "configure_logging", lambda: None)
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": "k"})
    )
    return seen


def test_tag_postings_cli_json(fake_tagging, settings, capsys):
    tag_postings.main(["--max-usd", "2", "--limit", "50"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "tagged": 7,
        "failed": 1,
        "budget_stopped": False,
        "cost_usd": 0.0042,
    }
    used = fake_tagging["settings"]
    assert used.llm_daily_budget_usd == 2.0 and fake_tagging["limit"] == 50
    assert used.llm_total_budget_usd == settings.llm_total_budget_usd


def test_tag_postings_exit_codes(fake_tagging, settings, monkeypatch, capsys):
    fake_tagging["raise"] = RunAlreadyActive("an ingestion run is already active")
    with pytest.raises(SystemExit) as exc:
        tag_postings.main([])
    assert exc.value.code == 1 and "already active" in capsys.readouterr().err
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": None})
    )
    with pytest.raises(SystemExit) as exc:
        tag_postings.main([])
    assert exc.value.code == 2 and capsys.readouterr().out == ""
    with pytest.raises(SystemExit):
        tag_postings.main(["--limit", "0"])


async def test_tag_postings_result_carries_the_ledger_cost(
    seeded, session, session_factory, make_source, competitor_factory, accounted_llm, settings
):
    from app.models import JobPosting

    comp = competitor_factory()
    session.add(comp)
    await session.flush()
    src = await make_source(kind="job_board", competitor_id=comp.id)
    session.add(
        JobPosting(
            source_id=src.id,
            competitor_id=comp.id,
            ats="lever",
            external_id="x",
            title="SCA Engineer",
            department="Engineering",
            seniority="Mid",
            content_hash="hx",
        )
    )
    await session.commit()
    llm = accounted_llm({"posting_tagger": [{"focus": "f", "product_role": True, "tags": ["sca"]}]})
    out = await tag_postings.tag(session_factory, llm, settings, None)
    assert out["tagged"] == 1 and out["failed"] == 0 and out["budget_stopped"] is False
    assert out["given_up"] == 0
    assert out["cost_usd"] > 0


# ---- extract_facts (the company-facts backfill) -------------------------------------------


@pytest.fixture
def fake_extraction(monkeypatch, settings):
    seen: dict = {}

    async def _run(run_settings, limit, lookback_days=None):
        seen.update(settings=run_settings, limit=limit, lookback_days=lookback_days)
        if seen.get("raise"):
            raise seen["raise"]
        return {
            "sources": 5,
            "stored": 3,
            "dropped": 2,
            "failed": 0,
            "reserved": 0,
            "budget_stopped": False,
            "cost_usd": 0.0031,
        }

    monkeypatch.setattr(extract_facts, "_run", _run)
    monkeypatch.setattr(extract_facts, "configure_logging", lambda: None)
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": "k"})
    )
    return seen


def test_extract_facts_cli_json(fake_extraction, settings, capsys):
    extract_facts.main(["--max-usd", "1.5", "--limit", "20"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "sources": 5,
        "stored": 3,
        "dropped": 2,
        "failed": 0,
        "reserved": 0,
        "budget_stopped": False,
        "cost_usd": 0.0031,
    }
    used = fake_extraction["settings"]
    assert used.llm_daily_budget_usd == max(1.5, settings.llm_daily_budget_usd)
    assert fake_extraction["limit"] == 20
    assert used.llm_total_budget_usd == settings.llm_total_budget_usd


def test_extract_facts_exit_codes(fake_extraction, settings, monkeypatch, capsys):
    fake_extraction["raise"] = RunAlreadyActive("an ingestion run is already active")
    with pytest.raises(SystemExit) as exc:
        extract_facts.main([])
    assert exc.value.code == 1 and "already active" in capsys.readouterr().err
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": None})
    )
    with pytest.raises(SystemExit) as exc:
        extract_facts.main([])
    assert exc.value.code == 2 and capsys.readouterr().out == ""
    with pytest.raises(SystemExit):
        extract_facts.main(["--limit", "0"])


def test_extract_facts_lookback_days_reaches_the_selection(fake_extraction, capsys):
    """The backfill's window is an option, documented in --help; the daily node keeps
    analysis_lookback_days."""
    extract_facts.main([])
    assert fake_extraction["lookback_days"] is None
    extract_facts.main(["--lookback-days", "365"])
    assert fake_extraction["lookback_days"] == 365
    with pytest.raises(SystemExit) as exc:
        extract_facts.main(["--lookback-days", "0"])
    assert exc.value.code == 2
    capsys.readouterr()
    with pytest.raises(SystemExit):
        extract_facts.main(["--help"])
    assert "--lookback-days" in capsys.readouterr().out
    assert "--lookback-days" in (extract_facts.__doc__ or "")


async def test_extract_facts_result_carries_the_ledger_cost(
    seeded, session, session_factory, make_document, accounted_llm, settings
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models import CompanyFact, Competitor, DocumentCompetitor

    socket = await session.scalar(select(Competitor.id).where(Competitor.slug == "socket"))
    published = datetime.now(UTC) - timedelta(hours=3)
    doc = await make_document(
        title="Socket raises $40M",
        clean_text="Socket raised a $40M Series C led by X to grow its platform.",
        status="relevant",
        published_at=published,
    )
    session.add(DocumentCompetitor(document_id=doc.id, competitor_id=socket))
    await session.commit()
    answer = {
        "competitor": "socket",
        "kind": "funding_round",
        "value_num": 40_000_000,
        "unit": None,
        "currency": "USD",
        "label": "Series C",
        "as_of": published.date().isoformat(),
        "quote": "Socket raised a $40M Series C led by X",
    }
    llm = accounted_llm({"fact_extractor": [{"facts": [answer]}]})
    out = await extract_facts.extract(session_factory, llm, settings, None)
    assert {k: v for k, v in out.items() if k != "cost_usd"} == {
        "sources": 1,
        "stored": 1,
        "dropped": 0,
        "failed": 0,
        "given_up": 0,
        "reserved": 0,
        "budget_stopped": False,
    }
    assert out["cost_usd"] > 0
    async with session_factory() as s:
        assert (await s.scalar(select(CompanyFact.source_id))) == doc.id


# ---- generate_briefs (the weekly competitor and product briefs) ----------------------------


@pytest.fixture
def fake_briefs(monkeypatch, settings):
    seen: dict = {}

    async def _run(run_settings, competitors, products):
        seen.update(settings=run_settings, competitors=competitors, products=products)
        if seen.get("raise"):
            raise seen["raise"]
        return {
            "generated": 2,
            "unchanged": 4,
            "skipped": 3,
            "failed": 1,
            "reserved": 0,
            "removed": 5,
            "budget_stopped": False,
            "failures": ["competitor sonatype: the fact-check supported no bottom line"],
            "cost_usd": 0.0123,
        }

    monkeypatch.setattr(generate_briefs, "_run", _run)
    monkeypatch.setattr(generate_briefs, "configure_logging", lambda: None)
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": "k"})
    )
    return seen


def test_generate_briefs_cli_json(fake_briefs, settings, capsys):
    generate_briefs.main(
        [
            "--max-usd",
            "1.25",
            "--competitor",
            "sonatype",
            "--competitor",
            "snyk",
            "--product",
            "Advanced Security",
            "--product",
            "xray",
        ]
    )
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "generated": 2,
        "unchanged": 4,
        "skipped": 3,
        "failed": 1,
        "reserved": 0,
        "removed": 5,
        "budget_stopped": False,
        "failures": ["competitor sonatype: the fact-check supported no bottom line"],
        "cost_usd": 0.0123,
    }
    assert "daily LLM cap for this run" in err
    used = fake_briefs["settings"]
    assert used.llm_daily_budget_usd == max(1.25, settings.llm_daily_budget_usd)
    assert used.llm_total_budget_usd == settings.llm_total_budget_usd
    assert fake_briefs["competitors"] == ["sonatype", "snyk"]
    assert fake_briefs["products"] == ["Advanced Security", "Xray"]  # names, any case or slug

    generate_briefs.main([])  # no filter: every competitor and every product
    capsys.readouterr()
    assert fake_briefs["competitors"] is None and fake_briefs["products"] is None
    generate_briefs.main(["--product", "advanced-security"])  # only what is named
    capsys.readouterr()
    assert fake_briefs["competitors"] == [] and fake_briefs["products"] == ["Advanced Security"]


def test_generate_briefs_exit_codes(fake_briefs, settings, monkeypatch, capsys):
    from app.intel.briefs import UnknownBriefTarget

    fake_briefs["raise"] = RunAlreadyActive("an ingestion run is already active")
    with pytest.raises(SystemExit) as exc:
        generate_briefs.main([])
    assert exc.value.code == 1 and "already active" in capsys.readouterr().err
    fake_briefs["raise"] = UnknownBriefTarget("unknown competitor slug(s): nope")
    with pytest.raises(SystemExit) as exc:
        generate_briefs.main(["--competitor", "nope"])
    out, err = capsys.readouterr()
    assert exc.value.code == 2 and "nope" in err and out == ""
    with pytest.raises(SystemExit) as exc:
        generate_briefs.main(["--product", "Nexus"])  # not a JFrog product
    assert exc.value.code == 2
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": None})
    )
    with pytest.raises(SystemExit) as exc:
        generate_briefs.main([])
    assert exc.value.code == 2 and capsys.readouterr().out == ""


async def test_generate_briefs_result_carries_the_ledger_cost(
    session, session_factory, competitor_factory, make_signal, accounted_llm, settings
):
    from app.intel.briefs import CHECK_TASK, COMPETITOR_TASK
    from tests.intel.test_briefs import analysed, impact, verdicts
    from tests.intel.test_profile import ago, new_comp

    comp = await new_comp(session, competitor_factory, slug="lone", name="Lone")
    sigs = [
        await analysed(
            session, make_signal, comp, ago(d), "Lone shipped a registry.", impact("Xray")
        )
        for d in (2, 4, 6)
    ]
    await session.commit()
    reply = {
        "bottom_line": [{"text": "Lone shipped a registry.", "cites": [f"signal:{sigs[0].id}"]}],
        "watch": [],
    }
    llm = accounted_llm({COMPETITOR_TASK: [reply], CHECK_TASK: verdicts()})
    out = await generate_briefs.generate(session_factory, llm, settings, ["lone"], [])
    assert {k: v for k, v in out.items() if k != "cost_usd"} == {
        "generated": 1,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
        "reserved": 0,
        "removed": 0,
        "budget_stopped": False,
        "failures": [],
    }
    assert out["cost_usd"] > 0
