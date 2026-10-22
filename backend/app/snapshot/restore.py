"""Restore the demo snapshot into a brand-new, empty database.

The api container's entrypoint runs this (`python -m app.snapshot`) right before
`alembic upgrade head` and the seed. Nothing else writes to the database before then:
worker, worker-agents, scheduler and web start only once the api is healthy.

- `DEMO_SNAPSHOT=off` skips it, and the start is the plain one (migrations, then the seed).
- Only an empty database is restored into: no table, view or sequence in any non-system
  schema, so no `alembic_version` either. Any other database is left untouched.
- The restore is one transaction that takes a Postgres advisory lock and checks emptiness
  again inside it, so two starts racing restore at most once. COMMIT is sent only after
  the whole file has streamed. Any failure (a missing or corrupt file, a revision this
  code does not know, a SQL error, the timeout) rolls back and leaves the database empty;
  a warning is logged and the normal empty start continues.
- Forward compatible: the snapshot is at a revision of this code's migration history, and
  the `alembic upgrade head` that follows applies every later migration.
"""

import enum
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.snapshot import DUMP_NAME, FORMAT, MANIFEST_NAME, SNAPSHOT_DIR

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
# pg_advisory_xact_lock key: "frogeye.demo_snapshot" (any constant works; this one is unique).
LOCK_KEY = 7_202_604_220_001
APP_NAME = "frogeye-demo-restore"
NOT_EMPTY = "frogeye demo snapshot: database not empty"
TIMEOUT_S = 600.0
_CHUNK = 1 << 20

# A relation the app (or anything else) created: tables, partitioned tables, views,
# materialized views, sequences and foreign tables outside the system schemas.
USER_RELATIONS_SQL = """
SELECT 1 FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\\_toast%'
  AND n.nspname NOT LIKE 'pg\\_temp\\_%'
"""

_PREAMBLE = f"""BEGIN;
SELECT pg_catalog.pg_advisory_xact_lock({LOCK_KEY});
DO $$
BEGIN
  IF EXISTS ({USER_RELATIONS_SQL}) THEN
    RAISE EXCEPTION '{NOT_EMPTY}';
  END IF;
END $$;
"""
_EPILOGUE = "UPDATE public.demo_snapshot SET restored_at = now();\nCOMMIT;\n"


class Outcome(enum.StrEnum):
    off = "off"
    not_empty = "not_empty"
    restored = "restored"
    failed = "failed"


class SnapshotError(Exception):
    """The snapshot cannot be restored; the message says why."""


@dataclass(frozen=True)
class Manifest:
    sha256: str
    alembic_revision: str
    captured_at: datetime


def _conninfo(database_url: str) -> tuple[str, dict[str, str]]:
    """A libpq URI without the password, and the environment that carries it."""
    url = make_url(database_url).set(drivername="postgresql")
    env = {"PGPASSWORD": url.password} if url.password else {}
    return url.set(password=None).render_as_string(hide_password=False), env


def _connect(database_url: str) -> psycopg.Connection:
    uri, env = _conninfo(database_url)
    return psycopg.connect(uri, password=env.get("PGPASSWORD"), connect_timeout=10, autocommit=True)


def is_empty(database_url: str) -> bool:
    with _connect(database_url) as conn:
        return conn.execute(f"SELECT EXISTS ({USER_RELATIONS_SQL})").fetchone() == (False,)


def read_manifest(directory: Path) -> Manifest:
    path = directory / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise SnapshotError(f"unsupported snapshot format {data.get('format')!r}")
        return Manifest(
            sha256=str(data["sha256"]),
            alembic_revision=str(data["alembic_revision"]),
            captured_at=datetime.fromisoformat(data["captured_at"]),
        )
    except FileNotFoundError as exc:
        raise SnapshotError(f"no snapshot manifest at {path}") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise SnapshotError(f"unreadable snapshot manifest: {exc}") from exc


def check_file(directory: Path, manifest: Manifest) -> Path:
    """The dump, once its sha256 matches the manifest (a missing or corrupt file fails here)."""
    path = directory / DUMP_NAME
    digest = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while chunk := f.read(_CHUNK):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise SnapshotError(f"no snapshot file at {path}") from exc
    if digest.hexdigest() != manifest.sha256:
        raise SnapshotError("snapshot file does not match its manifest (corrupt or replaced)")
    return path


