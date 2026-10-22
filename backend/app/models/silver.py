from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models._types import NOW, TimestampTZ, enum_check
from app.models.enums import DocStatus, Scope

_SCOPES = ", ".join(f"'{m.value}'" for m in Scope)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        enum_check("status", DocStatus, "ck_documents_status"),
        CheckConstraint(
            f"scope_hint IS NULL OR scope_hint IN ({_SCOPES})", name="ck_documents_scope_hint"
        ),
        Index("ix_documents_published_id", text("published_at DESC NULLS LAST"), text("id DESC")),
        Index("ix_documents_status", "status"),
        Index("ix_documents_created_at", "created_at"),  # the "collected since" day
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(ForeignKey("raw_items.id"), unique=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    canonical_url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    author: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    clean_text: Mapped[str | None] = mapped_column(Text)
    simhash: Mapped[int | None] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'new'"))
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    scope_hint: Mapped[str | None] = mapped_column(Text)
    prefilter: Mapped[dict | None] = mapped_column(JSONB)
    triage: Mapped[dict | None] = mapped_column(JSONB)
    story_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("signals.id", ondelete="SET NULL", name="fk_documents_story_id_signals"),
    )
    embedding = mapped_column(Vector(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TimestampTZ, server_default=NOW)
    # When the company-facts extractor last read this document (set whether or not it found any).
    facts_extracted_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    # Bounded retries: the local days an extraction call failed, the last failure
    # and its reason; after MAX_FAILED_DAYS the document is given up (never selected again).
    facts_failures: Mapped[int] = mapped_column(server_default=text("0"))
    facts_failed_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    facts_error: Mapped[str | None] = mapped_column(Text)


class DocumentCompetitor(Base):
    __tablename__ = "document_competitors"

    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"), primary_key=True)
    salience: Mapped[float] = mapped_column(server_default=text("1.0"))
