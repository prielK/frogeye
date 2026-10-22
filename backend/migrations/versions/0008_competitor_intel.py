"""competitor intelligence: job postings, company facts, products, briefs, page judgements

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

FACT_KINDS = (
    "funding_round",
    "valuation",
    "headcount",
    "layoff",
    "revenue",
    "arr",
    "customer",
    "customer_count",
    "market_share",
    "acquisition",
    "leadership_change",
    "partnership",
    "office",
)
FACT_SOURCE_KINDS = ("document", "page_snapshot")
PAGE_KINDS = ("pricing", "about", "careers")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _now() -> sa.TextClause:
    return sa.text("now()")


def _brief_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", pg.JSONB(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("ats", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("department_raw", sa.Text(), nullable=True),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column("seniority", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("remote", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("employment_type", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            pg.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("tag_version", sa.Text(), nullable=True),
        sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_job_postings_source_external"),
    )
    op.create_index(
        "ix_job_postings_competitor_closed", "job_postings", ["competitor_id", "closed_at"]
    )
    op.create_index(
        "ix_job_postings_competitor_first_seen", "job_postings", ["competitor_id", "first_seen_at"]
    )

    op.create_table(
        "company_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("label_norm", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column(
            "extracted_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.CheckConstraint(f"kind IN ({_in(FACT_KINDS)})", name="ck_company_facts_kind"),
        sa.CheckConstraint(
            f"source_kind IN ({_in(FACT_SOURCE_KINDS)})", name="ck_company_facts_source_kind"
        ),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_facts_competitor_kind_as_of",
        "company_facts",
        ["competitor_id", "kind", "as_of"],
    )

    op.create_table(
        "competitor_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "jfrog_products",
            pg.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("competitor_id", "name", name="uq_competitor_products_name"),
    )

    op.create_table(
        "competitor_briefs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        *_brief_columns(),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("competitor_id", "version", name="uq_competitor_briefs_version"),
    )
    op.create_table(
        "product_briefs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        *_brief_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product", "version", name="uq_product_briefs_version"),
    )

    # page_snapshots (unused until page watch): what kind of page, and the change judgement
    op.add_column("page_snapshots", sa.Column("page_kind", sa.Text(), nullable=True))
    op.add_column("page_snapshots", sa.Column("meaningful", sa.Boolean(), nullable=True))
    op.add_column("page_snapshots", sa.Column("change_kind", sa.Text(), nullable=True))
    op.add_column("page_snapshots", sa.Column("change_summary", sa.Text(), nullable=True))
    op.add_column(
        "page_snapshots", sa.Column("judged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "page_snapshots",
        sa.Column("facts_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_page_snapshots_page_kind",
        "page_snapshots",
        f"page_kind IS NULL OR page_kind IN ({_in(PAGE_KINDS)})",
    )
    op.create_index(
        "ix_page_snapshots_source_fetched", "page_snapshots", ["source_id", "fetched_at"]
    )
    # the competitor intel aggregates look signals up by competitor; the PK
    # (signal_id, competitor_id) cannot serve that
    op.create_index("ix_signal_competitors_competitor_id", "signal_competitors", ["competitor_id"])

    op.add_column(
        "documents", sa.Column("facts_extracted_at", sa.DateTime(timezone=True), nullable=True)
    )
    # empty listings in a row; a job board's postings close on the second one
    op.add_column(
        "sources",
        sa.Column("consecutive_empty", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sources", "consecutive_empty")
    op.drop_column("documents", "facts_extracted_at")

    op.drop_index("ix_signal_competitors_competitor_id", table_name="signal_competitors")
    op.drop_index("ix_page_snapshots_source_fetched", table_name="page_snapshots")
    op.drop_constraint("ck_page_snapshots_page_kind", "page_snapshots", type_="check")
    for column in (
        "facts_extracted_at",
        "judged_at",
        "change_summary",
        "change_kind",
        "meaningful",
        "page_kind",
    ):
        op.drop_column("page_snapshots", column)

    op.drop_table("product_briefs")
    op.drop_table("competitor_briefs")
    op.drop_table("competitor_products")
    op.drop_index("ix_company_facts_competitor_kind_as_of", table_name="company_facts")
    op.drop_table("company_facts")
    op.drop_index("ix_job_postings_competitor_first_seen", table_name="job_postings")
    op.drop_index("ix_job_postings_competitor_closed", table_name="job_postings")
    op.drop_table("job_postings")
