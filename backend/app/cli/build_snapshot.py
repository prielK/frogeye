"""Build the demo snapshot (`backend/snapshot/`) a brand-new install starts from.

`make snapshot` runs the whole build; this module is its two steps inside the test project:

  python -m app.cli.build_snapshot sanitise --database frogeye_snapshot
      Upgrade a THROWAWAY copy of the stack's database (restored into the test project's
      Postgres by `make snapshot`) to the migration head and sanitise it in place: article
      text cut to an excerpt, raw item bodies dropped, caches, checkpoints and the circuit
      breaker emptied, long step texts trimmed, and the capture time recorded. Refuses any
      database whose name does not start with "frogeye_snapshot".

  pg_dump ... | python -m app.cli.build_snapshot package
      Read that database's plain-SQL dump on stdin, check it and scan it for secrets,
      local paths and e-mail addresses, then write the gzip'd dump and its manifest.

The stack's own database is only ever read (one `pg_dump` by `make snapshot`).
"""

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.snapshot import (
    DUMP_NAME,
    EMPTIED_TABLES,
    FORMAT,
    MANIFEST_NAME,
    PERSONA_DOMAIN,
    SNAPSHOT_DIR,
)

DATABASE_PREFIX = "frogeye_snapshot"
USERS_YAML = Path(__file__).resolve().parents[1] / "seed" / "users.yaml"
EXCERPT_CHARS = 1200  # an article's text, a job posting's description
SUMMARY_CHARS = 300  # an article's feed summary
STEP_TEXT_CHARS = 300  # an agent step's result summary and each string in its arguments
FULL_PAGE_SNAPSHOTS = 2  # per watched page: the next diff and the facts read the latest two
RAW_PAYLOAD_KEYS = ("outlet", "outlet_url", "domain")  # read after collection (attribution)
EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"  # Python and Postgres alike

_EXCERPT_FN = """
CREATE FUNCTION pg_temp.excerpt(body text, n integer) RETURNS text
LANGUAGE sql IMMUTABLE AS $fn$
  SELECT CASE WHEN body IS NULL OR length(body) <= n THEN body
              ELSE rtrim(regexp_replace(left(body, n - 1), '\\s+\\S*$', '')) || '…' END
$fn$
"""

