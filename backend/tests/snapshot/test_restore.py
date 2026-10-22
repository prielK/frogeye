"""The demo-snapshot restore (app.snapshot.restore), against throwaway databases.

Each test gets its own brand-new database on the test server, so the shared test database
is never touched. The restore pipes SQL through psql, so these run where psql is installed
(the test image has it).
"""

import asyncio
import gzip
import hashlib
import json
import os
import shutil
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.snapshot import DUMP_NAME, MANIFEST_NAME, SNAPSHOT_DIR
from app.snapshot.restore import APP_NAME, Outcome, is_empty, read_manifest, restore

pytestmark = pytest.mark.skipif(shutil.which("psql") is None, reason="needs psql")

PERSONAS = {
    "analyst@frogeye.local",
    "sales@frogeye.local",
    "pm@frogeye.local",
    "exec@frogeye.local",
    "devrel@frogeye.local",
}

# A minimal snapshot at revision 0010: enough for the restore's own epilogue.
_TINY = """
CREATE TABLE public.alembic_version (version_num character varying(32) PRIMARY KEY);
INSERT INTO public.alembic_version VALUES ('0010');
CREATE TABLE public.demo_snapshot (id smallint PRIMARY KEY, captured_at timestamptz NOT NULL,
  restored_at timestamptz);
INSERT INTO public.demo_snapshot (id, captured_at) VALUES (1, '2026-10-22T01:00:00Z');
"""


def _admin() -> psycopg.Connection:
    url = make_url(os.environ["TEST_DATABASE_URL"]).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False), autocommit=True)


@pytest.fixture
def fresh_db() -> Iterator[str]:
    """A brand-new, empty database; its SQLAlchemy URL."""
    name = f"frogeye_restore_{uuid.uuid4().hex[:12]}"
    with _admin() as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield (
            make_url(os.environ["TEST_DATABASE_URL"])
            .set(database=name)
            .render_as_string(hide_password=False)
        )
    finally:
        with _admin() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _query(url: str, sql: str) -> list[tuple]:
    conn_url = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conn_url) as conn:
        return conn.execute(sql).fetchall()


def _execute(url: str, sql: str) -> None:
    conn_url = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conn_url) as conn:
        conn.execute(sql)


def _tables(url: str) -> set[str]:
    return {
        r[0] for r in _query(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    }


def _write_snapshot(directory: Path, sql: str, *, revision: str = "0010") -> Path:
    data = gzip.compress(sql.encode(), mtime=0)
    (directory / DUMP_NAME).write_bytes(data)
    manifest = {
        "format": 1,
        "file": DUMP_NAME,
        "sha256": hashlib.sha256(data).hexdigest(),
        "alembic_revision": revision,
        "captured_at": "2026-10-22T01:00:00+00:00",
    }
    (directory / MANIFEST_NAME).write_text(json.dumps(manifest))
    return directory


def _upgrade(url: str, revision: str = "head") -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, revision)


