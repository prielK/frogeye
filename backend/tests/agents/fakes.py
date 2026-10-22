"""Scripted, deterministic LLM backend for tests. Never touches the network."""

import hashlib
import math
import random
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.agents.runtime.types import (
    ChatMessage,
    EmbedOut,
    StructuredOut,
    ToolDef,
    ToolTurn,
    Usage,
)

DEFAULT_USAGE = Usage(100, 0, 20)
CLUSTER_RE = re.compile(r"#cluster:([\w.-]+)")
# Per-text noise added to a cluster's base vector. With 0.01 the cosine between two
# texts of the same cluster is >= 0.96 even in the worst case (any dims).
CLUSTER_NOISE = 0.01

Script = dict[str, list[Any] | Callable[..., Any]]


class Refusal:
    """Script item: the model refuses (structured calls only)."""

    def __init__(self, reason: str = "I can't help with that.") -> None:
        self.reason = reason


def _unit(seed: str, dims: int) -> list[float]:
    rng = random.Random(hashlib.sha256(seed.encode()).digest())
    v = [rng.gauss(0.0, 1.0) for _ in range(dims)]
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def fake_embedding(text: str, dims: int) -> list[float]:
    """Hash-seeded unit vector; texts sharing `#cluster:NAME` are near-identical."""
    m = CLUSTER_RE.search(text)
    if m is None:
        return _unit("text:" + text, dims)
    base = _unit("cluster:" + m.group(1), dims)
    noise = _unit("text:" + text, dims)
    v = [b + CLUSTER_NOISE * n for b, n in zip(base, noise, strict=True)]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


class FakeBackend:
    """`LLMBackend` returning scripted values keyed by task.

    A script entry is a list (consumed in order) or a callable invoked with the request
    as keyword arguments (kind, task, model, effort, plus system/user/schema for
    structured, system/messages/tools for tool turns, dims/texts for embed).

    Structured items: a pydantic instance, a dict (validated into the schema), a
    `Refusal`, a ready `StructuredOut`, or an exception instance (raised).
    Tool-turn items: a `ToolTurn`, a str (final text, no tool calls) or an exception.
    Embed: deterministic `fake_embedding` vectors unless the task is scripted
    (items: an `EmbedOut`, a list of vectors, or an exception).
    """

    def __init__(self, script: Script | None = None, *, usage: Usage = DEFAULT_USAGE) -> None:
        self.script: Script = {
            k: list(v) if isinstance(v, list) else v for k, v in (script or {}).items()
        }
        self.usage = usage
        self.calls: list[tuple[str, str]] = []
        self.requests: list[dict[str, Any]] = []

    def _record(self, request: dict[str, Any]) -> None:
        self.calls.append((request["task"], request["kind"]))
        self.requests.append(request)

    def _next(self, request: dict[str, Any]) -> Any:
        task = request["task"]
        entry = self.script.get(task)
        if entry is None:
            raise LookupError(f"FakeBackend: nothing scripted for task {task!r}")
        if callable(entry):
            return entry(**request)
        if not entry:
            raise LookupError(f"FakeBackend: script for task {task!r} is exhausted")
        return entry.pop(0)

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
        request = {
            "kind": "structured",
            "task": task,
            "model": model,
            "effort": effort,
            "schema": schema,
            "system": system,
            "user": user,
        }
        self._record(request)
        item = self._next(request)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, StructuredOut):
            return item
        if isinstance(item, Refusal):
            return StructuredOut(value=None, usage=self.usage, model=model, refusal=item.reason)
        if isinstance(item, BaseModel) and not isinstance(item, schema):
            item = item.model_dump()
        value = schema.model_validate(item)
        return StructuredOut(value=value, usage=self.usage, model=model, refusal=None)

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
        request = {
            "kind": "tool_turn",
            "task": task,
            "model": model,
            "effort": effort,
            "system": system,
            "messages": list(messages),
            "tools": list(tools),
        }
        self._record(request)
        item = self._next(request)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return ToolTurn(content=item, tool_calls=[], usage=self.usage, model=model)
        return item

    async def embed(self, *, task: str, model: str, dims: int, texts: list[str]) -> EmbedOut:
        request = {"kind": "embed", "task": task, "model": model, "dims": dims, "texts": texts}
        self._record(request)
        if task in self.script:
            item = self._next(request)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, EmbedOut):
                return item
            return EmbedOut(vectors=item, usage=self.usage, model=model)
        vectors = [fake_embedding(t, dims) for t in texts]
        return EmbedOut(vectors=vectors, usage=self.usage, model=model)
