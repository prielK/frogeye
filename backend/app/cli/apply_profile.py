"""Apply the low-footprint profile to an existing database.

The seed never overwrites ``enabled`` on existing rows, so this enables the bot-blocking
vendor feeds and the generated GDELT sources, and disables SEC EDGAR sources (SEC requires a
declared contact). It never enables a source tied to JFrog itself (no self news;
migration 0006 disabled those rows). Idempotent.

Usage: python -m app.cli.apply_profile
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.models import Competitor, Source

ENABLE_NAMES = ("Checkmarx Blog", "Aqua Blog", "Black Duck Blog")


async def apply_profile(session: AsyncSession) -> dict[str, list[str]]:
    self_ids = select(Competitor.id).where(Competitor.is_self)
    not_self = Source.competitor_id.is_(None) | Source.competitor_id.not_in(self_ids)
    named = select(Source).where(Source.name.in_(ENABLE_NAMES), Source.kind == "rss", not_self)
    gdelt = select(Source).where(Source.kind == "gdelt", not_self)
    edgar = select(Source).where(Source.kind == "sec_edgar")
    enabled: list[str] = []
    disabled: list[str] = []
    for stmt in (named, gdelt):
        for src in (await session.scalars(stmt)).all():
            if not src.enabled:
                src.enabled = True
                enabled.append(src.name)
    for src in (await session.scalars(edgar)).all():
        if src.enabled:
            src.enabled = False
            disabled.append(src.name)
    await session.flush()
    return {"enabled": sorted(enabled), "disabled": sorted(disabled)}


async def _run() -> None:
    try:
        async with SessionLocal() as session:
            changes = await apply_profile(session)
            await session.commit()
        if not any(changes.values()):
            print("apply_profile: nothing to change (already applied)")
        for verb in ("enabled", "disabled"):
            if changes[verb]:
                print(f"apply_profile: {verb} {len(changes[verb])} source(s)")
                for name in changes[verb]:
                    print(f"  - {name}")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
