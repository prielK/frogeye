"""The demo-snapshot builder: sanitising a throwaway copy, and checking and scanning its dump."""

import asyncio
import gzip
import hashlib
import io
import json
import os
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cli.build_snapshot import BuildError, _snapshot_url, package, sanitise
from app.models import (
    AgentRun,
    AgentStep,
    Document,
    HostBlock,
    JobPosting,
    LlmCache,
    RawItem,
    RetrievalItem,
    Source,
    User,
)
from app.seed.loader import load_seed
from app.snapshot import DUMP_NAME, MANIFEST_NAME

LONG = "Intro line. " + "word " * 600 + "Contact jane.doe@example.org for details."


def _dump(*body: str) -> bytes:
    return "\n".join(
        [
            "--",
            "-- PostgreSQL database dump",
            "--",
            "\\restrict AbC123",
            "SET statement_timeout = 0;",
            *body,
            "COPY public.alembic_version (version_num) FROM stdin;",
            "0010",
            "\\.",
            "COPY public.demo_snapshot (id, captured_at, restored_at) FROM stdin;",
            "1\t2026-10-22 01:13:10.621233+00\t\\N",
            "\\.",
            "--",
            "-- PostgreSQL database dump complete",
            "--",
            "\\unrestrict AbC123",
            "",
        ]
    ).encode()


def _package(data: bytes, out_dir) -> None:
    package(io.BytesIO(data), out_dir)


def test_package_writes_the_dump_without_psql_meta_commands_and_its_manifest(tmp_path, capsys):
    _package(_dump("CREATE TABLE public.t (id int);"), tmp_path)
    gz = (tmp_path / DUMP_NAME).read_bytes()
    sql = gzip.decompress(gz).decode()
    assert "\\restrict" not in sql and "\\unrestrict" not in sql
    assert "CREATE TABLE public.t" in sql
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert manifest["sha256"] == hashlib.sha256(gz).hexdigest()
    assert manifest["alembic_revision"] == "0010"
    assert manifest["captured_at"] == "2026-10-22T01:13:10.621233+00:00"
    assert "scan: secret: none" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("\\! rm -rf /", "psql meta-command"),
        ("BEGIN;", "transaction control"),
        ("COMMIT;", "transaction control"),
        ("INSERT INTO t VALUES ('sk-proj-abcdefghijklmnopqrstuvwxyz012345');", "secrets"),
        ("INSERT INTO t VALUES ('C:\\Users\\someone\\repo');", "local paths"),
        ("INSERT INTO t VALUES ('/c/Users/someone/repo');", "local paths"),
        ("INSERT INTO t VALUES ('https://hooks.slack.com/services/T000/B000/XXXX');", "secrets"),
    ],
)
def test_package_refuses_an_unsafe_dump(tmp_path, body, message):
    with pytest.raises(BuildError, match=message):
        _package(_dump(body), tmp_path)
    assert not (tmp_path / DUMP_NAME).exists()


def test_package_refuses_a_truncated_dump(tmp_path):
    with pytest.raises(BuildError, match="truncated"):
        _package(_dump()[:-60], tmp_path)


def test_package_reports_e_mail_addresses_but_not_the_personas(tmp_path, capsys):
    _package(_dump("-- someone@example.org analyst@frogeye.local"), tmp_path)
    out = capsys.readouterr().out
    assert "scan: e-mail: someone@example.org (1x)" in out
    assert "frogeye.local" not in out


def test_prose_about_secrets_is_not_a_secret(tmp_path, capsys):
    _package(_dump("-- the dirty secret: overemphasis on compliance"), tmp_path)
    assert "scan: secret: none" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["frogeye", "frogeye_test", "postgres"])
def test_only_a_throwaway_database_is_sanitised(name):
    with pytest.raises(BuildError, match="refusing"):
        _snapshot_url(name)


# ---- sanitise, against a throwaway copy ----------------------------------------------------


def _admin() -> psycopg.Connection:
    url = make_url(os.environ["TEST_DATABASE_URL"]).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False), autocommit=True)


