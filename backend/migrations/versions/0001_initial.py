"""initial

Revision ID: 0001
Revises: 
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('capability_categories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('competitors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('tier', sa.SmallInteger(), nullable=False),
    sa.Column('relationship', sa.Text(), nullable=False),
    sa.Column('is_self', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('website', sa.Text(), nullable=True),
    sa.Column('ticker', sa.Text(), nullable=True),
    sa.Column('cik', sa.Text(), nullable=True),
    sa.Column('github_orgs', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('aliases', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('ambiguous_aliases', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('negative_keywords', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('disambiguation_hint', sa.Text(), nullable=True),
    sa.Column('logo_path', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.CheckConstraint("relationship IN ('competitor', 'partner', 'frenemy', 'self')", name='ck_competitors_relationship'),
    sa.CheckConstraint('tier BETWEEN 0 AND 3', name='ck_competitors_tier'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('kits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('priority', sa.SmallInteger(), nullable=False),
    sa.Column('owner_roles', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('keywords', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.CheckConstraint('priority BETWEEN 1 AND 3', name='ck_kits_priority'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('pipeline_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('trigger', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name='ck_pipeline_runs_status'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.CheckConstraint("role IN ('analyst', 'sales', 'pm', 'exec', 'pmm_devrel')", name='ck_users_role'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('capabilities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('category_id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('weight', sa.Double(), server_default=sa.text('1.0'), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['capability_categories.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('metric_points',
    sa.Column('competitor_id', sa.Integer(), nullable=False),
    sa.Column('metric', sa.Text(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('value', sa.Double(), nullable=False),
    sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], ),
    sa.PrimaryKeyConstraint('competitor_id', 'metric', 'date')
    )
    op.create_table('sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('competitor_id', sa.Integer(), nullable=True),
    sa.Column('reliability', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('etag', sa.Text(), nullable=True),
    sa.Column('last_modified', sa.Text(), nullable=True),
    sa.Column('last_fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consecutive_failures', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('health', sa.Text(), server_default=sa.text("'ok'"), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.CheckConstraint("health IN ('ok', 'degraded', 'down')", name='ck_sources_health'),
    sa.CheckConstraint("kind IN ('rss', 'google_news', 'gdelt', 'hn', 'github_releases', 'github_repo_stats', 'sec_edgar', 'page_watch', 'job_board', 'oss_stats', 'tavily')", name='ck_sources_kind'),
    sa.CheckConstraint("reliability IN ('A', 'B', 'C', 'D', 'E', 'F')", name='ck_sources_reliability'),
    sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'url', name='uq_sources_kind_url')
    )
    op.create_table('page_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('text_hash', sa.Text(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('diff_from_prev', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('raw_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('external_id', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id', name='uq_raw_items_source_external')
    )
    op.create_index('ix_raw_items_source_fetched', 'raw_items', ['source_id', 'fetched_at'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('raw_item_id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('canonical_url', sa.Text(), nullable=False),
    sa.Column('url_hash', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('author', sa.Text(), nullable=True),
    sa.Column('lang', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('clean_text', sa.Text(), nullable=True),
    sa.Column('simhash', sa.BigInteger(), nullable=True),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'new'"), nullable=False),
    sa.Column('duplicate_of', sa.Integer(), nullable=True),
    sa.Column('scope_hint', sa.Text(), nullable=True),
    sa.Column('prefilter', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('triage', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('story_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("scope_hint IS NULL OR scope_hint IN ('competitor', 'industry', 'jfrog')", name='ck_documents_scope_hint'),
    sa.CheckConstraint("status IN ('new', 'irrelevant', 'relevant', 'duplicate')", name='ck_documents_status'),
    sa.ForeignKeyConstraint(['duplicate_of'], ['documents.id'], ),
    sa.ForeignKeyConstraint(['raw_item_id'], ['raw_items.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('raw_item_id'),
    sa.UniqueConstraint('url_hash')
    )
    op.create_index('ix_documents_published_id', 'documents', [sa.literal_column('published_at DESC NULLS LAST'), sa.literal_column('id DESC')], unique=False)
    op.create_index('ix_documents_status', 'documents', ['status'], unique=False)
    op.create_table('document_competitors',
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('competitor_id', sa.Integer(), nullable=False),
    sa.Column('salience', sa.Double(), server_default=sa.text('1.0'), nullable=False),
    sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.PrimaryKeyConstraint('document_id', 'competitor_id')
    )


def downgrade() -> None:
    op.drop_table('document_competitors')
    op.drop_index('ix_documents_status', table_name='documents')
    op.drop_index('ix_documents_published_id', table_name='documents')
    op.drop_table('documents')
    op.drop_index('ix_raw_items_source_fetched', table_name='raw_items')
    op.drop_table('raw_items')
    op.drop_table('page_snapshots')
    op.drop_table('sources')
    op.drop_table('metric_points')
    op.drop_table('capabilities')
    op.drop_table('users')
    op.drop_table('pipeline_runs')
    op.drop_table('kits')
    op.drop_table('competitors')
    op.drop_table('capability_categories')
    op.execute("DROP EXTENSION IF EXISTS vector")
