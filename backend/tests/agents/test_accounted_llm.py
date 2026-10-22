import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, create_model
from sqlalchemy import func, select

from app.agents.runtime.accounted import BudgetExceeded, LLMError, LLMRefusal, request_hash
from app.agents.runtime.context import RunCtx
from app.agents.runtime.pricing import cost_usd
from app.agents.runtime.prompts import load_prompt, render
from app.agents.runtime.types import ChatMessage, ToolCall, ToolDef, ToolTurn, Usage
from app.models.agents import AgentRun, AgentStep, LlmCache, LlmCall
from tests.agents.fakes import Refusal, cosine

FIXTURE_PROMPTS = Path(__file__).resolve().parent.parent / "fixtures" / "prompts"
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)  # 12:00 in Asia/Jerusalem
TASK = "t.verdict"
VARS = {"topic": "model registry", "doc": "Acme ships a registry."}


class Verdict(BaseModel):
    label: str
    score: int


@pytest.fixture
def prompt():
    return load_prompt("echo", directory=FIXTURE_PROMPTS)


@pytest.fixture
async def ctx(session) -> RunCtx:
    run = AgentRun(graph="test", thread_id=f"t-{uuid.uuid4()}", trigger="test", status="running")
    session.add(run)
    await session.flush()
    return RunCtx(agent_run_id=run.id, node="triage", agent="triager")


async def rows(session, model):
    session.expire_all()
    order = model.created_at if model is LlmCache else model.id
    return (await session.execute(select(model).order_by(order))).scalars().all()


async def seed_call(session, *, cost: float, at: datetime, status: str = "ok") -> None:
    session.add(
        LlmCall(
            task="seed",
            model="gpt-5-nano",
            request_hash=f"seed-{uuid.uuid4()}",
            cost_usd=Decimal(str(cost)),
            latency_ms=1,
            status=status,
            created_at=at,
        )
    )
    await session.flush()


async def test_structured_logs_call_and_step_and_cost(
    accounted_llm, session, settings, prompt, ctx
):
    llm = accounted_llm({TASK: [Verdict(label="threat", score=3)]})

    out = await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)

    assert out == Verdict(label="threat", score=3)
    assert llm.backend.calls == [(TASK, "structured")]
    req = llm.backend.requests[0]
    assert (req["model"], req["effort"]) == (settings.model_fast, settings.reasoning_effort_fast)
    assert "model registry" in req["system"] and "Acme ships a registry." in req["user"]

    [call] = await rows(session, LlmCall)
    assert (call.task, call.model, call.status, call.prompt_version) == (
        TASK,
        settings.model_fast,
        "ok",
        "1",
    )
    assert call.agent_run_id == ctx.agent_run_id
    assert (call.input_tokens, call.cached_tokens, call.output_tokens) == (100, 0, 20)
    expected = cost_usd(settings.model_fast, 100, 0, 20)
    assert expected > 0
    assert float(call.cost_usd) == pytest.approx(expected)
    assert len(call.request_hash) == 64

    [step] = await rows(session, AgentStep)
    assert (step.kind, step.node, step.agent, step.agent_run_id, step.step_no) == (
        "llm",
        "triage",
        "triager",
        ctx.agent_run_id,
        1,
    )
    assert step.model == settings.model_fast
    assert (step.tokens_in, step.cached_tokens, step.tokens_out) == (100, 0, 20)
    assert float(step.cost_usd) == pytest.approx(expected)
    assert step.latency_ms is not None and step.latency_ms >= 0


# LLM-call keys of the echo fixture captured by running the AccountedLLM from before request_key()
# existed (tree 2c4c006): the refactor left `structured`'s cache key byte-for-byte unchanged.
GOLDEN_KEYS = {
    ("fast", None): "77375b4d3f7fdcfd36d6fe5f6f4564343cc23ba6b4086ae2acb3b05eb2180807",
    ("smart", "extra feedback"): "97a39d02c991615145c625b4f23c75bc7c0cffc380c3504ab8ffd94641ea9ed6",
}


