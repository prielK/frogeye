"""Migration 0010: the single-row demo_snapshot table."""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _exists(engine) -> bool:
    async with engine.connect() as conn:
        return bool(
            await conn.scalar(text("select to_regclass('public.demo_snapshot') is not null"))
        )


async def test_demo_snapshot_holds_at_most_one_row(connection):
    await connection.execute(text("insert into demo_snapshot (captured_at) values (now())"))
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(
                text("insert into demo_snapshot (id, captured_at) values (2, now())")
            )


async def test_downgrade_0010_then_upgrade(engine):
    url = os.environ["TEST_DATABASE_URL"]
    try:
        await asyncio.to_thread(command.downgrade, _cfg(url), "0009")
        assert not await _exists(engine)
    finally:
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
    assert await _exists(engine)
