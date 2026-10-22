import datetime as dt

from sqlalchemy import CheckConstraint, Date, ForeignKey, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models._types import NOW, TimestampTZ, enum_check
from app.models.enums import RunStatus


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (enum_check("status", RunStatus, "ck_pipeline_runs_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    stats: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)


class MetricPoint(Base):
    __tablename__ = "metric_points"

    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"), primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[float]


class HostBlock(Base):
    """Per-host circuit breaker state: strikes and the end of the current pause."""

    __tablename__ = "host_blocks"

    host: Mapped[str] = mapped_column(Text, primary_key=True)
    strikes: Mapped[int] = mapped_column(server_default=text("0"))
    blocked_until: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    last_status: Mapped[int | None]
    updated_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)


class DemoSnapshot(Base):
    """When the demo snapshot this database was restored from was captured (`app.snapshot`).

    At most one row; empty unless the database started from the bundled snapshot."""

    __tablename__ = "demo_snapshot"
    __table_args__ = (CheckConstraint("id = 1", name="ck_demo_snapshot_single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"))
    captured_at: Mapped[dt.datetime] = mapped_column(TimestampTZ)
    restored_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