def test_the_shipped_snapshot_restores_into_an_empty_database_once(fresh_db, capsys):
    """Empty -> restored, at a revision `upgrade head` continues from; a second start skips."""
    manifest = read_manifest(SNAPSHOT_DIR)
    assert restore(fresh_db) is Outcome.restored
    assert "demo snapshot restored: data captured" in capsys.readouterr().out

    assert _query(fresh_db, "SELECT version_num FROM alembic_version") == [
        (manifest.alembic_revision,)
    ]
    [(captured_at, restored_at)] = _query(
        fresh_db, "SELECT captured_at, restored_at FROM demo_snapshot"
    )
    assert captured_at == manifest.captured_at and restored_at is not None
    assert {r[0] for r in _query(fresh_db, "SELECT email FROM users")} == PERSONAS
    for table in ("llm_cache", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        assert _query(fresh_db, f"SELECT count(*) FROM {table}") == [(0,)], table
    assert _query(fresh_db, "SELECT count(*) > 0 FROM briefings") == [(True,)]
    # the article text is an excerpt, and keyword search still finds it
    assert _query(fresh_db, "SELECT max(length(clean_text)) <= 1200 FROM documents") == [(True,)]
    assert _query(
        fresh_db,
        "SELECT count(*) > 0 FROM retrieval_items"
        " WHERE kind = 'article_chunk' AND tsv_en @@ plainto_tsquery('english', 'gitlab')",
    ) == [(True,)]

    _upgrade(fresh_db)  # forward compatible: the normal migrations run on top
    before = _query(fresh_db, "SELECT count(*) FROM documents")
    assert restore(fresh_db) is Outcome.not_empty
    assert "demo snapshot skipped: database not empty" in capsys.readouterr().out
    assert _query(fresh_db, "SELECT count(*) FROM documents") == before


def test_off_leaves_an_empty_database_alone(fresh_db, capsys):
    assert restore(fresh_db, mode="off") is Outcome.off
    assert is_empty(fresh_db)
    assert "demo snapshot: off" in capsys.readouterr().out


def test_an_unknown_mode_is_off(fresh_db, tmp_path):
    _write_snapshot(tmp_path, _TINY)
    assert restore(fresh_db, mode="yes", directory=tmp_path) is Outcome.off
    assert is_empty(fresh_db)


@pytest.mark.parametrize("revision", ["0001", "0009", "head"])
def test_a_migrated_database_is_never_touched(fresh_db, tmp_path, revision, capsys):
    """Partially or fully migrated (alembic_version present): skipped, rows unchanged."""
    _upgrade(fresh_db, revision)
    tables = _tables(fresh_db)
    _write_snapshot(tmp_path, _TINY)
    assert restore(fresh_db, directory=tmp_path) is Outcome.not_empty
    assert "demo snapshot skipped: database not empty" in capsys.readouterr().out
    assert _tables(fresh_db) == tables
    if "demo_snapshot" in tables:
        assert _query(fresh_db, "SELECT count(*) FROM demo_snapshot") == [(0,)]


def test_any_relation_makes_a_database_not_empty(fresh_db, tmp_path):
    """Seed-only data, a stray table in another schema, a lone sequence: all left alone."""
    _execute(fresh_db, "CREATE SCHEMA other; CREATE SEQUENCE other.s")
    _write_snapshot(tmp_path, _TINY)
    assert restore(fresh_db, directory=tmp_path) is Outcome.not_empty
    assert _tables(fresh_db) == set()


def test_a_seeded_database_is_never_touched(fresh_db, tmp_path):
    _upgrade(fresh_db)
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.seed.loader import load_seed

    async def seed() -> None:
        engine = create_async_engine(fresh_db)
        async with async_sessionmaker(engine)() as session:
            await load_seed(session)
            await session.commit()
        await engine.dispose()

    asyncio.run(seed())
    competitors = _query(fresh_db, "SELECT count(*) FROM competitors")
    _write_snapshot(tmp_path, _TINY)
    assert restore(fresh_db, directory=tmp_path) is Outcome.not_empty
    assert _query(fresh_db, "SELECT count(*) FROM competitors") == competitors
    assert _query(fresh_db, "SELECT count(*) FROM demo_snapshot") == [(0,)]


def test_a_small_snapshot_restores_and_records_when(fresh_db, tmp_path):
    _write_snapshot(tmp_path, _TINY)
    assert restore(fresh_db, directory=tmp_path) is Outcome.restored
    assert _query(fresh_db, "SELECT version_num FROM alembic_version") == [("0010",)]
    assert _query(fresh_db, "SELECT restored_at IS NOT NULL FROM demo_snapshot") == [(True,)]


def test_a_missing_snapshot_falls_back_to_an_empty_start(fresh_db, tmp_path, capsys):
    assert restore(fresh_db, directory=tmp_path) is Outcome.failed  # no manifest
    _write_snapshot(tmp_path, _TINY)
    (tmp_path / DUMP_NAME).unlink()
    assert restore(fresh_db, directory=tmp_path) is Outcome.failed  # no dump
    out = capsys.readouterr().out
    assert "WARNING demo snapshot not restored: no snapshot file" in out
    assert "the database is empty" in out
    assert is_empty(fresh_db)


def test_a_corrupt_snapshot_falls_back_to_an_empty_start(fresh_db, tmp_path, capsys):
    _write_snapshot(tmp_path, _TINY)
    dump = tmp_path / DUMP_NAME
    data = bytearray(dump.read_bytes())
    data[len(data) // 2] ^= 0xFF
    dump.write_bytes(bytes(data))
    assert restore(fresh_db, directory=tmp_path) is Outcome.failed
    assert "corrupt or replaced" in capsys.readouterr().out
    assert is_empty(fresh_db)


def test_a_snapshot_from_unknown_code_is_refused(fresh_db, tmp_path, capsys):
    _write_snapshot(tmp_path, _TINY, revision="9999")
    assert restore(fresh_db, directory=tmp_path) is Outcome.failed
    assert "not in this code's migrations" in capsys.readouterr().out
    assert is_empty(fresh_db)


def test_a_sql_error_rolls_the_whole_restore_back(fresh_db, tmp_path, capsys):
    """Tables created before the failing statement are gone too: one transaction."""
    _write_snapshot(tmp_path, _TINY + "CREATE TABLE public.half (id int);\nSELECT 1/0;\n")
    assert restore(fresh_db, directory=tmp_path) is Outcome.failed
    assert "division by zero" in capsys.readouterr().out
    assert is_empty(fresh_db)


def test_a_stalled_restore_is_killed_and_rolled_back(fresh_db, tmp_path):
    _write_snapshot(tmp_path, _TINY + "SELECT pg_sleep(60);\n")
    assert restore(fresh_db, directory=tmp_path, timeout_s=3) is Outcome.failed
    assert is_empty(fresh_db)
    assert _query(
        fresh_db,
        f"SELECT count(*) FROM pg_stat_activity WHERE application_name = '{APP_NAME}'",
    ) == [(0,)]


def test_racing_starts_restore_exactly_once(fresh_db, tmp_path):
    """Both see an empty database; the advisory lock and the re-check inside the
    transaction let one restore and the other skip."""
    _write_snapshot(tmp_path, _TINY + "SELECT pg_sleep(1);\n")
    outcomes: list[Outcome] = []
    threads = [
        threading.Thread(target=lambda: outcomes.append(restore(fresh_db, directory=tmp_path)))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == sorted([Outcome.restored, Outcome.not_empty])
    assert _query(fresh_db, "SELECT count(*) FROM demo_snapshot") == [(1,)]