# (label, statement); each runs once, in one transaction, in this order.
_SANITISE: tuple[tuple[str, str], ...] = (
    (
        "raw items never normalised, dropped",
        "DELETE FROM raw_items r WHERE r.normalized_at IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.raw_item_id = r.id)",
    ),
    (
        "raw items: body dropped, payload cut to its attribution keys",
        "UPDATE raw_items SET raw_text = NULL, payload = COALESCE((SELECT jsonb_object_agg(k, v)"
        " FROM jsonb_each(payload) AS e(k, v) WHERE k = ANY(CAST(:keys AS text[]))),"
        " CAST('{}' AS jsonb))",
    ),
    # E-mail addresses in collected text (an author's contact line, a placeholder in a
    # README) serve nothing here: each becomes "[email]".
    (
        "raw items: e-mail addresses removed",
        "UPDATE raw_items SET title = regexp_replace(title, :email, '[email]', 'g')"
        " WHERE title ~ :email",
    ),
    (
        "documents: e-mail addresses removed",
        "UPDATE documents SET title = regexp_replace(title, :email, '[email]', 'g'),"
        " summary = regexp_replace(summary, :email, '[email]', 'g'),"
        " clean_text = regexp_replace(clean_text, :email, '[email]', 'g')"
        " WHERE concat_ws(' ', title, summary, clean_text) ~ :email",
    ),
    (
        "job postings: e-mail addresses removed",
        "UPDATE job_postings"
        " SET description_text = regexp_replace(description_text, :email, '[email]', 'g')"
        " WHERE description_text ~ :email",
    ),
    (
        "page snapshots: e-mail addresses removed",
        "UPDATE page_snapshots SET text = regexp_replace(text, :email, '[email]', 'g'),"
        " diff_from_prev = regexp_replace(diff_from_prev, :email, '[email]', 'g')"
        " WHERE concat_ws(' ', text, diff_from_prev) ~ :email",
    ),
    (
        "documents: text cut to an excerpt",
        "UPDATE documents SET clean_text = pg_temp.excerpt(clean_text, :excerpt),"
        " summary = pg_temp.excerpt(summary, :summary)"
        " WHERE length(clean_text) > :excerpt OR length(summary) > :summary",
    ),
    (
        # Read only to cluster a relevant document into a story. A document triaged later
        # is embedded again first (index_new), from its excerpt.
        "documents: embedding dropped (not relevant)",
        "UPDATE documents SET embedding = NULL"
        " WHERE status <> 'relevant' AND embedding IS NOT NULL",
    ),
    (
        # The excerpt is chunk 0's text; later chunks embedded text that is no longer shipped.
        "article chunks past the first, dropped",
        "DELETE FROM retrieval_items ri WHERE ri.kind = 'article_chunk' AND (ri.chunk_no > 0"
        " OR NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = ri.ref_id))",
    ),
    (
        # tsv_en and tsv_simple are generated columns: they follow the new text.
        "article chunks: text replaced with the document's excerpt",
        "UPDATE retrieval_items ri SET text = concat_ws(E'\\n\\n', d.title,"
        " COALESCE(d.clean_text, d.summary)) FROM documents d"
        " WHERE ri.kind = 'article_chunk' AND d.id = ri.ref_id",
    ),
    (
        "job postings: description cut to an excerpt",
        "UPDATE job_postings SET description_text = pg_temp.excerpt(description_text, :excerpt)"
        " WHERE length(description_text) > :excerpt",
    ),
    (
        # The change log reads stored diffs and summaries; the next diff reads the latest
        # snapshot's text and the facts extractor the one before it.
        "page snapshots older than the latest two: text cut to an excerpt",
        "UPDATE page_snapshots p SET text = pg_temp.excerpt(p.text, :excerpt)"
        " WHERE length(p.text) > :excerpt AND (SELECT count(*) FROM page_snapshots q"
        " WHERE q.source_id = p.source_id AND q.id > p.id) >= :full_pages",
    ),
    (
        "agent steps: result summary trimmed",
        "UPDATE agent_steps SET result_summary = pg_temp.excerpt(result_summary, :step)"
        " WHERE length(result_summary) > :step",
    ),
)
_PARAMS = {
    "email": EMAIL_RE,
    "keys": list(RAW_PAYLOAD_KEYS),
    "excerpt": EXCERPT_CHARS,
    "summary": SUMMARY_CHARS,
    "step": STEP_TEXT_CHARS,
    "full_pages": FULL_PAGE_SNAPSHOTS,
}


class BuildError(Exception):
    pass


def _snapshot_url(database: str) -> str:
    if not database.startswith(DATABASE_PREFIX):
        raise BuildError(
            f"refusing {database!r}: the build database must start with "
            f"{DATABASE_PREFIX!r} (a throwaway copy, never the stack's own)"
        )
    base = make_url(os.environ["TEST_DATABASE_URL"])
    return base.set(database=database).render_as_string(hide_password=False)


