"""Run the Daily Intelligence graph once, in this process, and print its DailyResult.

Usage: python -m app.cli.run_daily [--max-usd X] [--resume]

  --max-usd X  raise the daily LLM cap to X USD for this process only (backlog catch-up).
               It only ever raises it (max of X and the configured cap); the total cap
               still applies.
  --resume     continue the latest interrupted run (< 6 h old) on its own thread instead of
               starting a new one.
Exit codes: 0 done, 1 another run holds the lock or there is nothing to resume.
"""

import argparse
import asyncio
import dataclasses
import json
import sys

from app.agents.runner import NothingToResume, run_daily_intel
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, engine
from app.core.logging import configure_logging
from app.processing.ingest import RunAlreadyActive


def _settings(max_usd: float | None) -> Settings:
    settings = get_settings()
    if max_usd is None:
        return settings
    # Only ever raises the cap; a copy, so get_settings() stays untouched in this process.
    cap = max(settings.llm_daily_budget_usd, max_usd)
    return settings.model_copy(update={"llm_daily_budget_usd": cap})


async def _run(settings: Settings, resume: bool) -> dict:
    try:
        result = await run_daily_intel(
            SessionLocal, trigger="manual", resume=resume, settings=settings
        )
        return dataclasses.asdict(result)
    finally:
        await engine.dispose()


def _positive(value: str) -> float:
    usd = float(value)
    if usd <= 0:
        raise argparse.ArgumentTypeError("must be a positive amount of USD")
    return usd


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli.run_daily")
    parser.add_argument("--max-usd", type=_positive, default=None, metavar="X")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    configure_logging()
    settings = _settings(args.max_usd)
    if args.max_usd is not None:
        kept = settings.llm_daily_budget_usd > args.max_usd
        print(
            f"daily LLM cap for this run: ${settings.llm_daily_budget_usd:.2f}"
            + (" (--max-usd keeps the higher configured cap)" if kept else "")
            + f"; the total cap ${settings.llm_total_budget_usd:.2f} still applies",
            file=sys.stderr,
        )
    try:
        print(json.dumps(asyncio.run(_run(settings, args.resume)), indent=2))
    except (RunAlreadyActive, NothingToResume) as exc:
        print(f"nothing run: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
