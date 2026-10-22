import datetime as dt
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models._types import NOW, TimestampTZ, enum_check
from app.models.enums import AgentRunStatus, StepKind

COST = Numeric(10, 6)
ZERO = text("0")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (enum_check("status", AgentRunStatus, "ck_agent_runs_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    graph: Mapped[str] = mapped_column(Text)
    thread_id: Mapped[str] = mapped_column(Text, unique=True)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    pipeline_run_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    started_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default=ZERO)
    tokens_out: Mapped[int] = mapped_column(Integer, server_default=ZERO)
    cost_usd: Mapped[Decimal] = mapped_column(COST, server_default=ZERO)
    notes: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        enum_check("kind", StepKind, "ck_agent_steps_kind"),
        Index("ix_agent_steps_run_step", "agent_run_id", "step_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    step_no: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(Text)
    args: Mapped[dict | None] = mapped_column(JSONB)
    result_summary: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default=ZERO)
    cached_tokens: Mapped[int] = mapped_column(Integer, server_default=ZERO)
    tokens_out: Mapped[int] = mapped_column(Integer, server_default=ZERO)
    cost_usd: Mapped[Decimal] = mapped_column(COST, server_default=ZERO)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)


class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'cached', 'error', 'refused')", name="ck_llm_calls_status"
        ),
        Index("ix_llm_calls_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    task: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=ZERO)
    cached_tokens: Mapped[int] = mapped_column(Integer, server_default=ZERO)
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=ZERO)
    cost_usd: Mapped[Decimal] = mapped_column(COST)
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)


class LlmCache(Base):
    __tablename__ = "llm_cache"

    request_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
