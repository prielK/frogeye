"""Migration 0008: competitor intelligence schema (postings, facts, products, briefs)."""

import asyncio
import os
import subprocess
import sys

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

NEW_TABLES = {
    "job_postings",
    "company_facts",
    "competitor_products",
    "competitor_briefs",
    "product_briefs",
}
SNAPSHOT_COLUMNS = {
    "page_kind",
    "meaningful",
    "change_kind",
    "change_summary",
    "judged_at",
    "facts_extracted_at",
}
NEW_INDEXES = {
    "ix_job_postings_competitor_closed": "(competitor_id, closed_at)",
    "ix_job_postings_competitor_first_seen": "(competitor_id, first_seen_at)",
    "ix_company_facts_competitor_kind_as_of": "(competitor_id, kind, as_of)",
    "ix_page_snapshots_source_fetched": "(source_id, fetched_at)",
    # the per-competitor intel reads look signals up by competitor
    "ix_signal_competitors_competitor_id": "(competitor_id)",
}
UNIQUES = {
    "uq_job_postings_source_external": "UNIQUE (source_id, external_id)",
    "uq_competitor_products_name": "UNIQUE (competitor_id, name)",
    "uq_competitor_briefs_version": "UNIQUE (competitor_id, version)",
    "uq_product_briefs_version": "UNIQUE (product, version)",
}


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _tables(engine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("select tablename from pg_tables where schemaname='public'"))
        return {r[0] for r in rows}


async def _columns(engine, table: str) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("select column_name from information_schema.columns where table_name = :t"),
            {"t": table},
        )
        return {r[0] for r in rows}


async def test_migration_0008_tables_constraints_and_indexes(engine):
    assert NEW_TABLES <= await _tables(engine)
    assert SNAPSHOT_COLUMNS <= await _columns(engine, "page_snapshots")
    assert "facts_extracted_at" in await _columns(engine, "documents")
    assert "consecutive_empty" in await _columns(engine, "sources")  # the empty-listing streak
    async with engine.connect() as conn:
        for name, cols in NEW_INDEXES.items():
            indexdef = await conn.scalar(
                text("select indexdef from pg_indexes where indexname = :n"), {"n": name}
            )
            assert indexdef is not None and indexdef.endswith(cols), name
        for name, definition in UNIQUES.items():
            got = await conn.scalar(
                text("select pg_get_constraintdef(oid) from pg_constraint where conname = :n"),
                {"n": name},
            )
            assert got == definition, name
        tags_default = await conn.scalar(
            text(
                "select column_default from information_schema.columns "
                "where table_name = 'job_postings' and column_name = 'tags'"
            )
        )
        brief_fk = await conn.scalar(
            text(
                "select confdeltype from pg_constraint "
                "where conrelid = 'competitor_briefs'::regclass and contype = 'f'"
            )
        )
    assert tags_default.startswith("'{}'")
    assert brief_fk == "c"  # a deleted competitor takes its briefs with it


async def test_checks_reject_unknown_fact_kind_and_page_kind(engine):
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            cid = await conn.scalar(
                text(
                    "insert into competitors (slug, name, tier, relationship) "
                    "values ('mig8', 'Mig8', 2, 'competitor') returning id"
                )
            )
            fact = (
                "insert into company_facts (competitor_id, kind, label, label_norm, source_kind,"
                " source_id, quote, extractor_version) values (:c, :k, 'Series C', 'series c',"
                " :s, 1, 'raised a Series C', '1')"
            )
            await conn.execute(text(fact), {"c": cid, "k": "funding_round", "s": "document"})
            for kind, source_kind in [("gossip", "document"), ("headcount", "tweet")]:
                nested = await conn.begin_nested()
                with pytest.raises(IntegrityError):
                    await conn.execute(text(fact), {"c": cid, "k": kind, "s": source_kind})
                await nested.rollback()
            sid = await conn.scalar(
                text(
                    "insert into sources (kind, name, url, reliability) "
                    "values ('page_watch', 'p', 'https://mig8.test/pricing', 'A') returning id"
                )
            )
            nested = await conn.begin_nested()
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        "insert into page_snapshots (source_id, text_hash, text, page_kind) "
                        "values (:s, 'h', 't', 'blog')"
                    ),
                    {"s": sid},
                )
            await nested.rollback()
        finally:
            await trans.rollback()


async def test_downgrade_0008_then_upgrade(engine):
    url = os.environ["TEST_DATABASE_URL"]
    try:
        await asyncio.to_thread(command.downgrade, _cfg(url), "0007")
        assert not NEW_TABLES & await _tables(engine)
        assert not SNAPSHOT_COLUMNS & await _columns(engine, "page_snapshots")
        assert "facts_extracted_at" not in await _columns(engine, "documents")
        assert "consecutive_empty" not in await _columns(engine, "sources")
        assert {"id", "source_id", "text_hash", "text"} <= await _columns(engine, "page_snapshots")
        async with engine.connect() as conn:
            gone = await conn.scalar(
                text("select count(*) from pg_indexes where indexname = :n"),
                {"n": "ix_signal_competitors_competitor_id"},
            )
        assert gone == 0
    finally:
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
    assert NEW_TABLES <= await _tables(engine)
    assert SNAPSHOT_COLUMNS <= await _columns(engine, "page_snapshots")


async def test_alembic_check_clean_after_0008(engine):
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "check"],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No new upgrade operations detected" in proc.stdout + proc.stderr