@pytest.mark.parametrize(("tier", "extra"), list(GOLDEN_KEYS))
async def test_request_key_is_the_structured_cache_key(
    accounted_llm, session, settings, prompt, ctx, tier, extra
):
    llm = accounted_llm({TASK: [Verdict(label="a", score=1)]})
    key = llm.request_key(tier, Verdict, prompt=prompt, variables=VARS, extra_user=extra)

    await llm.structured(
        TASK, tier, Verdict, prompt=prompt, variables=VARS, ctx=ctx, extra_user=extra
    )

    [call] = await rows(session, LlmCall)
    if tier == "fast":
        model, effort = settings.model_fast, settings.reasoning_effort_fast
    else:
        model, effort = settings.model_smart, settings.reasoning_effort_smart
    system, user = render(prompt, **VARS)
    if extra:
        user = f"{user}\n\n{extra}"
    recipe = request_hash(  # the cache-key recipe: model, effort, prompt, schema, rendered text
        model,
        effort,
        prompt.name,
        prompt.version,
        Verdict.__name__,
        Verdict.model_json_schema(),
        system,
        user,
    )
    assert call.request_hash == key == recipe == GOLDEN_KEYS[(tier, extra)]


async def test_cache_hit_returns_without_backend_call(accounted_llm, session, prompt, ctx):
    script = {TASK: [Verdict(label="a", score=1), Verdict(label="b", score=2)]}
    llm = accounted_llm(script)

    first = await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)
    second = await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)

    assert first == second == Verdict(label="a", score=1)
    assert llm.backend.calls == [(TASK, "structured")]
    calls = await rows(session, LlmCall)
    assert [c.status for c in calls] == ["ok", "cached"]
    assert calls[1].cost_usd == 0 and calls[1].input_tokens == 0
    assert calls[0].request_hash == calls[1].request_hash
    assert len(await rows(session, LlmCache)) == 1

    # The cache lives in Postgres: a fresh AccountedLLM hits it too.
    other = accounted_llm({})
    again = await other.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)
    assert again == first and other.backend.calls == []

    # cache=False always calls the backend.
    fresh = await llm.structured(
        TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx, cache=False
    )
    assert fresh == Verdict(label="b", score=2)
    assert len(llm.backend.calls) == 2

    # A different tier (model) is a different request.
    llm2 = accounted_llm({TASK: [Verdict(label="c", score=3)]})
    smart = await llm2.structured(TASK, "smart", Verdict, prompt=prompt, variables=VARS, ctx=ctx)
    assert smart == Verdict(label="c", score=3)


async def test_budget_daily_exceeded_raises_before_call(accounted_llm, session, prompt, ctx):
    await seed_call(session, cost=0.75, at=NOW - timedelta(hours=1))
    llm = accounted_llm({TASK: [Verdict(label="a", score=1)]}, now=lambda: NOW)

    with pytest.raises(BudgetExceeded) as ei:
        await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)

    assert ei.value.scope == "daily"
    assert llm.backend.calls == []
    assert len(await rows(session, LlmCall)) == 1  # only the seed row


async def test_budget_total_exceeded(accounted_llm, session, prompt, ctx):
    await seed_call(session, cost=9.0, at=NOW - timedelta(days=3))
    llm = accounted_llm({TASK: [Verdict(label="a", score=1)]}, now=lambda: NOW)

    with pytest.raises(BudgetExceeded) as ei:
        await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)
    assert ei.value.scope == "total"
    with pytest.raises(BudgetExceeded):
        await llm.embed(["x"], ctx=ctx)
    with pytest.raises(BudgetExceeded):
        await llm.tool_turn(
            "t.tools", "smart", system="s", messages=[ChatMessage("user", "hi")], tools=[], ctx=ctx
        )
    assert llm.backend.calls == []
    assert await llm.spent_today() == 0
    assert await llm.spent_total() == pytest.approx(9.0)


async def test_budget_counts_local_spend_between_refreshes(
    accounted_llm, test_settings_override, settings, prompt, ctx
):
    per_call = cost_usd(settings.model_fast, 100, 0, 20)
    tight = test_settings_override(llm_daily_budget_usd=per_call * 1.5)
    llm = accounted_llm(
        {TASK: lambda **req: Verdict(label=req["user"][-6:], score=1)}, settings=tight
    )

    await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)
    await llm.structured(
        TASK, "fast", Verdict, prompt=prompt, variables={**VARS, "topic": "two"}, ctx=ctx
    )
    with pytest.raises(BudgetExceeded) as ei:
        await llm.structured(
            TASK, "fast", Verdict, prompt=prompt, variables={**VARS, "topic": "three"}, ctx=ctx
        )
    assert ei.value.scope == "daily"
    assert len(llm.backend.calls) == 2


