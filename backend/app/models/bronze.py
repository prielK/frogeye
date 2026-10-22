from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models._types import NOW, TimestampTZ
from app.models.enums import PageKind

ZERO = text("0")


class RawItem(Base):
    __tablename__ = "raw_items"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_raw_items_source_external"),
        Index("ix_raw_items_source_fetched", "source_id", "fetched_at"),
        Index(
            "ix_raw_items_unnormalized",
            "id",
            postgresql_where=text("normalized_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    fetched_at: Mapped[datetime] = mapped_column(TimestampTZ, server_default=NOW)
    url: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    content_hash: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    raw_text: Mapped[str | None] = mapped_column(Text)
    normalized_at: Mapped[datetime | None] = mapped_column(TimestampTZ)


_PAGE_KINDS = ", ".join(f"'{m.value}'" for m in PageKind)


class PageSnapshot(Base):
    """A watched page's main text, stored when it changes, and the judgement of that change."""

    __tablename__ = "page_snapshots"
    __table_args__ = (
        CheckConstraint(
            f"page_kind IS NULL OR page_kind IN ({_PAGE_KINDS})",
            name="ck_page_snapshots_page_kind",
        ),
        Index("ix_page_snapshots_source_fetched", "source_id", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    fetched_at: Mapped[datetime] = mapped_column(TimestampTZ, server_default=NOW)
    text_hash: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    diff_from_prev: Mapped[str | None] = mapped_column(Text)
    page_kind: Mapped[str | None] = mapped_column(Text)  # PageKind
    meaningful: Mapped[bool | None]
    change_kind: Mapped[str | None] = mapped_column(Text)
    change_summary: Mapped[str | None] = mapped_column(Text)
    judged_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    facts_extracted_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    # Bounded retries, as on documents (`ZERO`: the `text` column shadows text()).
    facts_failures: Mapped[int] = mapped_column(server_default=ZERO)
    facts_failed_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    facts_error: Mapped[str | None] = mapped_column(Text)
    # Bounded retries of the page-change judge, as for the facts above.
    judge_failures: Mapped[int] = mapped_column(server_default=ZERO)
    judge_failed_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    judge_error: Mapped[str | None] = mapped_column(Text)