@pytest.fixture
async def snapshot_db() -> AsyncIterator[tuple[str, str]]:
    name = f"frogeye_snapshot_t{uuid.uuid4().hex[:10]}"
    with _admin() as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = _snapshot_url(name)
    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        await asyncio.to_thread(command.upgrade, cfg, "head")
        yield name, url
    finally:
        with _admin() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def _fill(url: str) -> None:
    engine = create_async_engine(url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        await load_seed(s)
        competitor = (await s.execute(text("SELECT id FROM competitors LIMIT 1"))).scalar_one()
        src = Source(kind="rss", name="feed", url="https://example.com/f.xml", reliability="B")
        s.add(src)
        await s.flush()
        raw = RawItem(
            source_id=src.id,
            url="https://example.com/a",
            external_id="a",
            content_hash="h",
            payload={"outlet": "Example News", "feed_title": "x", "author": "A. Writer"},
            raw_text=LONG,
        )
        s.add(raw)
        await s.flush()
        doc = Document(
            raw_item_id=raw.id,
            source_id=src.id,
            canonical_url="https://example.com/a",
            url_hash="u",
            content_hash="c",
            title="Big news",
            clean_text=LONG,
            summary=LONG,
            status="new",
            embedding=[0.1] * 512,
        )
        s.add(doc)
        await s.flush()
        s.add_all(
            [
                RetrievalItem(
                    kind="article_chunk",
                    ref_id=doc.id,
                    chunk_no=n,
                    text=LONG,
                    embedding=[0.2] * 512,
                )
                for n in range(2)
            ]
        )
        s.add(LlmCache(request_hash="r", model="m", response={"a": 1}))
        s.add(HostBlock(host="example.com", strikes=1))
        s.add(
            JobPosting(
                source_id=src.id,
                competitor_id=competitor,
                ats="greenhouse",
                external_id="j",
                title="Engineer",
                department="engineering",
                seniority="senior",
                content_hash="jh",
                description_text=LONG,
            )
        )
        run = AgentRun(graph="daily_intel", thread_id="t", trigger="manual", status="succeeded")
        s.add(run)
        await s.flush()
        s.add(
            AgentStep(
                agent_run_id=run.id,
                step_no=1,
                node="n",
                kind="tool",
                args={"query": "q" * 900, "n": 3},
                result_summary="r" * 900,
            )
        )
        await s.commit()
    await engine.dispose()


async def test_sanitise_cuts_text_empties_caches_and_records_the_capture(snapshot_db):
    name, url = snapshot_db
    await _fill(url)
    await sanitise(name)
    engine = create_async_engine(url)
    async with engine.connect() as c:

        async def one(sql: str):
            return (await c.execute(text(sql))).one()

        clean, summary, embedding = await one(
            "SELECT clean_text, summary, embedding FROM documents"
        )
        assert len(clean) <= 1200 and clean.endswith("…") and len(summary) <= 300
        assert embedding is None  # not relevant: nothing clusters it
        raw_text, payload = await one("SELECT raw_text, payload FROM raw_items")
        assert raw_text is None and payload == {"outlet": "Example News"}
        chunks = (await c.execute(text("SELECT chunk_no, text FROM retrieval_items"))).all()
        assert [n for n, _ in chunks] == [0]
        assert chunks[0][1] == f"Big news\n\n{clean}"
        assert (
            await one("SELECT tsv_en @@ plainto_tsquery('english', 'intro') FROM retrieval_items")
        )[0]
        assert len((await one("SELECT description_text FROM job_postings"))[0]) <= 1200
        args, result = await one("SELECT args, result_summary FROM agent_steps")
        assert len(args["query"]) <= 300 and args["n"] == 3 and len(result) <= 300
        for table in ("llm_cache", "host_blocks"):
            assert (await one(f"SELECT count(*) FROM {table}"))[0] == 0, table
        assert (await one("SELECT count(*) FROM demo_snapshot WHERE captured_at IS NOT NULL"))[0]
    await engine.dispose()


async def test_sanitise_redacts_e_mail_addresses_in_collected_text(snapshot_db):
    name, url = snapshot_db
    await _fill(url)
    engine = create_async_engine(url)
    async with engine.begin() as c:
        await c.execute(text("UPDATE documents SET clean_text = 'Write to jane.doe@example.org.'"))
    await sanitise(name)
    async with engine.connect() as c:
        body = (await c.execute(text("SELECT clean_text FROM documents"))).scalar_one()
        chunk = (await c.execute(text("SELECT text FROM retrieval_items"))).scalar_one()
    await engine.dispose()
    assert body == "Write to [email]." and "jane.doe" not in chunk


async def test_sanitise_refuses_users_beyond_the_personas(snapshot_db):
    name, url = snapshot_db
    await _fill(url)
    engine = create_async_engine(url)
    async with async_sessionmaker(engine)() as s:
        s.add(User(name="Someone", email="someone@example.org", role="sales", password_hash="x"))
        await s.commit()
    await engine.dispose()
    with pytest.raises(BuildError, match="not exactly the demo personas"):
        await sanitise(name)


async def test_sanitise_refuses_a_capture_taken_mid_run(snapshot_db):
    name, url = snapshot_db
    await _fill(url)
    engine = create_async_engine(url)
    async with engine.begin() as c:
        await c.execute(text("UPDATE agent_runs SET status = 'running'"))
    await engine.dispose()
    with pytest.raises(BuildError, match="mid-run"):
        await sanitise(name)
