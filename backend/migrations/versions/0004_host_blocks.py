"""host_blocks: persistent per-host circuit breaker

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_blocks",
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("strikes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("host"),
    )


def downgrade() -> None:
    op.drop_table("host_blocks")
