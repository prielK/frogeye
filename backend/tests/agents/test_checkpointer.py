"""The Postgres checkpointer the daily graph runs on (in production; tests use MemorySaver)."""

import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.agents.runtime import checkpointer as cp_mod
from app.agents.runtime.checkpointer import checkpoint_dsn, open_checkpointer

CHECKPOINT_TABLES = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
)


def test_checkpoint_dsn_drops_the_sqlalchemy_driver(settings):
    url = "postgresql+psycopg://frogeye:s3cret@db:5432/frogeye"
    assert checkpoint_dsn(settings.model_copy(update={"database_url": url})) == (
        "postgresql://frogeye:s3cret@db:5432/frogeye"
    )
    plain = "postgresql://u:p@h/d"
    assert checkpoint_dsn(settings.model_copy(update={"database_url": plain})) == plain


class _State(TypedDict, total=False):
    n: int


async def test_open_checkpointer_sets_up_and_persists(settings, monkeypatch):
    """Against the real test database: setup() once per process, then a thread round-trips."""
    url = os.environ["TEST_DATABASE_URL"]
    monkeypatch.setattr(cp_mod, "_setup_done", False)
    g = StateGraph(_State)
    g.add_node("bump", lambda s: {"n": s.get("n", 0) + 1})
    g.add_edge(START, "bump")
    g.add_edge("bump", END)
    config = {"configurable": {"thread_id": "checkpointer-test"}}
    try:
        async with open_checkpointer(settings.model_copy(update={"database_url": url})) as saver:
            assert cp_mod._setup_done is True
            out = await g.compile(checkpointer=saver).ainvoke({"n": 1}, config)
            assert out == {"n": 2}
        async with open_checkpointer(settings.model_copy(update={"database_url": url})) as saver:
            state = await g.compile(checkpointer=saver).aget_state(config)
            assert state.values == {"n": 2}
    finally:
        engine = create_async_engine(url)
        async with engine.begin() as conn:  # leave the shared test schema as alembic made it
            for table in CHECKPOINT_TABLES:
                await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        await engine.dispose()
