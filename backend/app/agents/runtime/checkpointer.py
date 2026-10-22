"""The LangGraph Postgres checkpointer for the daily graph (tests pass a MemorySaver instead).

LangGraph's saver speaks plain psycopg, so the SQLAlchemy driver part (`+psycopg`) is
dropped from the DSN. Its tables (checkpoints, checkpoint_blobs, checkpoint_writes,
checkpoint_migrations) live beside ours; alembic's env ignores them. `setup()` creates or
migrates them and runs once per process.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from app.core.config import Settings

_setup_done = False


def checkpoint_dsn(settings: Settings) -> str:
    """`postgresql+psycopg://…` -> `postgresql://…` (credentials kept)."""
    url = make_url(settings.database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]:
    """An AsyncPostgresSaver on its own autocommit connection, open for the `async with`."""
    global _setup_done
    async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn(settings)) as saver:
        if not _setup_done:
            await saver.setup()
            _setup_done = True
        yield saver
