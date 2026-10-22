import argparse
import asyncio

from sqlalchemy import func, select

from app.core.db import SessionLocal, engine
from app.models import Competitor
from app.seed.loader import load_seed


async def _run(if_empty: bool) -> None:
    try:
        async with SessionLocal() as session:
            if if_empty and await session.scalar(select(func.count()).select_from(Competitor)):
                print("seed: competitors already present, skipping")
                return
            report = await load_seed(session)
            await session.commit()
            print(f"seed: loaded {report}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.seed")
    parser.add_argument("--if-empty", action="store_true", help="skip if competitors has rows")
    args = parser.parse_args()
    asyncio.run(_run(args.if_empty))


if __name__ == "__main__":
    main()
