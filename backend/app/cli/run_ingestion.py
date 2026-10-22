"""Run one ingestion synchronously and print its stats as JSON.

Usage: python -m app.cli.run_ingestion [--allow-refetch-today]

A job board or watched page is fetched at most once per local day, whatever runs it
(app.collectors.daily_guard): one already read today is skipped and counted in
`sources_skipped_today`.

  --allow-refetch-today  fetch every job board and watched page once more even if a run
                         already read it today: one extra request each per use. For an
                         operator's one-off check only (the same-day acceptance
                         snapshot); it logs a warning, and the scheduler never passes it.
"""

import argparse
import asyncio
import json

from app.core.db import SessionLocal
from app.models import PipelineRun
from app.processing.ingest import run_ingestion


async def _run(allow_refetch_today: bool) -> dict:
    run_id = await run_ingestion(
        SessionLocal, trigger="cli", allow_refetch_today=allow_refetch_today
    )
    async with SessionLocal() as session:
        run = await session.get(PipelineRun, run_id)
        return {"run_id": run.id, "status": run.status, **run.stats}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.run_ingestion",
        description="Run one ingestion now and print its stats as JSON. A job board or "
        "watched page is fetched at most once per local day: one already read today is "
        "skipped (sources_skipped_today).",
    )
    parser.add_argument(
        "--allow-refetch-today",
        action="store_true",
        help="Fetch every job board and watched page once more even if a run already read it "
        "today (one extra request each per use). An operator's one-off check only; it logs "
        "a warning and the scheduler never uses it.",
    )
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(_run(args.allow_refetch_today)), indent=2))


if __name__ == "__main__":
    main()
