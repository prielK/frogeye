"""Migration 0009: a job board's first complete listing, bounded retries for
the posting tagger and the facts extractor, and the "collected since" index."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import text

RETRY_COLUMNS = {
    "job_postings": {"tag_failures", "tag_failed_at", "tag_error"},
    "documents": {"facts_failures", "facts_failed_at", "facts_error"},
    "page_snapshots": {
        "facts_failures",
        "facts_failed_at",
        "facts_error",
        # the page-change judge is bounded too
        "judge_failures",
        "judge_failed_at",
        "judge_error",
    },
}


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _columns(engine, table: str) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("select column_name from information_schema.columns where table_name = :t"),
            {"t": table},
        )
        return {r[0] for r in rows}


# every column 0009 adds, keyed by (table, column), with its server default (None: none)
NEW_COLUMN_DEFAULTS: dict[tuple[str, str], str | None] = {
    ("sources", "first_complete_fetch_at"): None,
    ("sources", "last_complete_fetch_at"): None,
    ("sources", "last_attempt_at"): None,
    **{
        (table, f"{prefix}_{column}"): ("0" if column == "failures" else None)
        for table, prefix in (
            ("job_postings", "tag"),
            ("documents", "facts"),
            ("page_snapshots", "facts"),
            ("page_snapshots", "judge"),  # the page-change judge is bounded too
        )
        for column in ("failures", "failed_at", "error")
    },
}


async def test_migration_0009_columns_and_index(engine):
    assert "first_complete_fetch_at" in await _columns(engine, "sources")
    for table, columns in RETRY_COLUMNS.items():
        assert columns <= await _columns(engine, table), table
    async with engine.connect() as conn:
        indexdef = await conn.scalar(
            text("select indexdef from pg_indexes where indexname = 'ix_documents_created_at'")
        )
        found = {
            (table, column): (
                await conn.execute(
                    text(
                        "select column_default from information_schema.columns "
                        "where table_name = :t and column_name = :c"
                    ),
                    {"t": table, "c": column},
                )
            ).all()
            for table, column in NEW_COLUMN_DEFAULTS
        }
    assert indexdef is not None and indexdef.endswith("(created_at)")
    assert all(len(rows) == 1 for rows in found.values()), found  # each column exists, once
    defaults = {key: rows[0][0] for key, rows in found.items()}
    assert defaults == NEW_COLUMN_DEFAULTS


async def test_upgrade_backfills_a_fetched_board_as_completely_listed(engine):
    """The rule before 0009 read any successful fetch as a complete one: an existing board
    keeps its first snapshot day (its earliest posting, else its last fetch), so the upgrade
    changes no page; a board never fetched stays unlisted."""
    url = os.environ["TEST_DATABASE_URL"]
    first = datetime(2026, 10, 1, 4, tzinfo=UTC)
    fetched = datetime(2026, 10, 19, 4, tzinfo=UTC)
    await asyncio.to_thread(command.downgrade, _cfg(url), "0008")
    try:
        async with engine.begin() as conn:
            cid = await conn.scalar(
                text(
                    "insert into competitors (slug, name, tier, relationship) "
                    "values ('mig9', 'Mig9', 2, 'competitor') returning id"
                )
            )

            async def board(name: str, at: datetime | None) -> int:
                return await conn.scalar(
                    text(
                        "insert into sources (kind, name, url, reliability, competitor_id, "
                        "last_fetched_at) values ('job_board', :n, :u, 'A', :c, :at) returning id"
                    ),
                    {"n": name, "u": f"https://mig9.test/{name}", "c": cid, "at": at},
                )

            with_postings = await board("listed", fetched)
            empty = await board("empty", fetched)
            never = await board("never", None)
            await conn.execute(
                text(
                    "insert into job_postings (source_id, competitor_id, ats, external_id, "
                    "title, department, seniority, content_hash, first_seen_at, last_seen_at) "
                    "values (:s, :c, 'lever', 'p1', 'Engineer', 'Engineering', 'Mid', 'h', :f, :f)"
                ),
                {"s": with_postings, "c": cid, "f": first},
            )
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
        async with engine.connect() as conn:
            rows = dict(
                (
                    await conn.execute(
                        text(
                            "select id, first_complete_fetch_at from sources "
                            "where competitor_id = :c"
                        ),
                        {"c": cid},
                    )
                ).all()
            )
        assert rows == {with_postings: first, empty: fetched, never: None}
    finally:
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "delete from job_postings where competitor_id in "
                    "(select id from competitors where slug = 'mig9')"
                )
            )
            await conn.execute(
                text(
                    "delete from sources where competitor_id in "
                    "(select id from competitors where slug = 'mig9')"
                )
            )
            await conn.execute(text("delete from competitors where slug = 'mig9'"))


async def test_downgrade_0009_then_upgrade(engine):
    url = os.environ["TEST_DATABASE_URL"]
    try:
        await asyncio.to_thread(command.downgrade, _cfg(url), "0008")
        sources = await _columns(engine, "sources")
        assert (
            not {"first_complete_fetch_at", "last_complete_fetch_at", "last_attempt_at"} & sources
        )
        for table, columns in RETRY_COLUMNS.items():
            assert not columns & await _columns(engine, table), table
    finally:
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
    assert "first_complete_fetch_at" in await _columns(engine, "sources")


async def test_alembic_check_clean_after_0009(engine):
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "check"],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No new upgrade operations detected" in proc.stdout + proc.stderr
