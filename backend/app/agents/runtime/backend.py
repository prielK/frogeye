"""The protocol every model backend implements (OpenAI in production, a scripted fake in tests)."""

from typing import Protocol

from pydantic import BaseModel

from app.agents.runtime.types import ChatMessage, EmbedOut, StructuredOut, ToolDef, ToolTurn


class LLMBackend(Protocol):
    async def structured(
        self,
        *,
        task: str,
        model: str,
        effort: str,
        schema: type[BaseModel],
        system: str,
        user: str,
    ) -> StructuredOut: ...

    async def tool_turn(
        self,
        *,
        task: str,
        model: str,
        effort: str,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef],
    ) -> ToolTurn: ...

    async def embed(self, *, task: str, model: str, dims: int, texts: list[str]) -> EmbedOut: ...
