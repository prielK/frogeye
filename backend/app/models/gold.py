import datetime as dt
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models._types import NOW, TimestampTZ, enum_check
from app.models.enums import (
    BriefingEdition,
    BriefingStatus,
    Scope,
    SignalStatus,
    SignalType,
    Stance,
)

EMBED_DIMS = 512
_EMPTY_JSON = text("'{}'::jsonb")


def _nullable_check(column: str, enum) -> CheckConstraint:
    values = ", ".join(f"'{m.value}'" for m in enum)
    return CheckConstraint(
        f"{column} IS NULL OR {column} IN ({values})", name=f"ck_signals_{column}"
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        enum_check("scope", Scope, "ck_signals_scope"),
        _nullable_check("signal_type", SignalType),
        _nullable_check("stance", Stance),
        enum_check("status", SignalStatus, "ck_signals_status"),
        Index(
            "ix_signals_importance_first_seen", text("importance DESC"), text("first_seen_at DESC")
        ),
        Index("ix_signals_status", "status"),
        Index("ix_signals_first_seen_at", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    signal_type: Mapped[str | None] = mapped_column(Text)
    stance: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[dt.date | None] = mapped_column(Date)
    first_seen_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
    last_updated_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
    fact: Mapped[str | None] = mapped_column(Text)
    impact: Mapped[str | None] = mapped_column(Text)
    role_actions: Mapped[dict] = mapped_column(JSONB, server_default=_EMPTY_JSON)
    impact_score: Mapped[int | None] = mapped_column(SmallInteger)
    novelty_score: Mapped[int | None] = mapped_column(SmallInteger)
    importance: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    score_breakdown: Mapped[dict] = mapped_column(JSONB, server_default=_EMPTY_JSON)
    reliability: Mapped[str | None] = mapped_column(Text)
    credibility: Mapped[int | None] = mapped_column(SmallInteger)
    corroboration_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    hedged: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    centroid = mapped_column(Vector(EMBED_DIMS), nullable=True)
    analyzed_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    extraction_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)


class SignalDocument(Base):
    __tablename__ = "signal_documents"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class SignalCompetitor(Base):
    __tablename__ = "signal_competitors"
    # Migration 0008: per-competitor intel reads look signals up by competitor
    __table_args__ = (Index("ix_signal_competitors_competitor_id", "competitor_id"),)

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"), primary_key=True)


class SignalCapability(Base):
    __tablename__ = "signal_capabilities"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    capability_id: Mapped[int] = mapped_column(ForeignKey("capabilities.id"), primary_key=True)


class SignalKit(Base):
    __tablename__ = "signal_kits"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    kit_id: Mapped[int] = mapped_column(ForeignKey("kits.id"), primary_key=True)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    quote: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    match_score: Mapped[float | None] = mapped_column(Float)


class Briefing(Base):
    __tablename__ = "briefings"
    __table_args__ = (
        enum_check("edition", BriefingEdition, "ck_briefings_edition"),
        enum_check("status", BriefingStatus, "ck_briefings_status"),
        UniqueConstraint("date", "edition", "version", name="uq_briefings_date_edition_version"),
        Index("ix_briefings_date_edition", text("date DESC"), "edition"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date)
    edition: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text)
    content: Mapped[dict] = mapped_column(JSONB)
    signal_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), server_default=text("'{}'::integer[]")
    )
    funnel: Mapped[dict] = mapped_column(JSONB, server_default=_EMPTY_JSON)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), server_default=text("0"))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    published_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)


class SignalAnalysis(Base):
    """One versioned, cited stage-B analysis of a signal.

    Current means the latest version's created_at >= signals.analyzed_at.
    """

    __tablename__ = "signal_analyses"
    __table_args__ = (UniqueConstraint("signal_id", "version", name="uq_signal_analyses_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), server_default=text("0"))
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
