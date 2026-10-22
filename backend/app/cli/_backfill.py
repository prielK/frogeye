"""Shared plumbing of the LLM backfill CLIs (analyze_signals, tag_postings, extract_facts,
generate_briefs): the one copy of the policy every backfill keeps.

- `--max-usd X` only ever raises the daily LLM cap, for this process only (the
  max of X and the configured cap; the total cap still applies), and says so on stderr.
- `--limit N`, for a backfill that takes one, must be a positive count.
- Exit 2 without OPENAI_API_KEY, before any call. Exit 1 when a run holds the daily run's
  advisory lock: a backfill never runs beside one. Otherwise exit 0, also after a budget
  stop, with one JSON line on stdout: the backfill's counts plus `cost_usd`, the LLM spend
  the ledger booked while it ran.
- Its steps and calls carry no agent run (RunCtx(agent_run_id=None, ...)).

A backfill module declares its `Backfill`, wraps its batch in `with_ledger_cost`, hands
that to `run_locked` in its `_run`, and calls `backfill_main` from `main`:

    CLI = Backfill(prog="python -m app.cli.tag_postings", idle="nothing tagged",
                   limit_noun="postings")

    async def tag(session_factory, llm, settings, limit) -> dict:
        return await with_ledger_cost(session_factory, lambda: counts_of_one_batch(...))

    async def _run(settings, limit) -> dict:
        return await run_locked(settings, lambda sf, llm: tag(sf, llm, settings, limit))

    def main(argv=None):
        backfill_main(CLI, argv, run=lambda s, args: _run(s, args.limit),
                      setup=configure_logging)

`main` names `_run` and `configure_logging` at call time, so tests can replace them.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.openai_backend import OpenAIBackend
from app.agents.verified_llm import ledger_cost_since, ledger_floor
from app.cli.run_daily import _positive, _settings
from app.core.config import Settings
from app.core.db import SessionLocal, engine
from app.processing.ingest import RunAlreadyActive, acquire_run_lock, release_run_lock

Counts = dict[str, Any]


@dataclass(frozen=True)
class Backfill:
    """What a backfill CLI is called and says."""

    prog: str  # "python -m app.cli.tag_postings"
    idle: str  # what did not happen without a key: "nothing tagged"
    limit_noun: str | None = None  # what --limit counts ("postings"); None: no --limit
    add_arguments: Callable[[argparse.ArgumentParser], Any] | None = None  # extra options


def positive_days(raw: str) -> int:
    """An option's whole number of days, at least 1 (`--lookback-days`)."""
    days = int(raw)
    if days < 1:
        raise argparse.ArgumentTypeError("must be a positive number of days")
    return days


def parse_args(cli: Backfill, argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=cli.prog)
    parser.add_argument("--max-usd", type=_positive, default=None, metavar="X")
    if cli.limit_noun is not None:
        parser.add_argument("--limit", type=int, default=None, metavar="N")
    if cli.add_arguments is not None:
        cli.add_arguments(parser)
    args = parser.parse_args(argv)
    if cli.limit_noun is not None and args.limit is not None and args.limit < 1:
        parser.error(f"--limit must be a positive number of {cli.limit_noun}")
    return args


def cap_note(settings: Settings, max_usd: float) -> str:
    """The line: the daily cap this process runs under, and that the total still holds."""
    kept = settings.llm_daily_budget_usd > max_usd
    return (
        f"daily LLM cap for this run: ${settings.llm_daily_budget_usd:.2f}"
        + (" (--max-usd keeps the higher configured cap)" if kept else "")
        + f"; the total cap ${settings.llm_total_budget_usd:.2f} still applies"
    )


async def with_ledger_cost(
    session_factory: async_sessionmaker[AsyncSession], work: Callable[[], Awaitable[Counts]]
) -> Counts:
    """`work()`'s counts plus `cost_usd`: what the ledger booked while it ran (rows written
    after the highest id seen before it started)."""
    floor = await ledger_floor(session_factory)
    counts = await work()
    return {**counts, "cost_usd": round(await ledger_cost_since(session_factory, floor), 6)}


async def run_locked(
    settings: Settings,
    work: Callable[[async_sessionmaker[AsyncSession], AccountedLLM], Awaitable[Counts]],
) -> Counts:
    """`work` on the app's sessions and a real LLM, under the daily run's advisory lock
    (RunAlreadyActive when a run holds it); the engine is disposed either way."""
    llm = AccountedLLM(OpenAIBackend(settings), SessionLocal, settings)
    try:
        conn, owned = await acquire_run_lock(SessionLocal)
        try:
            return await work(SessionLocal, llm)
        finally:
            await release_run_lock(conn, owned)
    finally:
        await engine.dispose()


def backfill_main(
    cli: Backfill,
    argv: list[str] | None,
    *,
    run: Callable[[Settings, argparse.Namespace], Coroutine[Any, Any, Counts]],
    setup: Callable[[], Any],
) -> None:
    """Parse, check the key, say the cap, run and print the one JSON line (see above)."""
    args = parse_args(cli, argv)
    setup()
    settings = _settings(args.max_usd)
    if not settings.openai_api_key:
        print(f"OPENAI_API_KEY is not set: {cli.idle}.", file=sys.stderr)
        sys.exit(2)
    if args.max_usd is not None:
        print(cap_note(settings, args.max_usd), file=sys.stderr)
    try:
        print(json.dumps(asyncio.run(run(settings, args))))
    except RunAlreadyActive as exc:
        print(f"nothing run: {exc}", file=sys.stderr)
        sys.exit(1)