def _alembic_upgrade(url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _trim_strings(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1].rstrip() + "…"
    if isinstance(value, dict):
        return {k: _trim_strings(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_trim_strings(v, limit) for v in value]
    return value


async def _guard(conn: AsyncConnection) -> None:
    running = (
        (
            await conn.execute(
                text(
                    "SELECT 'pipeline run ' || id FROM pipeline_runs WHERE status = 'running'"
                    " UNION ALL SELECT 'agent run ' || id FROM agent_runs WHERE status = 'running'"
                )
            )
        )
        .scalars()
        .all()
    )
    if running:
        raise BuildError(f"captured mid-run ({', '.join(running)}); rebuild once it finishes")
    personas = {
        (u["email"], u["name"], u["role"])
        for u in yaml.safe_load(USERS_YAML.read_text(encoding="utf-8"))["users"]
    }
    users = {tuple(r) for r in await conn.execute(text("SELECT email, name, role FROM users"))}
    if users != personas:
        raise BuildError(
            "users are not exactly the demo personas:"
            f" extra {sorted(users - personas)}, missing {sorted(personas - users)}"
        )


async def sanitise(database: str) -> None:
    url = _snapshot_url(database)
    await asyncio.to_thread(_alembic_upgrade, url)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await _guard(conn)
            await conn.execute(text(_EXCERPT_FN))
            present = (
                (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                            " AND tablename = ANY(CAST(:t AS text[]))"
                        ),
                        {"t": list(EMPTIED_TABLES)},
                    )
                )
                .scalars()
                .all()
            )
            for table in sorted(present):
                n = (await conn.execute(text(f"DELETE FROM {table}"))).rowcount
                print(f"sanitise: {table}: emptied ({n} rows)")
            for label, sql in _SANITISE:
                n = (await conn.execute(text(sql), _PARAMS)).rowcount
                print(f"sanitise: {label}: {n} rows")
            steps = (
                await conn.execute(
                    text("SELECT id, args FROM agent_steps WHERE length(args::text) > :step"),
                    _PARAMS,
                )
            ).all()
            for step_id, args in steps:
                await conn.execute(
                    text("UPDATE agent_steps SET args = CAST(:args AS jsonb) WHERE id = :id"),
                    {"id": step_id, "args": json.dumps(_trim_strings(args, STEP_TEXT_CHARS))},
                )
            print(f"sanitise: agent steps: long argument strings trimmed: {len(steps)} rows")
            await conn.execute(text("DELETE FROM demo_snapshot"))
            await conn.execute(
                text("INSERT INTO demo_snapshot (id, captured_at) VALUES (1, now())")
            )
        async with engine.connect() as conn:
            tables = (
                (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                            " ORDER BY tablename"
                        )
                    )
                )
                .scalars()
                .all()
            )
            for table in tables:
                n = await conn.scalar(text(f"SELECT count(*) FROM {table}"))
                print(f"rows: {table}: {n}")
    finally:
        await engine.dispose()


# ---- package: check, scan and write the dump --------------------------------------------

_SECRETS = {
    "openai key": re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
    "github token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "slack webhook": re.compile(r"hooks\.slack\.com/(?:services|workflows)/[A-Za-z0-9/_-]+"),
    "aws key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # a key-like value (letters and digits, 12+ characters), not prose ("dirty secret: ...")
    "credential assignment": re.compile(
        r"(?i)\b(?:password|passwd|api[_-]?key|secret|token)\s*[=:]\s*['\"]?"
        r"(?=[^\s'\"\\]*\d)(?=[^\s'\"\\]*[a-z])[^\s'\"\\]{12,}"
    ),
}
_LOCAL_PATHS = re.compile(
    r"(?i)\b[a-z]:\\{1,2}(?:users|documents and settings)\\"  # C:\Users\ (or JSON-escaped)
    r"|/[a-z]/users/"  # /c/Users/ (Git Bash)
    r"|/home/[a-z0-9._-]+/"
    r"|/users/[a-z0-9._-]+/(?:desktop|documents|downloads|library|projects|src|code)"
)
_EMAIL = re.compile(EMAIL_RE)
# psql meta-commands pg_dump adds around a plain dump; the restore pipes the rest through
# psql, which must see SQL only.
_RESTRICT = re.compile(rb"^\\(?:un)?restrict [A-Za-z0-9]+\n?$")


