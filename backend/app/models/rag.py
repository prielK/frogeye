import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Computed, Index, Integer, Text, UniqueConstraint
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models._types import NOW, TimestampTZ, enum_check
from app.models.enums import RetrievalKind
from app.models.gold import EMBED_DIMS


class RetrievalItem(Base):
    __tablename__ = "retrieval_items"
    __table_args__ = (
        enum_check("kind", RetrievalKind, "ck_retrieval_items_kind"),
        UniqueConstraint("kind", "ref_id", "chunk_no", name="uq_retrieval_items_kind_ref_chunk"),
        Index(
            "ix_retrieval_items_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_retrieval_items_tsv_en", "tsv_en", postgresql_using="gin"),
        Index("ix_retrieval_items_tsv_simple", "tsv_simple", postgresql_using="gin"),
        Index("ix_retrieval_items_competitor_ids", "competitor_ids", postgresql_using="gin"),
        Index("ix_retrieval_items_published_at", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    ref_id: Mapped[int] = mapped_column(BigInteger)
    chunk_no: Mapped[int] = mapped_column(Integer, server_default=sql("0"))
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(EMBED_DIMS), nullable=True)
    tsv_en = mapped_column(TSVECTOR, Computed("to_tsvector('english', text)", persisted=True))
    tsv_simple = mapped_column(TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True))
    competitor_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), server_default=sql("'{}'::integer[]")
    )
    scope: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    reliability: Mapped[str | None] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean, server_default=sql("false"))
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)


class JfrogKbDoc(Base):
    __tablename__ = "jfrog_kb_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
