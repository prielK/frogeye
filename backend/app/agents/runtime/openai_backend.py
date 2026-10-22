"""OpenAI backend via langchain-openai (Responses API). Only AccountedLLM may call it."""

import logging
from functools import lru_cache
from typing import Any

import tiktoken
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_openai.chat_models.base import OpenAIRefusalError
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, SecretStr, ValidationError

from app.agents.runtime.types import (
    BackendError,
    ChatMessage,
    EmbedOut,
    StructuredOut,
    ToolCall,
    ToolDef,
    ToolTurn,
    Usage,
)
from app.core.config import Settings

logger = logging.getLogger(__name__)

EMBED_BATCH = 100
# Schema keywords whose values map NAMES to schemas: their keys are never keywords.
_NAME_MAPS = frozenset({"properties", "$defs", "definitions", "patternProperties"})


# text-embedding-3-* tokenize with cl100k_base (baked into the image beside o200k_base),
# so the local usage estimate counts what the API bills.
EMBED_ENCODING = "cl100k_base"


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding(EMBED_ENCODING)


def usage_from(msg: BaseMessage) -> Usage:
    """Token usage from `AIMessage.usage_metadata` (cached = input_token_details.cache_read)."""
    meta: Any = getattr(msg, "usage_metadata", None) or {}
    details = meta.get("input_token_details") or {}
    return Usage(
        input_tokens=int(meta.get("input_tokens") or 0),
        cached_tokens=int(details.get("cache_read") or 0),
        output_tokens=int(meta.get("output_tokens") or 0),
    )


def refusal_from(raw: BaseMessage, parsing_error: BaseException | None) -> str | None:
    """The refusal text, if the model refused.

    Responses API: a `{"type": "refusal", "refusal": …}` block in `raw.content`.
    Chat Completions: `raw.additional_kwargs["refusal"]`. langchain's structured-output
    parser also reports it as an `OpenAIRefusalError` parsing error.
    """
    if isinstance(raw.content, list):
        for block in raw.content:
            if isinstance(block, dict) and block.get("type") == "refusal":
                return str(block.get("refusal") or "refused")
    if refusal := raw.additional_kwargs.get("refusal"):
        return str(refusal)
    if isinstance(parsing_error, OpenAIRefusalError):
        return str(parsing_error) or "refused"
    return None


def _const_to_enum(node: Any) -> Any:
    """Rewrite every `const: v` keyword to `enum: [v]`; property names are left alone."""
    if isinstance(node, list):
        return [_const_to_enum(v) for v in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _NAME_MAPS and isinstance(value, dict):
            out[key] = {name: _const_to_enum(sub) for name, sub in value.items()}
        elif key != "const":
            out[key] = _const_to_enum(value)
    if "const" in node:
        out.setdefault("enum", [node["const"]])
    return out


def strict_response_format(schema: type[BaseModel]) -> dict[str, Any]:
    """The strict `json_schema` response format sent for `schema`.

    The OpenAI SDK's own strict conversion, with `const` rewritten to a one-value `enum`:
    a single-value `Literal` (a one-signal briefing, a one-document story) then never
    depends on `const` support.
    """
    return {
        "name": schema.__name__,
        "schema": _const_to_enum(to_strict_json_schema(schema)),
        "strict": True,
    }


def to_lc_messages(system: str, messages: list[ChatMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = [SystemMessage(content=system)]
    for m in messages:
        if m.role == "system":
            out.append(SystemMessage(content=m.content))
        elif m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            calls = [
                {"type": "tool_call", "id": tc.id, "name": tc.name, "args": tc.args}
                for tc in m.tool_calls or []
            ]
            # Empty list content -> no empty assistant text item in the Responses input.
            out.append(AIMessage(content=m.content or [], tool_calls=calls))
        elif m.role == "tool":
            if not m.tool_call_id:
                raise ValueError("tool message needs tool_call_id")
            out.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id))
        else:
            raise ValueError(f"unknown role {m.role!r}")
    return out


class OpenAIBackend:
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not set")
        self._settings = settings
        self._api_key = SecretStr(settings.openai_api_key)
        self._chats: dict[tuple[str, str], ChatOpenAI] = {}
        self._embedders: dict[tuple[str, int], OpenAIEmbeddings] = {}

    def _chat(self, model: str, effort: str) -> ChatOpenAI:
        key = (model, effort)
        if key not in self._chats:
            self._chats[key] = ChatOpenAI(
                model=model,
                api_key=self._api_key,
                use_responses_api=True,
                reasoning={"effort": effort},
                timeout=self._settings.llm_timeout_s,
                max_retries=self._settings.llm_max_retries,
            )
        return self._chats[key]

    async def structured(
        self,
        *,
        task: str,
        model: str,
        effort: str,
        schema: type[BaseModel],
        system: str,
        user: str,
    ) -> StructuredOut:
        # The format is sent as a dict, so langchain parses JSON and we validate it.
        runnable = self._chat(model, effort).with_structured_output(
            strict_response_format(schema), method="json_schema", strict=True, include_raw=True
        )
        result = await runnable.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        raw: BaseMessage = result["raw"]
        usage = usage_from(raw)
        parsing_error = result.get("parsing_error")
        if refusal := refusal_from(raw, parsing_error):
            return StructuredOut(value=None, usage=usage, model=model, refusal=refusal)
        parsed = result.get("parsed")
        if parsing_error is not None or parsed is None:
            raise BackendError(f"unparseable structured output: {parsing_error}", usage=usage)
        try:
            value = schema.model_validate(parsed)
        except ValidationError as exc:
            raise BackendError(f"schema-invalid structured output: {exc}", usage=usage) from exc
        return StructuredOut(value=value, usage=usage, model=model, refusal=None)

    async def tool_turn(
        self,
        *,
        task: str,
        model: str,
        effort: str,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef],
    ) -> ToolTurn:
        chat = self._chat(model, effort)
        runnable = (
            chat.bind_tools(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        },
                    }
                    for t in tools
                ]
            )
            if tools
            else chat
        )
        msg = await runnable.ainvoke(to_lc_messages(system, messages))
        if not isinstance(msg, AIMessage):
            raise BackendError(f"unexpected message type {type(msg).__name__}")
        for bad in msg.invalid_tool_calls:
            logger.warning("dropping malformed tool call %s: %s", bad.get("name"), bad.get("error"))
        calls = [
            ToolCall(id=str(tc["id"]), name=tc["name"], args=dict(tc["args"]))
            for tc in msg.tool_calls
        ]
        return ToolTurn(content=str(msg.text), tool_calls=calls, usage=usage_from(msg), model=model)

    def _embeddings(self, model: str, dims: int) -> OpenAIEmbeddings:
        key = (model, dims)
        if key not in self._embedders:
            self._embedders[key] = OpenAIEmbeddings(
                model=model,
                dimensions=dims,
                api_key=self._api_key,
                timeout=self._settings.llm_timeout_s,
                max_retries=self._settings.llm_max_retries,
            )
        return self._embedders[key]

    async def embed(self, *, task: str, model: str, dims: int, texts: list[str]) -> EmbedOut:
        client = self._embeddings(model, dims)
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            vectors.extend(await client.aembed_documents(texts[i : i + EMBED_BATCH]))
        # OpenAIEmbeddings does not surface usage; estimate it locally.
        enc = _encoding()
        tokens = sum(len(enc.encode(t, disallowed_special=())) for t in texts)
        return EmbedOut(vectors=vectors, usage=Usage(input_tokens=tokens), model=model)
