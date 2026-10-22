"""Value types shared by the LLM backends and AccountedLLM."""

from dataclasses import dataclass
from typing import Any, Literal

Tier = Literal["fast", "smart"]
Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolTurn:
    content: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str


@dataclass(frozen=True)
class StructuredOut[T]:
    value: T | None
    usage: Usage
    model: str
    refusal: str | None = None


@dataclass(frozen=True)
class EmbedOut:
    vectors: list[list[float]]
    usage: Usage
    model: str


@dataclass(frozen=True)
class ToolDef:
    """A tool offered to the model; `parameters` is a JSON schema. Used only by backends."""

    name: str
    description: str
    parameters: dict[str, Any]


class BackendError(Exception):
    """A backend failure that still consumed tokens (e.g. unparseable output)."""

    def __init__(self, message: str, *, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage or Usage()
