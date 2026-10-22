import asyncio
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import text


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _exists(engine) -> bool:
    async with engine.connect() as conn:
        return bool(
            await conn.scalar(text("select to_regclass('public.signal_analyses') is not null"))
        )


async def test_signal_analyses_table_constraints_and_cascade(engine):
    assert await _exists(engine)
    async with engine.connect() as conn:
        unique = await conn.scalar(
            text(
                "select pg_get_constraintdef(oid) from pg_constraint "
                "where conname = 'uq_signal_analyses_version'"
            )
        )
        fk = await conn.scalar(
            text(
                "select confdeltype from pg_constraint "
                "where conrelid = 'signal_analyses'::regclass and contype = 'f'"
            )
        )
        default = await conn.scalar(
            text(
                "select column_default from information_schema.columns "
                "where table_name='signal_analyses' and column_name='cost_usd'"
            )
        )
    assert unique == "UNIQUE (signal_id, version)"
    assert fk == "c"  # ON DELETE CASCADE
    assert default.startswith("0")


async def test_downgrade_0007_then_upgrade(engine):
    url = os.environ["TEST_DATABASE_URL"]
    try:
        await asyncio.to_thread(command.downgrade, _cfg(url), "0006")
        assert not await _exists(engine)
    finally:
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
    assert await _exists(engine)
