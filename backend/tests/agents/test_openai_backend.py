"""Offline checks of the OpenAI backend's response handling (no network)."""

from typing import Literal

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict

from app.agents.runtime.openai_backend import (
    OpenAIBackend,
    refusal_from,
    strict_response_format,
    to_lc_messages,
    usage_from,
)
from app.agents.runtime.types import BackendError, ChatMessage, ToolCall, Usage


def test_usage_reads_cache_read_details():
    msg = AIMessage(
        content=[],
        usage_metadata={
            "input_tokens": 1200,
            "output_tokens": 40,
            "total_tokens": 1240,
            "input_token_details": {"cache_read": 1024},
        },
    )
    assert usage_from(msg) == Usage(1200, 1024, 40)
    assert usage_from(AIMessage(content="x")) == Usage()


def test_refusal_detected_from_responses_content_block():
    raw = AIMessage(content=[{"type": "refusal", "refusal": "I can't help with that."}])
    assert refusal_from(raw, None) == "I can't help with that."
    legacy = AIMessage(content="", additional_kwargs={"refusal": "nope"})
    assert refusal_from(legacy, None) == "nope"
    ok = AIMessage(content=[{"type": "text", "text": '{"a": 1}'}])
    assert refusal_from(ok, None) is None


def test_chat_messages_convert_to_langchain():
    msgs = to_lc_messages(
        "sys",
        [
            ChatMessage("user", "q"),
            ChatMessage("assistant", "", tool_calls=[ToolCall("c1", "search", {"q": "x"})]),
            ChatMessage("tool", "result", tool_call_id="c1"),
        ],
    )
    assert [type(m) for m in msgs] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    assert msgs[2].tool_calls[0]["id"] == "c1" and msgs[2].tool_calls[0]["args"] == {"q": "x"}
    assert msgs[3].tool_call_id == "c1"


def test_clients_cached_per_model_settings(test_settings_override):
    backend = OpenAIBackend(test_settings_override(openai_api_key="sk-test-not-real"))
    emb = backend._embeddings("text-embedding-3-small", 512)
    assert backend._embeddings("text-embedding-3-small", 512) is emb
    assert backend._embeddings("text-embedding-3-small", 256) is not emb
    assert emb.dimensions == 512
    chat = backend._chat("gpt-5-nano", "minimal")
    assert backend._chat("gpt-5-nano", "minimal") is chat
    assert backend._chat("gpt-5-nano", "low") is not chat


def test_backend_requires_api_key(test_settings_override):
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIBackend(test_settings_override(openai_api_key=None))


class SingleIds(BaseModel):
    """Single-value Literals (a one-signal briefing, a one-doc story) emit `const`."""

    model_config = ConfigDict(extra="forbid")

    signal_id: Literal[7]
    sentence_ids: list[Literal["S1"]]
    const: str  # a field that happens to be named "const" is a property, not the keyword


def keyword_consts(node) -> list:
    """Every `const` used as a JSON-schema keyword (property names do not count)."""
    if isinstance(node, list):
        return [c for v in node for c in keyword_consts(v)]
    if not isinstance(node, dict):
        return []
    found = []
    for key, value in node.items():
        if key == "properties":
            found += [c for sub in value.values() for c in keyword_consts(sub)]
            continue
        if key == "const":
            found.append(value)
        found += keyword_consts(value)
    return found


def test_strict_response_format_rewrites_const_to_enum():
    fmt = strict_response_format(SingleIds)
    assert (fmt["name"], fmt["strict"]) == ("SingleIds", True)
    schema = fmt["schema"]
    assert keyword_consts(schema) == []
    assert schema["properties"]["signal_id"]["enum"] == [7]
    assert schema["properties"]["sentence_ids"]["items"]["enum"] == ["S1"]
    assert schema["properties"]["const"]["type"] == "string"  # the property survives
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["const", "sentence_ids", "signal_id"]


def test_strict_response_format_reaches_the_responses_payload(test_settings_override):
    chat = ChatOpenAI(model="gpt-5-nano", api_key="sk-test-not-real", use_responses_api=True)
    fmt = strict_response_format(SingleIds)
    payload = chat._get_request_payload([HumanMessage(content="x")], response_format=fmt)
    sent = payload["text"]["format"]
    assert (sent["type"], sent["name"], sent["strict"]) == ("json_schema", "SingleIds", True)
    assert sent["schema"] == fmt["schema"] and keyword_consts(sent) == []


class _FakeRunnable:
    def __init__(self, parsed):
        self.parsed = parsed

    async def ainvoke(self, messages):
        raw = AIMessage(
            content=[{"type": "text", "text": "{}"}],
            usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55},
        )
        return {"raw": raw, "parsed": self.parsed, "parsing_error": None}


class _FakeChat:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def with_structured_output(self, schema, **kwargs):
        self.calls.append((schema, kwargs))
        return _FakeRunnable(self.parsed)


async def _structured(backend, monkeypatch, parsed):
    chat = _FakeChat(parsed)
    monkeypatch.setattr(backend, "_chat", lambda model, effort: chat)
    out = await backend.structured(
        task="t", model="gpt-5-nano", effort="minimal", schema=SingleIds, system="s", user="u"
    )
    return out, chat


async def test_structured_sends_enum_schema_and_validates_the_dict(
    test_settings_override, monkeypatch
):
    backend = OpenAIBackend(test_settings_override(openai_api_key="sk-test-not-real"))
    parsed = {"signal_id": 7, "sentence_ids": ["S1"], "const": "x"}
    out, chat = await _structured(backend, monkeypatch, parsed)
    assert out.value == SingleIds(**parsed) and out.refusal is None
    assert out.usage == Usage(50, 0, 5)
    [(sent, kwargs)] = chat.calls
    assert sent == strict_response_format(SingleIds)
    assert kwargs == {"method": "json_schema", "strict": True, "include_raw": True}


async def test_structured_schema_invalid_dict_is_a_backend_error_with_usage(
    test_settings_override, monkeypatch
):
    backend = OpenAIBackend(test_settings_override(openai_api_key="sk-test-not-real"))
    with pytest.raises(BackendError) as ei:
        await _structured(backend, monkeypatch, {"signal_id": 8, "sentence_ids": [], "const": ""})
    assert ei.value.usage == Usage(50, 0, 5)  # tokens were spent: they are still accounted


def test_embedding_usage_is_estimated_with_the_embedding_models_tokenizer():
    """Text-embedding-3-* bill cl100k_base tokens, not o200k_base ones."""
    from app.agents.runtime import openai_backend

    assert openai_backend._encoding().name == "cl100k_base"
