"""List or clear the per-host circuit breaker state.

Usage: python -m app.cli.host_blocks [--clear HOST | --clear-all]
"""

import argparse
import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.models import HostBlock


async def list_blocks(session: AsyncSession) -> list[HostBlock]:
    return list(
        (
            await session.scalars(
                select(HostBlock).where(HostBlock.strikes > 0).order_by(HostBlock.host)
            )
        ).all()
    )


async def clear_blocks(session: AsyncSession, host: str | None) -> int:
    stmt = delete(HostBlock)
    if host is not None:
        stmt = stmt.where(HostBlock.host == host)
    result = await session.execute(stmt)
    return result.rowcount or 0


async def _run(args: argparse.Namespace) -> None:
    try:
        async with SessionLocal() as session:
            if args.clear_all or args.clear:
                n = await clear_blocks(session, None if args.clear_all else args.clear)
                await session.commit()
                print(f"host_blocks: cleared {n} host(s)")
                return
            rows = await list_blocks(session)
        if not rows:
            print("host_blocks: no blocks recorded")
        for r in rows:
            until = r.blocked_until.isoformat() if r.blocked_until else "-"
            print(
                f"{r.host}\tstrikes={r.strikes}\tblocked_until={until}\tlast_status={r.last_status}"
            )
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--clear", metavar="HOST")
    g.add_argument("--clear-all", action="store_true")
    asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    main()