def check_revision(revision: str, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """The snapshot's revision must be in this code's history, so `upgrade head` can go on
    from it; a snapshot from newer (or other) code is refused."""
    script = ScriptDirectory(str(migrations_dir))
    known = {rev.revision for rev in script.walk_revisions()}
    if revision not in known:
        raise SnapshotError(f"snapshot revision {revision!r} is not in this code's migrations")


def _end_restore_sessions(database_url: str) -> None:
    """Make sure no restore transaction is left running on the server (after a kill)."""
    with _connect(database_url) as conn:
        for _ in range(60):
            alive = conn.execute(
                "SELECT pg_catalog.pg_terminate_backend(pid) FROM pg_catalog.pg_stat_activity"
                " WHERE application_name = %s AND pid <> pg_catalog.pg_backend_pid()",
                (APP_NAME,),
            ).fetchall()
            if not alive:
                return
            time.sleep(0.5)


def _stream(dump: Path, database_url: str, timeout_s: float) -> None:
    """Pipe preamble + dump + epilogue into psql; COMMIT only once the whole file is read."""
    psql = shutil.which("psql")
    if psql is None:
        raise SnapshotError("psql is not installed")
    uri, env = _conninfo(database_url)
    cmd = [psql, "--no-psqlrc", "--quiet", "--set", "ON_ERROR_STOP=1", "--dbname", uri]
    env = {**os.environ, **env, "PGAPPNAME": APP_NAME, "PGCONNECT_TIMEOUT": "10"}
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, env=env
        )
        watchdog = threading.Timer(timeout_s, proc.kill)
        watchdog.start()
        complete = False
        try:
            assert proc.stdin is not None
            proc.stdin.write(_PREAMBLE.encode())
            with gzip.open(dump, "rb") as sql:
                while chunk := sql.read(_CHUNK):
                    proc.stdin.write(chunk)
            complete = True
            proc.stdin.write(_EPILOGUE.encode())
            proc.stdin.close()
            code = proc.wait()
        except BrokenPipeError:
            code = proc.wait()  # psql stopped on an error; its message is in `errors`
        except BaseException:
            proc.kill()  # no COMMIT was sent: the server rolls the transaction back
            proc.wait()
            raise
        finally:
            watchdog.cancel()
        errors.seek(0)
        message = " ".join(errors.read().decode(errors="replace").split())[-500:]
    if code != 0 or not complete:
        if NOT_EMPTY in message:
            raise _NotEmpty
        reason = message or ("timed out" if code < 0 else f"psql exited with {code}")
        raise SnapshotError(reason)


class _NotEmpty(Exception):
    pass


def restore(
    database_url: str,
    *,
    mode: str = "auto",
    directory: Path = SNAPSHOT_DIR,
    timeout_s: float = TIMEOUT_S,
) -> Outcome:
    """Restore the snapshot if `mode` is "auto" and the database is empty. Never raises."""
    mode = mode.strip().lower()
    if mode != "auto":
        if mode != "off":
            print(f"WARNING demo snapshot: DEMO_SNAPSHOT={mode!r} is not auto or off; off")
        print("demo snapshot: off")
        return Outcome.off
    try:
        if not is_empty(database_url):
            print("demo snapshot skipped: database not empty")
            return Outcome.not_empty
        manifest = read_manifest(directory)
        check_revision(manifest.alembic_revision)
        dump = check_file(directory, manifest)
        started = time.monotonic()
        _stream(dump, database_url, timeout_s)
    except _NotEmpty:
        print("demo snapshot skipped: database not empty")
        return Outcome.not_empty
    except Exception as exc:  # any failure falls back to the empty start
        _fallback(database_url, exc)
        return Outcome.failed
    captured = manifest.captured_at.strftime("%d %b %Y %H:%M %Z").strip()
    print(f"demo snapshot restored: data captured {captured} ({time.monotonic() - started:.0f} s)")
    return Outcome.restored


def _fallback(database_url: str, exc: Exception) -> None:
    print(f"WARNING demo snapshot not restored: {exc}")
    try:
        _end_restore_sessions(database_url)
        state = "empty" if is_empty(database_url) else "NOT empty"
    except Exception as check_exc:
        state = f"unchecked ({check_exc})"
    print(f"WARNING demo snapshot: continuing the normal start; the database is {state}")