def _scan(lines: list[bytes]) -> tuple[Counter, Counter, Counter]:
    secrets: Counter = Counter()
    paths: Counter = Counter()
    emails: Counter = Counter()
    for raw in lines:
        line = raw.decode("utf-8", errors="replace")
        for name, pattern in _SECRETS.items():
            for m in pattern.finditer(line):
                secrets[f"{name}: {m.group(0)[:12]}…"] += 1
        for m in _LOCAL_PATHS.finditer(line):
            paths[m.group(0)] += 1
        for m in _EMAIL.finditer(line):
            if not m.group(0).lower().endswith(PERSONA_DOMAIN):
                emails[m.group(0)] += 1
    return secrets, paths, emails


def _check(lines: list[bytes]) -> tuple[str, str]:
    """The dump's alembic revision and capture time, once it is a complete plain dump
    with no psql meta-command and no transaction control of its own."""
    if (
        not lines
        or not lines[0].startswith(b"--")
        or not any(line.strip() == b"-- PostgreSQL database dump complete" for line in lines[-5:])
    ):
        raise BuildError("not a complete plain-SQL pg_dump (truncated?)")
    in_copy = None
    revision = captured = None
    for line in lines:
        if in_copy is not None:
            if line == b"\\.\n":
                in_copy = None
            elif in_copy == b"public.alembic_version":
                revision = line.decode().strip()
            elif in_copy == b"public.demo_snapshot":
                captured = line.decode().split("\t")[1]
            continue
        if line.startswith(b"COPY ") and line.rstrip().endswith(b"FROM stdin;"):
            in_copy = line.split()[1]
        elif line.startswith(b"\\"):
            raise BuildError(f"psql meta-command in the dump: {line[:40]!r}")
        elif line.strip().upper() in (b"BEGIN;", b"COMMIT;", b"ROLLBACK;", b"END;"):
            raise BuildError(f"transaction control in the dump: {line.strip()!r}")
    if not revision or not captured:
        raise BuildError("the dump has no alembic_version or demo_snapshot row (not sanitised?)")
    return revision, captured


def package(source: Any, out_dir: Path) -> None:
    lines = [line for line in source if not _RESTRICT.match(line)]
    revision, captured = _check(lines)
    secrets, paths, emails = _scan(lines)
    for label, found in (("secret", secrets), ("local path", paths), ("e-mail", emails)):
        for value, n in sorted(found.items()):
            print(f"scan: {label}: {value} ({n}x)")
        if not found:
            print(f"scan: {label}: none")
    if secrets or paths:
        raise BuildError("the dump holds secrets or local paths; nothing written")
    body = b"".join(lines)
    data = gzip.compress(body, compresslevel=9, mtime=0)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / DUMP_NAME).write_bytes(data)
    manifest = {
        "format": FORMAT,
        "file": DUMP_NAME,
        "sha256": hashlib.sha256(data).hexdigest(),
        "alembic_revision": revision,
        "captured_at": datetime.fromisoformat(captured).astimezone(UTC).isoformat(),
        "sql_bytes": len(body),
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"package: {DUMP_NAME} {len(data) / 1e6:.1f} MB (SQL {len(body) / 1e6:.1f} MB),"
        f" revision {revision}, captured {manifest['captured_at']}"
    )


def main() -> None:
    p = argparse.ArgumentParser(
        prog="python -m app.cli.build_snapshot",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="step", required=True)
    s = sub.add_parser("sanitise", help="sanitise a throwaway copy of the database in place")
    s.add_argument("--database", default=DATABASE_PREFIX)
    k = sub.add_parser("package", help="check, scan and write the plain-SQL dump read on stdin")
    k.add_argument("--out-dir", type=Path, default=SNAPSHOT_DIR)
    args = p.parse_args()
    try:
        if args.step == "sanitise":
            asyncio.run(sanitise(args.database))
        else:
            package(sys.stdin.buffer, args.out_dir)
    except BuildError as exc:
        sys.exit(f"build_snapshot: {exc}")


if __name__ == "__main__":
    main()
