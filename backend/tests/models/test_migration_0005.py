import asyncio
import os
import subprocess
import sys

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

NEW_TABLES = [
    "agent_runs",
    "agent_steps",
    "briefings",
    "evidence",
    "jfrog_kb_docs",
    "llm_cache",
    "llm_calls",
    "retrieval_items",
    "signal_capabilities",
    "signal_competitors",
    "signal_documents",
    "signal_kits",
    "signals",
]


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _tables(engine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("select tablename from pg_tables where schemaname='public'"))
        return {r[0] for r in rows}


async def test_migration_0005_tables_and_vector_columns(engine):
    assert set(NEW_TABLES) <= await _tables(engine)
    async with engine.connect() as conn:
        for table, col in [
            ("retrieval_items", "embedding"),
            ("documents", "embedding"),
            ("signals", "centroid"),
        ]:
            rendered = await conn.scalar(
                text(
                    "select format_type(a.atttypid, a.atttypmod) from pg_attribute a "
                    "where a.attrelid = cast(:t as regclass) and a.attname = :c"
                ),
                {"t": table, "c": col},
            )
            assert rendered == "vector(512)", (table, col)
        fk = await conn.scalar(
            text(
                "select confdeltype from pg_constraint "
                "where conname = 'fk_documents_story_id_signals'"
            )
        )
        assert fk == "n"  # ON DELETE SET NULL


async def test_generated_tsv_columns(engine):
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into retrieval_items (kind, ref_id, text) "
                "values ('jfrog_kb', 1, 'Sonatype Nexus Repository 3.97')"
            )
        )
        simple = await conn.scalar(
            text("select tsv_simple @@ to_tsquery('simple','3.97') from retrieval_items")
        )
        english = await conn.scalar(
            text(
                "select tsv_en @@ websearch_to_tsquery('english','repositories') "
                "from retrieval_items"
            )
        )
        await conn.execute(text("delete from retrieval_items"))
    assert simple is True
    assert english is True


async def test_briefing_unique_version(engine):
    ins = text(
        "insert into briefings (date, edition, version, status, content) "
        "values ('2026-10-18', 'master', :v, 'published', '{}'::jsonb)"
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(ins, {"v": 1})
            await conn.execute(ins, {"v": 2})
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(ins, {"v": 1})
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("delete from briefings"))


async def test_downgrade_0005_then_upgrade(engine):
    url = os.environ["TEST_DATABASE_URL"]
    try:
        await asyncio.to_thread(command.downgrade, _cfg(url), "0004")
        tables = await _tables(engine)
        assert not set(NEW_TABLES) & tables
        async with engine.connect() as conn:
            has_col = await conn.scalar(
                text(
                    "select count(*) from information_schema.columns "
                    "where table_name='documents' and column_name='embedding'"
                )
            )
        assert has_col == 0
    finally:
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
    assert set(NEW_TABLES) <= await _tables(engine)


async def test_alembic_check_ignores_checkpoint_tables(engine):
    async with engine.begin() as conn:
        await conn.execute(text("create table checkpoints (thread_id text primary key)"))
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "check"],
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("drop table checkpoints"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No new upgrade operations detected" in proc.stdout + proc.stderr
