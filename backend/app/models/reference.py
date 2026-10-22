from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as sa_relationship

from app.core.db import Base
from app.models._types import TimestampTZ, enum_check, text_array
from app.models.enums import Relationship, Reliability, Role, SourceHealth, SourceKind


class Competitor(Base):
    __tablename__ = "competitors"
    __table_args__ = (
        CheckConstraint("tier BETWEEN 0 AND 3", name="ck_competitors_tier"),
        enum_check("relationship", Relationship, "ck_competitors_relationship"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    tier: Mapped[int] = mapped_column(SmallInteger)  # 0 = self (JFrog)
    relationship: Mapped[str] = mapped_column(Text)
    is_self: Mapped[bool] = mapped_column(server_default=text("false"))
    website: Mapped[str | None] = mapped_column(Text)
    ticker: Mapped[str | None] = mapped_column(Text)
    cik: Mapped[str | None] = mapped_column(Text)
    github_orgs: Mapped[list[str]] = text_array()
    aliases: Mapped[list[str]] = text_array()
    ambiguous_aliases: Mapped[list[str]] = text_array()
    negative_keywords: Mapped[list[str]] = text_array()
    disambiguation_hint: Mapped[str | None] = mapped_column(Text)
    logo_path: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class CapabilityCategory(Base):
    __tablename__ = "capability_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    order: Mapped[int] = mapped_column(server_default=text("0"))


class Capability(Base):
    __tablename__ = "capabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("capability_categories.id"))
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(server_default=text("1.0"))


class Kit(Base):
    __tablename__ = "kits"
    __table_args__ = (CheckConstraint("priority BETWEEN 1 AND 3", name="ck_kits_priority"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(SmallInteger)
    owner_roles: Mapped[list[str]] = text_array()
    keywords: Mapped[list[str]] = text_array()


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        enum_check("kind", SourceKind, "ck_sources_kind"),
        enum_check("reliability", Reliability, "ck_sources_reliability"),
        enum_check("health", SourceHealth, "ck_sources_health"),
        UniqueConstraint("kind", "url", name="uq_sources_kind_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    competitor_id: Mapped[int | None] = mapped_column(ForeignKey("competitors.id"))
    reliability: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    consecutive_failures: Mapped[int] = mapped_column(server_default=text("0"))
    # Successful fetches in a row that listed nothing (job boards: a single empty
    # listing closes no posting; the second in a row does). Runtime state, never seeded.
    consecutive_empty: Mapped[int] = mapped_column(server_default=text("0"))
    # Job boards: when the board was first listed in full (`FetchResult.postings_complete`).
    # Only then are its open roles measured: a partial first listing does not count.
    first_complete_fetch_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    # Job boards: the latest complete listing. A board not listed in full
    # for `board_stale_days` serves no current numbers; its series end at this day.
    last_complete_fetch_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    # When a request for this source was last started, success or not:
    # a job board or watched page is fetched at most once per local day
    # (`app.collectors.daily_guard`). Set before the request, so a crash still counts.
    last_attempt_at: Mapped[datetime | None] = mapped_column(TimestampTZ)
    health: Mapped[str] = mapped_column(Text, server_default=text("'ok'"))
    last_error: Mapped[str | None] = mapped_column(Text)

    # Eager so async collectors can read it; the check_sources CLI assigns a transient one.
    competitor: Mapped[Competitor | None] = sa_relationship(lazy="joined")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (enum_check("role", Role, "ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
