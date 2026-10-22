"""demo_snapshot: when the bundled demo data this database started from was captured

At most one row (id 1). A brand-new, empty database is restored from the demo snapshot
(`app.snapshot`) before the migrations run; the snapshot carries this row, so the UI can
say the data is a capture. Every other database keeps the table empty.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_snapshot",
        sa.Column("id", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_demo_snapshot_single_row"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("demo_snapshot")
