"""Where an LLM call happens: which agent run, graph node and agent (for the step trace)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RunCtx:
    agent_run_id: int | None
    node: str
    agent: str | None = None
