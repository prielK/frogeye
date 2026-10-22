"""raw_items.normalized_at

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('raw_items', sa.Column('normalized_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        'ix_raw_items_unnormalized',
        'raw_items',
        ['id'],
        postgresql_where=sa.text('normalized_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_raw_items_unnormalized', table_name='raw_items')
    op.drop_column('raw_items', 'normalized_at')
