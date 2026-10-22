import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.agents.runtime.accounted import LLMError
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt
from app.agents.runtime.types import BackendError, ChatMessage, ToolCall, ToolDef, ToolTurn, Usage
from evals.recorder import (
    NotRecorded,
    RecordingBackend,
    case_scope,
    decode_vector,
    encode_vector,
    structured_key,
    suite_scope,
    tool_turn_key,
)
from tests.agents.fakes import FakeBackend, fake_embedding

FIXTURE_PROMPTS = Path(__file__).resolve().parent.parent / "fixtures" / "prompts"
VARS = {"topic": "model registry", "doc": "Acme ships a registry."}
CTX = RunCtx(agent_run_id=None, node="eval")
TOOLS = [ToolDef("lookup", "Look a vendor up.", {"type": "object", "properties": {}})]


class Verdict(BaseModel):
    label: str
    score: int


def request(**overrides):
    req = {
        "task": "triage",
        "model": "gpt-5-nano",
        "effort": "minimal",
        "schema": Verdict,
        "system": "You triage documents.",
        "user": "Title: Harness raises a round",
    }
    return req | overrides


def test_same_request_same_key_and_any_change_is_a_new_key():
    key = structured_key(**request())
    assert key == structured_key(**request())
    for change in (
        {"user": "Title: Harness raises a round."},
        {"system": "You triage documents!"},
        {"model": "gpt-6-luna"},
        {"effort": "low"},
        {"task": "disambiguate"},
    ):
        assert structured_key(**request(**change)) != key, change

    class Wider(BaseModel):
        label: str
        score: int
        reason: str

    wider = request(schema=Wider)
    assert structured_key(**wider) != key  # the schema's structure is part of the key
    msgs = [ChatMessage("user", "hello")]
    turn = tool_turn_key(task="t", model="m", effort="low", system="s", messages=msgs, tools=TOOLS)
    assert turn != tool_turn_key(
        task="t", model="m", effort="low", system="s", messages=msgs, tools=[]
    )


def test_vectors_round_trip_through_float16():
    vector = fake_embedding("Harness acquires Augment Code", 512)
    back = decode_vector(encode_vector(vector))
    assert len(back) == 512
    assert max(abs(a - b) for a, b in zip(vector, back, strict=True)) < 1e-3
    assert decode_vector(encode_vector(back)) == back  # stable once decoded
    assert len(encode_vector(vector)) < 1400


def live_backend():
    def turn(**kw):
        return ToolTurn(
            content="",
            tool_calls=[ToolCall(id="c1", name="lookup", args={"q": "harness"})],
            usage=Usage(50, 0, 5),
            model=kw["model"],
        )

    return FakeBackend(
        {"triage": [Verdict(label="relevant", score=3)], "disambiguate": turn},
        usage=Usage(1000, 0, 100),
    )


