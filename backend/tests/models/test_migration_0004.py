import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def test_host_blocks_migration_up_down(engine):
    import os

    url = os.environ["TEST_DATABASE_URL"]

    async def exists() -> bool:
        async with engine.connect() as conn:
            return bool(
                await conn.scalar(text("select to_regclass('public.host_blocks') is not null"))
            )

    assert await exists()
    try:
        await asyncio.to_thread(command.downgrade, _cfg(url), "0003")
        assert not await exists()
    finally:
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
    assert await exists()
