"""Backfill company facts (the daily graph's `extract_facts` node) from unread sources.

Usage: python -m app.cli.extract_facts [--max-usd X] [--limit N] [--lookback-days N]

  --max-usd X         raise the daily LLM cap to X USD for this process only. It only ever
                      raises it (max of X and the configured cap); the total cap still
                      applies.
  --limit N           read at most N sources (default: the configured `fact_extract_cap`).
                      Run it again for the next batch: a source is read once.
  --lookback-days N   read documents of the last N days (default: the configured
                      `analysis_lookback_days`, 14, as the daily node), so a backfill can
                      reach the history. Page snapshots have no window.

The sources are those the daily node reads: relevant documents of the window and
about/careers page snapshots that pass the keyword screen and have not been read. Every
stored fact carries a quote verified against its source.

Prints one JSON line `{"sources", "stored", "dropped", "failed", "given_up", "reserved",
"budget_stopped", "cost_usd"}` and exits 0, also after a budget stop (the facts stored
before it are kept). `cost_usd` is the LLM spend the ledger recorded during the run;
`given_up` counts the failed sources whose third failed day it was, never read again; `reserved`
counts the sources not started because the day's remaining budget is the briefing's reserve.

It takes the same advisory lock as a daily run, so it never runs beside one: exit 1 when a
run holds it. Its steps and calls carry no agent run (like analyze_signals). Exit 2 without
OPENAI_API_KEY, before any call. The cap, lock and exit-code plumbing is shared with the
other backfills (app/cli/_backfill.py).
"""

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.cli._backfill import Backfill, backfill_main, positive_days, run_locked, with_ledger_cost
from app.core.config import Settings
from app.core.logging import configure_logging
from app.intel.facts import extract_facts_batch

NODE = "extract_facts"
AGENT = "fact_extractor"

if TYPE_CHECKING:
    from argparse import ArgumentParser


def _options(parser: "ArgumentParser") -> None:
    parser.add_argument(
        "--lookback-days",
        type=positive_days,
        default=None,
        metavar="N",
        help="read documents of the last N days (default: analysis_lookback_days, 14, as the "
        "daily node); page snapshots have no window",
    )


CLI = Backfill(
    prog="python -m app.cli.extract_facts",
    idle="nothing extracted",
    limit_noun="sources",
    add_arguments=_options,
)


async def extract(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    settings: Settings,
    limit: int | None,
    lookback_days: int | None = None,
) -> dict:
    """One batch and its JSON result; `cost_usd` is what the ledger booked meanwhile."""

    async def counts() -> dict:
        ctx = RunCtx(agent_run_id=None, node=NODE, agent=AGENT)
        batch = await extract_facts_batch(
            session_factory, llm, settings, ctx, limit=limit, lookback_days=lookback_days
        )
        return {
            "sources": batch.sources,
            "stored": batch.stored,
            "dropped": batch.dropped,
            "failed": batch.failed,
            "given_up": batch.given_up,
            "reserved": batch.reserved,
            "budget_stopped": batch.budget_stopped,
        }

    return await with_ledger_cost(session_factory, counts)


async def _run(settings: Settings, limit: int | None, lookback_days: int | None = None) -> dict:
    return await run_locked(
        settings, lambda sf, llm: extract(sf, llm, settings, limit, lookback_days)
    )


def main(argv: list[str] | None = None) -> None:
    backfill_main(
        CLI,
        argv,
        run=lambda s, args: _run(s, args.limit, args.lookback_days),
        setup=configure_logging,
    )


if __name__ == "__main__":
    main()