async def test_record_then_replay_answers_identically_at_zero_cost(tmp_path):
    live = live_backend()
    rec = RecordingBackend(tmp_path, "record", live)
    with suite_scope("triage"):
        out = await rec.structured(**request())
        msgs = [ChatMessage("user", "Which Harness?")]
        turn = await rec.tool_turn(
            task="disambiguate", model="m", effort="low", system="s", messages=msgs, tools=TOOLS
        )
        vectors = (await rec.embed(task="embed", model="e", dims=512, texts=["a", "b"])).vectors
    assert out.value == Verdict(label="relevant", score=3) and out.usage == Usage(1000, 0, 100)
    assert rec.live_calls == 3 and rec.live_usd > 0
    lines = (tmp_path / "triage.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4  # one structured, one tool turn, one line per embedded text

    replay = RecordingBackend(tmp_path, "replay")
    again = await replay.structured(**request())
    assert again.value == out.value and again.usage == Usage()
    turn2 = await replay.tool_turn(
        task="disambiguate", model="m", effort="low", system="s", messages=msgs, tools=TOOLS
    )
    assert turn2.tool_calls == turn.tool_calls
    # per-text keys: the batch's make-up does not matter
    one = await replay.embed(task="search_embed", model="e", dims=512, texts=["b"])
    assert one.vectors == [vectors[1]]
    assert replay.live_calls == 0 and replay.replayed_calls == 3
    assert replay.replayed_usd > 0  # what the replayed answers cost when recorded


async def test_replay_miss_is_logged_on_the_case_and_never_calls_a_model(tmp_path):
    replay = RecordingBackend(tmp_path, "replay")
    with case_scope() as log:
        with pytest.raises(NotRecorded):
            await replay.structured(**request(user="a prompt nobody recorded"))
        with pytest.raises(NotRecorded):
            await replay.embed(task="embed", model="e", dims=512, texts=["x"])
    assert len(log.misses) == 2 and replay.misses == 2
    assert not list(tmp_path.glob("*.jsonl"))
    with pytest.raises(ValueError):
        RecordingBackend(tmp_path, "record")  # record mode needs a live backend


async def test_a_changed_prompt_is_a_miss_through_accounted_llm(tmp_path, accounted_llm):
    prompt = load_prompt("echo", directory=FIXTURE_PROMPTS)
    live = FakeBackend({"t.verdict": [Verdict(label="x", score=1)]})
    llm = accounted_llm(backend=RecordingBackend(tmp_path, "record", live))
    with suite_scope("unit"):
        await llm.structured("t.verdict", "fast", Verdict, prompt=prompt, variables=VARS, ctx=CTX)

    replay = accounted_llm(backend=RecordingBackend(tmp_path, "replay"))
    got = await replay.structured(
        "t.verdict", "fast", Verdict, prompt=prompt, variables=VARS, ctx=CTX, cache=False
    )
    assert got == Verdict(label="x", score=1)
    changed = {**VARS, "doc": "Acme ships a registry!"}
    with case_scope() as log, pytest.raises(LLMError, match="not recorded"):
        await replay.structured(
            "t.verdict", "fast", Verdict, prompt=prompt, variables=changed, ctx=CTX
        )
    assert log.misses == ["t.verdict (structured)"]


async def test_record_stops_live_calls_at_its_budget(tmp_path):
    live = FakeBackend({"triage": lambda **kw: Verdict(label="y", score=2)}, usage=Usage(10**6))
    rec = RecordingBackend(tmp_path, "record", live, budget_usd=0.01)
    await rec.structured(**request())  # $0.05 at gpt-5-nano input rates
    with case_scope() as log, pytest.raises(NotRecorded, match="budget"):
        await rec.structured(**request(user="another document"))
    assert len(log.misses) == 1 and rec.live_calls == 1
    assert (await rec.structured(**request())).value.label == "y"  # recorded: still answered


async def test_unusable_output_is_recorded_and_replayed_as_the_same_failure(tmp_path):
    bad = BackendError("schema-invalid structured output", usage=Usage(10, 0, 2))
    rec = RecordingBackend(tmp_path, "record", FakeBackend({"triage": [bad]}))
    with pytest.raises(BackendError):
        await rec.structured(**request())
    with pytest.raises(BackendError, match="schema-invalid"):
        await RecordingBackend(tmp_path, "replay").structured(**request())


async def test_prune_keeps_only_what_the_run_used_in_the_files_it_owns(tmp_path):
    rec = RecordingBackend(tmp_path, "record", live_backend())
    with suite_scope("triage"):
        await rec.structured(**request())
    stale = {"key": "old", "kind": "embed", "task": "embed", "model": "e", "usage": [1, 0, 0]}
    other = {**stale, "key": "kept"}
    (tmp_path / "old.jsonl").write_text(json.dumps(stale) + "\n", encoding="utf-8")
    (tmp_path / "experiments.jsonl").write_text(json.dumps(other) + "\n", encoding="utf-8")
    run = RecordingBackend(tmp_path, "replay")
    await run.structured(**request())
    assert run.prune({"triage", "old"}) == 1
    assert not (tmp_path / "old.jsonl").exists()
    assert len((tmp_path / "triage.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert (tmp_path / "experiments.jsonl").exists()  # not a file this run owns


async def test_adopt_gives_an_experiment_its_own_copy(tmp_path):
    rec = RecordingBackend(tmp_path, "record", live_backend())
    with suite_scope("fact_checker"):
        await rec.structured(**request())
    run = RecordingBackend(tmp_path, "replay")
    await run.structured(**request())
    assert run.adopt(set(run.used), "experiments") == 1
    assert run.prune({"fact_checker"}) == 0  # used, but now at home in experiments.jsonl
    assert not (tmp_path / "fact_checker.jsonl").exists()
    again = RecordingBackend(tmp_path, "replay")
    assert (await again.structured(**request())).value.label == "relevant"
