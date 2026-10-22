"""competitor intelligence carries: complete board listings, bounded LLM retries

- `sources.first_complete_fetch_at`: when a job board was first listed in full. Only a complete
  listing measures a board: a partial first Greenhouse page, or discovery's probe of
  one, no longer counts. Existing boards are backfilled with the rule they were read by
  before (any successful fetch counted): their earliest posting, else their last fetch.
- `job_postings.tag_*`, `documents/page_snapshots.facts_*` and `page_snapshots.judge_*`: the
  days an LLM call for the item failed, the last of them and its reason; after 3 failed days
  the item is given up.
- `ix_documents_created_at`: the "collected since" day is the first document's.
- `sources.last_complete_fetch_at`: a job board's latest complete listing.
  A board not listed in full for `board_stale_days` serves no current numbers, and its
  series end at that day, never carried flat through days FrogEye did not read.
- `sources.last_attempt_at`: when a request for the source was last
  started. A job board or watched page is fetched at most once per local day, whatever runs.
  Both are backfilled from `last_fetched_at`, by the rule existing boards were read by (any
  successful fetch counted as complete).

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_RETRIES = (
    ("job_postings", "tag"),
    ("documents", "facts"),
    ("page_snapshots", "facts"),
    ("page_snapshots", "judge"),
)


def upgrade() -> None:
    op.add_column(
        "sources", sa.Column("first_complete_fetch_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE sources s SET first_complete_fetch_at = COALESCE(
            (SELECT min(jp.first_seen_at) FROM job_postings jp WHERE jp.source_id = s.id),
            s.last_fetched_at
        )
        WHERE s.kind = 'job_board' AND s.last_fetched_at IS NOT NULL
        """
    )
    for column in ("last_complete_fetch_at", "last_attempt_at"):
        op.add_column("sources", sa.Column(column, sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE sources SET last_complete_fetch_at = last_fetched_at "
        "WHERE kind = 'job_board' AND last_fetched_at IS NOT NULL"
    )
    op.execute(
        "UPDATE sources SET last_attempt_at = last_fetched_at WHERE last_fetched_at IS NOT NULL"
    )
    for table, prefix in _RETRIES:
        op.add_column(
            table,
            sa.Column(
                f"{prefix}_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
            ),
        )
        op.add_column(
            table, sa.Column(f"{prefix}_failed_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.add_column(table, sa.Column(f"{prefix}_error", sa.Text(), nullable=True))
    op.create_index("ix_documents_created_at", "documents", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_documents_created_at", table_name="documents")
    for table, prefix in reversed(_RETRIES):
        for column in ("error", "failed_at", "failures"):
            op.drop_column(table, f"{prefix}_{column}")
    for column in ("last_attempt_at", "last_complete_fetch_at"):
        op.drop_column("sources", column)
    op.drop_column("sources", "first_complete_fetch_at")
