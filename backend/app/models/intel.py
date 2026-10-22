"""Competitor intelligence: job postings, company facts, competitor products, weekly briefs."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models._types import NOW, TimestampTZ, enum_check, text_array
from app.models.enums import FactKind, FactSourceKind


class JobPosting(Base):
    """One public job-board posting, tracked across daily snapshots of its board.

    Open on day D: ``first_seen_at <= D`` and (``closed_at`` is null or ``closed_at > D``).
    A posting is closed only by a successful, complete fetch of its board that lacks it.
    A changed ``content_hash`` (title + description) clears its tags so it is tagged again.
    """

    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_job_postings_source_external"),
        Index("ix_job_postings_competitor_closed", "competitor_id", "closed_at"),
        Index("ix_job_postings_competitor_first_seen", "competitor_id", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"))
    ats: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    department_raw: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str] = mapped_column(Text)  # collectors.jobs.taxonomy.DEPARTMENTS
    team: Mapped[str | None] = mapped_column(Text)
    seniority: Mapped[str] = mapped_column(Text)  # collectors.jobs.taxonomy.SENIORITIES
    location: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    remote: Mapped[bool] = mapped_column(server_default=text("false"))
    employment_type: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)  # from the ATS, if given
    first_seen_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
    last_seen_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
    closed_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    content_hash: Mapped[str] = mapped_column(Text)
    description_text: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = text_array()  # capability slugs
    tag_version: Mapped[str | None] = mapped_column(Text)
    tagged_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    # Bounded retries: the local days a tagging call failed, the last failure and its
    # reason. After MAX_FAILED_DAYS the posting is given up (never selected again) until its
    # content changes, which clears all three.
    tag_failures: Mapped[int] = mapped_column(server_default=text("0"))
    tag_failed_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    tag_error: Mapped[str | None] = mapped_column(Text)


class CompanyFact(Base):
    """One verified, quoted statement about a competitor (funding round, headcount, ...)."""

    __tablename__ = "company_facts"
    __table_args__ = (
        enum_check("kind", FactKind, "ck_company_facts_kind"),
        enum_check("source_kind", FactSourceKind, "ck_company_facts_source_kind"),
        Index("ix_company_facts_competitor_kind_as_of", "competitor_id", "kind", "as_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"))
    kind: Mapped[str] = mapped_column(Text)
    value_num: Mapped[float | None]
    unit: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    label_norm: Mapped[str] = mapped_column(Text)
    as_of: Mapped[dt.date | None] = mapped_column(Date)
    source_kind: Mapped[str] = mapped_column(Text)
    source_id: Mapped[int] = mapped_column(Integer)  # documents.id or page_snapshots.id
    quote: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(server_default=text("false"))
    extractor_version: Mapped[str] = mapped_column(Text)
    extracted_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)


class CompetitorProduct(Base):
    """A competitor's main product and the JFrog products it competes with (curated seed)."""

    __tablename__ = "competitor_products"
    __table_args__ = (
        UniqueConstraint("competitor_id", "name", name="uq_competitor_products_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"))
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    jfrog_products: Mapped[list[str]] = text_array()


class CompetitorBrief(Base):
    """One versioned, cited "bottom line for JFrog" about a competitor."""

    __tablename__ = "competitor_briefs"
    __table_args__ = (
        UniqueConstraint("competitor_id", "version", name="uq_competitor_briefs_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), server_default=text("0"))
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)


class ProductBrief(Base):
    """One versioned, cited brief on the competitive landscape around a JFrog product."""

    __tablename__ = "product_briefs"
    __table_args__ = (UniqueConstraint("product", "version", name="uq_product_briefs_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product: Mapped[str] = mapped_column(Text)  # a JFROG_PRODUCTS name
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), server_default=text("0"))
    created_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, server_default=NOW)
