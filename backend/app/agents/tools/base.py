"""Tool registry for the bounded tool-using agent loop. Tools are read-only."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.agents.runtime.types import ToolDef
from app.core.config import Settings


@dataclass(frozen=True)
class ToolDeps:
    session_factory: async_sessionmaker[AsyncSession]
    llm: AccountedLLM
    settings: Settings
    ctx: RunCtx | None = None  # run_tool_agent fills this in when unset

    @property
    def run_ctx(self) -> RunCtx:
        return self.ctx or RunCtx(agent_run_id=None, node="tool")


@dataclass(frozen=True)
class ToolResult:
    content: str  # text returned to the model (truncated by run_tool_agent)
    data: Any = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    func: Callable[[Any, ToolDeps], Awaitable[ToolResult]]

    def to_def(self) -> ToolDef:
        return ToolDef(self.name, self.description, self.args_model.model_json_schema())


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> ToolSpec:
    _REGISTRY[spec.name] = spec
    return spec


def get_tools(names: list[str]) -> list[ToolSpec]:
    """Specs in the given order; an unknown name raises KeyError."""
    return [_REGISTRY[n] for n in names]
