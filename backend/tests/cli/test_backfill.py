"""The shared backfill plumbing (app/cli/_backfill.py): one copy of the cap message,
the --limit rule, the run lock and the exit codes, used by every LLM backfill CLI."""

import json
from decimal import Decimal

import pytest

from app.cli import extract_facts, run_daily, tag_postings
from app.cli._backfill import Backfill, backfill_main, with_ledger_cost
from app.models.agents import LlmCall
from app.processing.ingest import RunAlreadyActive

CLI = Backfill(prog="python -m app.cli.example", idle="nothing done", limit_noun="widgets")


@pytest.fixture
def keyed(monkeypatch, settings):
    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": "k"})
    )


def test_one_json_line_and_the_cap_only_ever_raised(keyed, settings, capsys):
    seen: list = []

    async def run(run_settings, args):
        seen.append((run_settings, args.limit))
        return {"done": 3, "cost_usd": 0.001}

    backfill_main(CLI, ["--max-usd", "1.5", "--limit", "4"], run=run, setup=lambda: None)
    out, err = capsys.readouterr()
    assert [json.loads(line) for line in out.strip().splitlines()] == [
        {"done": 3, "cost_usd": 0.001}
    ]
    used, limit = seen[0]
    assert limit == 4 and used.llm_daily_budget_usd == max(1.5, settings.llm_daily_budget_usd)
    assert used.llm_total_budget_usd == settings.llm_total_budget_usd
    assert "daily LLM cap for this run" in err and "the total cap" in err

    backfill_main(CLI, ["--max-usd", "0.01"], run=run, setup=lambda: None)  # never lower
    _, err = capsys.readouterr()
    assert seen[1][0].llm_daily_budget_usd == settings.llm_daily_budget_usd
    assert "keeps the higher configured cap" in err


def test_exit_codes_and_the_limit_rule(keyed, settings, monkeypatch, capsys):
    calls: list = []

    async def busy(run_settings, args):
        calls.append(args)
        raise RunAlreadyActive("an ingestion run is already active")

    with pytest.raises(SystemExit) as exc:
        backfill_main(CLI, [], run=busy, setup=lambda: None)
    out, err = capsys.readouterr()
    assert exc.value.code == 1 and "nothing run" in err and out == ""

    with pytest.raises(SystemExit) as exc:
        backfill_main(CLI, ["--limit", "0"], run=busy, setup=lambda: None)
    assert exc.value.code == 2 and "positive number of widgets" in capsys.readouterr().err

    monkeypatch.setattr(
        run_daily, "get_settings", lambda: settings.model_copy(update={"openai_api_key": None})
    )
    with pytest.raises(SystemExit) as exc:
        backfill_main(CLI, [], run=busy, setup=lambda: None)
    out, err = capsys.readouterr()
    assert exc.value.code == 2 and out == "" and "OPENAI_API_KEY is not set: nothing done." in err
    assert len(calls) == 1  # no call without a key

    plain = Backfill(prog="python -m app.cli.plain", idle="nothing done")  # no --limit
    with pytest.raises(SystemExit):
        backfill_main(plain, ["--limit", "3"], run=busy, setup=lambda: None)


def test_extra_arguments_reach_the_run(keyed, capsys):
    seen: list = []

    async def run(run_settings, args):
        seen.append(args.competitor)
        return {}

    extra = Backfill(
        prog="python -m app.cli.extra",
        idle="nothing done",
        add_arguments=lambda parser: parser.add_argument("--competitor"),
    )
    backfill_main(extra, ["--competitor", "socket"], run=run, setup=lambda: None)
    assert seen == ["socket"] and json.loads(capsys.readouterr().out) == {}


async def test_ledger_cost_counts_only_the_calls_made_inside(session, session_factory):
    def booked(cost: str) -> LlmCall:
        return LlmCall(
            task="t", model="m", request_hash="h", cost_usd=Decimal(cost), latency_ms=1, status="ok"
        )

    session.add(booked("0.5"))
    await session.commit()

    async def work() -> dict:
        async with session_factory() as s:
            s.add(booked("0.25"))
            await s.commit()
        return {"done": 1}

    assert await with_ledger_cost(session_factory, work) == {"done": 1, "cost_usd": 0.25}


def test_both_backfills_share_the_helper():
    for module, prog in (
        (tag_postings, "python -m app.cli.tag_postings"),
        (extract_facts, "python -m app.cli.extract_facts"),
    ):
        assert isinstance(module.CLI, Backfill) and module.CLI.prog == prog
        for copied in ("argparse", "acquire_run_lock", "release_run_lock", "LlmCall", "json"):
            assert copied not in vars(module), (module.__name__, copied)
