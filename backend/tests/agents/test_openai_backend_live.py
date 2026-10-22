"""Real OpenAI calls (a fraction of a cent). Run with RUN_LIVE_LLM=1 and OPENAI_API_KEY set:
`pytest -m live_llm -v -s`."""

import os

import pytest
from pydantic import BaseModel

from app.agents.runtime.openai_backend import OpenAIBackend
from app.agents.runtime.pricing import cost_usd
from app.agents.runtime.types import ChatMessage, ToolDef
from app.core.config import get_settings

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        not (os.environ.get("RUN_LIVE_LLM") == "1" and os.environ.get("OPENAI_API_KEY")),
        reason="live LLM test: needs RUN_LIVE_LLM=1 and OPENAI_API_KEY",
    ),
]


class Pong(BaseModel):
    reply: str


def _report(label: str, model: str, usage) -> None:
    cost = cost_usd(model, usage.input_tokens, usage.cached_tokens, usage.output_tokens)
    print(
        f"\n[live] {label} model={model} in={usage.input_tokens} cached={usage.cached_tokens}"
        f" out={usage.output_tokens} cost=${cost:.6f}"
    )


async def test_live_structured_nano_and_luna():
    s = get_settings()
    backend = OpenAIBackend(s)
    for tier, model, effort in (
        ("fast", s.model_fast, s.reasoning_effort_fast),
        ("smart", s.model_smart, s.reasoning_effort_smart),
    ):
        out = await backend.structured(
            task="live.ping",
            model=model,
            effort=effort,
            schema=Pong,
            system="You answer in the requested JSON format.",
            user="Reply with the single word pong.",
        )
        _report(f"structured/{tier} effort={effort}", model, out.usage)
        assert out.refusal is None
        assert isinstance(out.value, Pong) and out.value.reply
        assert out.usage.input_tokens > 0 and out.usage.output_tokens > 0


async def test_live_tool_turn_roundtrip_and_embed():
    s = get_settings()
    backend = OpenAIBackend(s)
    tools = [
        ToolDef(
            "get_version",
            "Look up the latest released version of a product.",
            {
                "type": "object",
                "properties": {"product": {"type": "string"}},
                "required": ["product"],
                "additionalProperties": False,
            },
        )
    ]
    system = "Use the get_version tool to answer. Then answer in one short sentence."
    messages = [ChatMessage("user", "What is the latest version of Artifactory?")]
    first = await backend.tool_turn(
        task="live.tool",
        model=s.model_fast,
        effort=s.reasoning_effort_fast,
        system=system,
        messages=messages,
        tools=tools,
    )
    _report("tool_turn/1", s.model_fast, first.usage)
    assert first.tool_calls and first.tool_calls[0].name == "get_version"
    call = first.tool_calls[0]
    messages += [
        ChatMessage("assistant", first.content, tool_calls=first.tool_calls),
        ChatMessage("tool", "7.111.4", tool_call_id=call.id),
    ]
    second = await backend.tool_turn(
        task="live.tool",
        model=s.model_fast,
        effort=s.reasoning_effort_fast,
        system=system,
        messages=messages,
        tools=tools,
    )
    _report("tool_turn/2", s.model_fast, second.usage)
    assert not second.tool_calls and "7.111.4" in second.content

    emb = await backend.embed(
        task="live.embed", model=s.embedding_model, dims=s.embedding_dims, texts=["a", "b c"]
    )
    _report("embed", s.embedding_model, emb.usage)
    assert [len(v) for v in emb.vectors] == [s.embedding_dims] * 2
    assert emb.usage.input_tokens > 0
