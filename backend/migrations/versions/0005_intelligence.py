"""intelligence schema: signals, briefings, agent ops, LLM ledger, retrieval index, JFrog KB

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("now()")
COST = sa.Numeric(10, 6)
EMPTY_JSON = sa.text("'{}'::jsonb")
EMPTY_INTS = sa.text("'{}'::integer[]")

SIGNAL_TYPES = (
    "product_launch feature_update deprecation pricing_change messaging_change partnership "
    "funding acquisition earnings_financials leadership_change hiring_trend customer_win_loss "
    "security_incident analyst_recognition community_buzz event_conference legal_regulatory "
    "industry_trend standard_spec other"
).split()
STANCES = ["threat", "opportunity", "neutral"]
SCOPES = ["competitor", "industry", "jfrog"]
SIGNAL_STATUSES = ["pending", "auto", "needs_review", "approved", "rejected", "suppressed"]
EDITIONS = ["master", "sales", "pm", "exec", "pmm_devrel"]
BRIEFING_STATUSES = ["published", "quiet", "skipped_budget", "failed"]
RUN_STATUSES = ["running", "succeeded", "failed", "interrupted"]
STEP_KINDS = ["llm", "tool", "node"]
LLM_STATUSES = ["ok", "cached", "error", "refused"]
RETRIEVAL_KINDS = ["article_chunk", "signal", "jfrog_kb"]


def _in(column: str, values: list[str], nullable: bool = False) -> str:
    listed = ", ".join(f"'{v}'" for v in values)
    expr = f"{column} IN ({listed})"
    return f"{column} IS NULL OR {expr}" if nullable else expr


def _zero_int(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), server_default=sa.text("0"), nullable=False)


def upgrade() -> None:
    # agent_runs first: signals, briefings, llm_calls and agent_steps reference it.
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("graph", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), nullable=True),
        sa.Column("started_at", TS, server_default=NOW, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        _zero_int("tokens_in"),
        _zero_int("tokens_out"),
        sa.Column("cost_usd", COST, server_default=sa.text("0"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(_in("status", RUN_STATUSES), name="ck_agent_runs_status"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("node", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("args", pg.JSONB(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        _zero_int("tokens_in"),
        _zero_int("cached_tokens"),
        _zero_int("tokens_out"),
        sa.Column("cost_usd", COST, server_default=sa.text("0"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.CheckConstraint(_in("kind", STEP_KINDS), name="ck_agent_steps_kind"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_steps_run_step", "agent_steps", ["agent_run_id", "step_no"])
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        _zero_int("input_tokens"),
        _zero_int("cached_tokens"),
        _zero_int("output_tokens"),
        sa.Column("cost_usd", COST, nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.CheckConstraint(_in("status", LLM_STATUSES), name="ck_llm_calls_status"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])
    op.create_table(
        "llm_cache",
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("response", pg.JSONB(), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("request_hash"),
    )

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("signal_type", sa.Text(), nullable=True),
        sa.Column("stance", sa.Text(), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("first_seen_at", TS, server_default=NOW, nullable=False),
        sa.Column("last_updated_at", TS, server_default=NOW, nullable=False),
        sa.Column("fact", sa.Text(), nullable=True),
        sa.Column("impact", sa.Text(), nullable=True),
        sa.Column("role_actions", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("impact_score", sa.SmallInteger(), nullable=True),
        sa.Column("novelty_score", sa.SmallInteger(), nullable=True),
        sa.Column("importance", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("score_breakdown", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("reliability", sa.Text(), nullable=True),
        sa.Column("credibility", sa.SmallInteger(), nullable=True),
        sa.Column("corroboration_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("hedged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("centroid", Vector(512), nullable=True),
        sa.Column("analyzed_at", TS, nullable=True),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("extraction_version", sa.Text(), nullable=True),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.CheckConstraint(_in("scope", SCOPES), name="ck_signals_scope"),
        sa.CheckConstraint(_in("signal_type", SIGNAL_TYPES, True), name="ck_signals_signal_type"),
        sa.CheckConstraint(_in("stance", STANCES, True), name="ck_signals_stance"),
        sa.CheckConstraint(_in("status", SIGNAL_STATUSES), name="ck_signals_status"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_signals_importance_first_seen",
        "signals",
        [sa.text("importance DESC"), sa.text("first_seen_at DESC")],
    )
    op.create_index("ix_signals_status", "signals", ["status"])
    op.create_index("ix_signals_first_seen_at", "signals", ["first_seen_at"])

    for table, other, other_ref in [
        ("signal_documents", "document_id", "documents.id"),
        ("signal_competitors", "competitor_id", "competitors.id"),
        ("signal_capabilities", "capability_id", "capabilities.id"),
        ("signal_kits", "kit_id", "kits.id"),
    ]:
        cols = [
            sa.Column("signal_id", sa.Integer(), nullable=False),
            sa.Column(other, sa.Integer(), nullable=False),
        ]
        if table == "signal_documents":
            cols.append(
                sa.Column(
                    "is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False
                )
            )
        op.create_table(
            table,
            *cols,
            sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint([other], [other_ref]),
            sa.PrimaryKeyConstraint("signal_id", other),
        )

    op.create_table(
        "evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "briefings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("edition", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content", pg.JSONB(), nullable=False),
        sa.Column("signal_ids", pg.ARRAY(sa.Integer()), server_default=EMPTY_INTS, nullable=False),
        sa.Column("funnel", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("cost_usd", COST, server_default=sa.text("0"), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.CheckConstraint(_in("edition", EDITIONS), name="ck_briefings_edition"),
        sa.CheckConstraint(_in("status", BRIEFING_STATUSES), name="ck_briefings_status"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", "edition", "version", name="uq_briefings_date_edition_version"),
    )
    op.create_index("ix_briefings_date_edition", "briefings", [sa.text("date DESC"), "edition"])

    op.add_column("documents", sa.Column("embedding", Vector(512), nullable=True))
    op.create_foreign_key(
        "fk_documents_story_id_signals",
        "documents",
        "signals",
        ["story_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "retrieval_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=True),
        sa.Column(
            "tsv_en",
            pg.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "tsv_simple",
            pg.TSVECTOR(),
            sa.Computed("to_tsvector('simple', text)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "competitor_ids", pg.ARRAY(sa.Integer()), server_default=EMPTY_INTS, nullable=False
        ),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("reliability", sa.Text(), nullable=True),
        sa.Column("approved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.CheckConstraint(_in("kind", RETRIEVAL_KINDS), name="ck_retrieval_items_kind"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "ref_id", "chunk_no", name="uq_retrieval_items_kind_ref_chunk"),
    )
    op.create_index(
        "ix_retrieval_items_embedding",
        "retrieval_items",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_retrieval_items_tsv_en", "retrieval_items", ["tsv_en"], postgresql_using="gin"
    )
    op.create_index(
        "ix_retrieval_items_tsv_simple", "retrieval_items", ["tsv_simple"], postgresql_using="gin"
    )
    op.create_index(
        "ix_retrieval_items_competitor_ids",
        "retrieval_items",
        ["competitor_ids"],
        postgresql_using="gin",
    )
    op.create_index("ix_retrieval_items_published_at", "retrieval_items", ["published_at"])

    op.create_table(
        "jfrog_kb_docs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("fetched_at", TS, server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )


def downgrade() -> None:
    op.drop_table("jfrog_kb_docs")
    op.drop_table("retrieval_items")
    op.drop_constraint("fk_documents_story_id_signals", "documents", type_="foreignkey")
    op.drop_column("documents", "embedding")
    op.drop_table("briefings")
    op.drop_table("evidence")
    for table in ("signal_kits", "signal_capabilities", "signal_competitors", "signal_documents"):
        op.drop_table(table)
    op.drop_table("signals")
    op.drop_table("llm_cache")
    op.drop_table("llm_calls")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