async def test_refusal_raises_and_logs(accounted_llm, session, settings, prompt, ctx):
    llm = accounted_llm({TASK: [Refusal("not allowed"), Verdict(label="ok", score=1)]})

    with pytest.raises(LLMRefusal) as ei:
        await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)

    assert (ei.value.task, ei.value.refusal) == (TASK, "not allowed")
    [call] = await rows(session, LlmCall)
    assert (call.status, call.error) == ("refused", "not allowed")
    assert float(call.cost_usd) == pytest.approx(cost_usd(settings.model_fast, 100, 0, 20))
    assert await rows(session, LlmCache) == []
    # Refusals are not cached: the retry reaches the backend.
    out = await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)
    assert out.label == "ok"


async def test_backend_error_logged_and_raised(accounted_llm, session, prompt, ctx):
    llm = accounted_llm({TASK: [RuntimeError("upstream boom")]})

    with pytest.raises(LLMError) as ei:
        await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)

    assert ei.value.task == TASK
    assert isinstance(ei.value.__cause__, RuntimeError)
    [call] = await rows(session, LlmCall)
    assert call.status == "error"
    assert "upstream boom" in call.error
    assert call.cost_usd == 0
    [step] = await rows(session, AgentStep)
    assert step.result_summary.startswith("error:")
    assert await rows(session, LlmCache) == []


async def test_spent_today_uses_local_day(accounted_llm, session, test_settings_override):
    now = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)  # 13:00 on Oct 17 in Jerusalem (UTC+3)
    await seed_call(session, cost=0.10, at=datetime(2026, 10, 16, 23, 30, tzinfo=UTC))  # 02:30
    await seed_call(session, cost=0.20, at=datetime(2026, 10, 16, 20, 30, tzinfo=UTC))  # 23:30 16th
    await seed_call(session, cost=0.40, at=datetime(2026, 10, 17, 21, 30, tzinfo=UTC))  # 00:30 18th
    llm = accounted_llm({}, now=lambda: now)

    assert await llm.spent_today() == pytest.approx(0.10)
    assert await llm.spent_total() == pytest.approx(0.70)

    # The same instant with a UTC day boundary counts a different set of rows.
    utc_llm = accounted_llm({}, now=lambda: now, settings=test_settings_override(timezone="UTC"))
    assert await utc_llm.spent_today() == pytest.approx(0.40)


async def test_tool_turn_accounted_never_cached(accounted_llm, session, settings, ctx):
    turn = ToolTurn(
        content="",
        tool_calls=[ToolCall(id="c1", name="search_intel", args={"q": "nexus"})],
        usage=Usage(200, 50, 10),
        model=settings.model_smart,
    )
    llm = accounted_llm({"t.tools": [turn, "final words"]})
    tools = [ToolDef("search_intel", "Search", {"type": "object", "properties": {}})]
    msgs = [ChatMessage("user", "What did Acme ship?")]

    first = await llm.tool_turn(
        "t.tools", "smart", system="sys", messages=msgs, tools=tools, ctx=ctx
    )
    second = await llm.tool_turn(
        "t.tools", "smart", system="sys", messages=msgs, tools=tools, ctx=ctx
    )

    assert first.tool_calls == [ToolCall(id="c1", name="search_intel", args={"q": "nexus"})]
    assert second.content == "final words" and second.tool_calls == []
    assert llm.backend.calls == [("t.tools", "tool_turn"), ("t.tools", "tool_turn")]
    req = llm.backend.requests[0]
    assert (req["model"], req["effort"]) == (settings.model_smart, settings.reasoning_effort_smart)
    calls = await rows(session, LlmCall)
    assert [c.status for c in calls] == ["ok", "ok"]
    assert (calls[0].input_tokens, calls[0].cached_tokens, calls[0].output_tokens) == (200, 50, 10)
    assert float(calls[0].cost_usd) == pytest.approx(cost_usd(settings.model_smart, 200, 50, 10))
    assert [s.step_no for s in await rows(session, AgentStep)] == [1, 2]
    assert await rows(session, LlmCache) == []


