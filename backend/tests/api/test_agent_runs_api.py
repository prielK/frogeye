"""The agent-run API: step args and results for analysts only, what non-analysts still see, and the
link from a run to the briefing it filed."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models import AgentRun, AgentStep, Briefing

DAY = date(2026, 10, 17)


async def _run(session, n: int, **kw) -> AgentRun:
    run = AgentRun(
        graph="daily_intel",
        thread_id=f"t18-thread-{n}",
        trigger="schedule",
        status=kw.pop("status", "succeeded"),
        started_at=datetime(2026, 10, 17, 3, n, tzinfo=UTC),
        **kw,
    )
    session.add(run)
    await session.flush()
    return run


async def _steps(session, run: AgentRun) -> None:
    session.add_all(
        [
            AgentStep(
                agent_run_id=run.id,
                step_no=1,
                node="analyze",
                agent="analyst",
                kind="tool",
                tool_name="search_intel",
                args={"query": "dependency firewall", "k": 8},
                result_summary="3 documents",
                tokens_in=0,
                cached_tokens=0,
                tokens_out=0,
                cost_usd=Decimal("0"),
            ),
            AgentStep(
                agent_run_id=run.id,
                step_no=2,
                node="analyze",
                agent="analyst",
                kind="llm",
                args={"signal_id": 41},
                result_summary="signal analysed",
                model="gpt-6-luna",
                tokens_in=1200,
                cached_tokens=200,
                tokens_out=150,
                cost_usd=Decimal("0.000195"),
                latency_ms=900,
            ),
            AgentStep(
                agent_run_id=run.id,
                step_no=3,
                node="analyze",
                agent="analyst",
                kind="llm",
                result_summary="error: ValidationError: 1 validation error, input_value='Rival "
                "buys 40% of JFrog'",
                model="gpt-6-luna",
                tokens_in=900,
                cached_tokens=0,
                tokens_out=0,
                cost_usd=Decimal("0.00009"),
            ),
            AgentStep(
                agent_run_id=run.id,
                step_no=4,
                node="newsroom",
                agent="editor",
                kind="node",
                result_summary="budget: daily",
                tokens_in=0,
                cached_tokens=0,
                tokens_out=0,
                cost_usd=Decimal("0"),
            ),
            AgentStep(
                agent_run_id=run.id,
                step_no=5,
                node="triage",
                agent="triage",
                kind="llm",
                result_summary="refused: not appropriate",
                model="gpt-5-nano",
                tokens_in=10,
                cached_tokens=0,
                tokens_out=1,
                cost_usd=Decimal("0"),
            ),
        ]
    )
    await session.flush()


@pytest.mark.parametrize("role", ["sales", "pm", "exec", "pmm_devrel"])
async def test_steps_redact_args_for_non_analysts(auth_client, seeded, session, role):
    run = await _run(session, 1)
    await _steps(session, run)

    steps = (await (await auth_client(role)).get(f"/api/v1/agent-runs/{run.id}/steps")).json()

    assert [s["step_no"] for s in steps] == [1, 2, 3, 4, 5]
    # results quote signal titles and facts whatever their status, so neither the
    # arguments nor the results reach non-analysts.
    assert [s["args"] for s in steps] == [None] * 5
    assert [s["result_summary"] for s in steps] == [None] * 5
    # What ran, how it went and what it cost stay readable.
    assert steps[0]["tool_name"] == "search_intel"
    assert (steps[1]["model"], steps[1]["tokens_in"], steps[1]["tokens_out"]) == (
        "gpt-6-luna",
        1200,
        150,
    )
    assert steps[1]["cost_usd"] == pytest.approx(0.000195)
    assert [s["status"] for s in steps] == ["ok", "ok", "error", "budget", "refused"]
    assert [s["error_kind"] for s in steps] == [None, None, "ValidationError", None, None]


async def test_steps_show_args_to_analysts(auth_client, seeded, session):
    run = await _run(session, 2)
    await _steps(session, run)

    steps = (await (await auth_client("analyst")).get(f"/api/v1/agent-runs/{run.id}/steps")).json()

    assert [s["args"] for s in steps][:2] == [
        {"query": "dependency firewall", "k": 8},
        {"signal_id": 41},
    ]
    assert steps[0]["result_summary"] == "3 documents"
    assert steps[2]["result_summary"].startswith("error: ValidationError: 1 validation error")
    assert [s["status"] for s in steps] == ["ok", "ok", "error", "budget", "refused"]


NOTES = (
    "budget_stopped; analyze signal 12: LLMError: analyst: ValidationError: input_value="
    "'Rival; buys JFrog'; newsroom: editor: LLMError: refused"
)


@pytest.mark.parametrize("role", ["sales", "pm", "exec", "pmm_devrel"])
async def test_run_notes_and_error_hide_details_from_non_analysts(
    auth_client, seeded, session, role
):
    """Node errors can quote model output (pydantic `input_value`), so non-analysts
    get the flags and the fact that stages failed, and a run's error by its class only."""
    noted = await _run(session, 5, notes=NOTES)
    failed = await _run(
        session, 6, status="failed", error="ValueError: bad input_value='Rival buys JFrog'"
    )
    c = await auth_client(role)
    rows = {r["id"]: r for r in (await c.get("/api/v1/agent-runs")).json()}
    assert rows[noted.id]["notes"] == "budget_stopped; stage_errors"
    assert rows[failed.id]["error"] == "ValueError"
    one = (await c.get(f"/api/v1/agent-runs/{failed.id}")).json()
    assert one["error"] == "ValueError"

    analyst = await auth_client("analyst")
    rows = {r["id"]: r for r in (await analyst.get("/api/v1/agent-runs")).json()}
    assert rows[noted.id]["notes"] == NOTES
    assert rows[failed.id]["error"].startswith("ValueError: bad input_value")


async def test_run_names_the_briefing_it_filed(auth_client, seeded, session):
    filed = await _run(session, 3)
    ingest_only = await _run(session, 4, notes="llm_disabled")
    for edition in ("master", "sales"):
        session.add(
            Briefing(
                date=DAY,
                edition=edition,
                version=1,
                status="published",
                content={"headline": "h"},
                signal_ids=[],
                funnel={},
                agent_run_id=filed.id,
            )
        )
    await session.flush()

    c = await auth_client("sales")
    rows = {r["id"]: r for r in (await c.get("/api/v1/agent-runs")).json()}
    assert rows[filed.id]["briefing_date"] == DAY.isoformat()
    assert rows[ingest_only.id]["briefing_date"] is None
    assert (await c.get(f"/api/v1/agent-runs/{filed.id}")).json()["briefing_date"] == "2026-10-17"
