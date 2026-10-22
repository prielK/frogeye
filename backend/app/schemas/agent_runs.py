from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentRunOut(BaseModel):
    id: int
    graph: str
    trigger: str
    status: str = Field(description="running | succeeded | failed | interrupted")
    started_at: datetime
    finished_at: datetime | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    notes: str | None = Field(
        description='"; "-joined flags and node errors, e.g. "llm_disabled". For non-analysts '
        'the node errors are folded into one "stage_errors" flag (they can quote model '
        "output)."
    )
    error: str | None = Field(
        description="Why the run failed; for non-analysts only the error's class name."
    )
    pipeline_run_id: int | None
    funnel: dict[str, Any] | None = Field(
        description="The run's funnel counts so far: written after every stage while the run "
        "is going and final once it finishes; null before the first stage."
    )
    briefing_date: date | None = Field(
        description="The local date of the briefing this run filed (read it at "
        "/briefings/{date}); null when the run filed none."
    )


class AgentStepOut(BaseModel):
    id: int
    step_no: int
    node: str
    agent: str | None
    kind: str = Field(description="llm | tool | node")
    tool_name: str | None
    args: dict[str, Any] | None = Field(
        description="The call's arguments, for analysts only: null for every other role."
    )
    result_summary: str | None = Field(
        description="What the step returned, for analysts only: null for every other role "
        "(results quote signal titles and facts whatever their status)."
    )
    status: Literal["ok", "error", "refused", "budget"] = Field(
        description="How the step ended, for every role: an error, a model refusal, or a "
        "stop at the LLM budget."
    )
    error_kind: str | None = Field(
        description="The error's class name (e.g. LLMError) when status is error and it is "
        "known; else null."
    )
    model: str | None
    tokens_in: int
    cached_tokens: int
    tokens_out: int
    cost_usd: float
    latency_ms: int | None
    created_at: datetime