async def test_embed_accounted_with_cluster_vectors(accounted_llm, session, settings, ctx):
    llm = accounted_llm({})
    texts = ["#cluster:a Acme ships", "#cluster:a Acme launches", "#cluster:b other", "plain"]

    vecs = await llm.embed(texts, ctx=ctx)

    assert len(vecs) == 4 and all(len(v) == settings.embedding_dims for v in vecs)
    assert cosine(vecs[0], vecs[1]) >= 0.95
    assert cosine(vecs[0], vecs[2]) < 0.5 and cosine(vecs[0], vecs[3]) < 0.5
    assert vecs == await llm.embed(texts, ctx=ctx)  # deterministic
    assert llm.backend.calls == [("embed", "embed"), ("embed", "embed")]
    assert llm.backend.requests[0]["dims"] == settings.embedding_dims
    calls = await rows(session, LlmCall)
    assert [(c.task, c.model, c.status) for c in calls] == [
        ("embed", settings.embedding_model, "ok")
    ] * 2
    assert await llm.embed([], ctx=ctx) == []
    assert len(llm.backend.calls) == 2
    steps = await rows(session, AgentStep)
    assert [(s.kind, s.agent, s.node, s.agent_run_id) for s in steps] == [
        ("llm", "embed", ctx.node, ctx.agent_run_id)
    ] * 2


async def test_cache_key_includes_schema_structure(accounted_llm, session, prompt, ctx):
    # Same __name__, different Literal enum (e.g. a competitor slug added between runs).
    old = create_model("Plan", slugs=(list[Literal["acme"]], ...))
    new = create_model("Plan", slugs=(list[Literal["acme", "globex"]], ...))
    assert old.__name__ == new.__name__
    llm = accounted_llm({TASK: [{"slugs": []}, {"slugs": ["globex"]}]})

    first = await llm.structured(TASK, "fast", old, prompt=prompt, variables=VARS, ctx=ctx)
    second = await llm.structured(TASK, "fast", new, prompt=prompt, variables=VARS, ctx=ctx)

    assert first.slugs == [] and second.slugs == ["globex"]
    assert llm.backend.calls == [(TASK, "structured")] * 2
    calls = await rows(session, LlmCall)
    assert [c.status for c in calls] == ["ok", "ok"]
    assert calls[0].request_hash != calls[1].request_hash


async def test_cache_key_includes_reasoning_effort(
    accounted_llm, test_settings_override, settings, session, prompt, ctx
):
    llm = accounted_llm({TASK: [Verdict(label="a", score=1)]})
    await llm.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)

    other_effort = "low" if settings.reasoning_effort_fast != "low" else "medium"
    llm2 = accounted_llm(
        {TASK: [Verdict(label="b", score=2)]},
        settings=test_settings_override(reasoning_effort_fast=other_effort),
    )
    out = await llm2.structured(TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=ctx)

    assert out == Verdict(label="b", score=2)
    assert llm2.backend.calls == [(TASK, "structured")]
    assert llm2.backend.requests[0]["effort"] == other_effort
    assert [c.status for c in await rows(session, LlmCall)] == ["ok", "ok"]


async def test_concurrent_calls_share_one_connection(accounted_llm, session, prompt, ctx):
    llm = accounted_llm({TASK: lambda **req: Verdict(label=req["system"][-20:], score=1)})

    outs = await asyncio.gather(
        *(
            llm.structured(
                TASK, "fast", Verdict, prompt=prompt, variables={**VARS, "topic": f"t{i}"}, ctx=ctx
            )
            for i in range(6)
        )
    )

    assert len({o.label for o in outs}) == 6
    calls = await rows(session, LlmCall)
    assert len(calls) == 6 and {c.status for c in calls} == {"ok"}
    assert sorted(s.step_no for s in await rows(session, AgentStep)) == [1, 2, 3, 4, 5, 6]
    count = (await session.execute(select(func.count()).select_from(LlmCache))).scalar_one()
    assert count == 6


async def test_steps_without_agent_run(accounted_llm, session, prompt):
    llm = accounted_llm({TASK: [Verdict(label="a", score=1)]})
    await llm.structured(
        TASK, "fast", Verdict, prompt=prompt, variables=VARS, ctx=RunCtx(None, "adhoc")
    )
    [step] = await rows(session, AgentStep)
    assert (step.agent_run_id, step.node, step.agent) == (None, "adhoc", None)
