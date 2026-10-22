"""Backfill stage-B analyses (the daily graph's `deep_analyze` node) for existing signals.

Usage: python -m app.cli.analyze_signals [--max-usd X] [--limit N]

  --max-usd X  raise the daily LLM cap to X USD for this process only. It only ever raises it
               (max of X and the configured cap); the total cap still applies.
  --limit N    analyse at most N signals (default: the configured `deep_analysis_cap`). Run
               it again for the next batch: signals with a current analysis are never picked.

Prints one JSON line `{"analyzed", "failed", "score_failed", "budget_stopped",
"reserve_stopped", "cost_usd"}` and exits 0, also after a budget stop (the analyses done
before it are kept). `reserve_stopped`: it started no further signal once the day's
remaining budget was under `NEWSROOM_RESERVE_USD`, so the day's briefing can still be
written; run it again on another day, or with a higher --max-usd. `cost_usd` is the LLM
spend the ledger recorded during the run. Like the daily node, it re-scores the signals it
analysed; `score_failed` counts the re-scores that failed and were skipped (the next
daily run scores them again; the log names each).

It takes the same advisory lock as a daily run, so it never runs beside one (two writers
would pay twice for the same signals): exit 1 when a run holds it. Its steps and calls carry
no agent run (like build_kb): the daily graph's run record belongs to the daily run. Exit 2
without OPENAI_API_KEY, before any call. The cap, lock, ledger and exit-code plumbing is
shared with the other backfills (app/cli/_backfill.py).
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.graphs.daily_intel import NODE_AGENTS, deep_analyze_batch
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.cli._backfill import Backfill, backfill_main, run_locked, with_ledger_cost
from app.core.config import Settings
from app.core.logging import configure_logging

NODE = "deep_analyze"
CLI = Backfill(
    prog="python -m app.cli.analyze_signals", idle="nothing analysed", limit_noun="signals"
)


async def analyze(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    settings: Settings,
    limit: int | None,
) -> dict:
    """One batch and its JSON result; `cost_usd` is what the ledger booked meanwhile."""

    async def counts() -> dict:
        ctx = RunCtx(agent_run_id=None, node=NODE, agent=NODE_AGENTS[NODE])
        batch = await deep_analyze_batch(session_factory, settings, llm, ctx, limit=limit)
        return {
            "analyzed": batch.analyzed,
            "failed": len(batch.failed) + len(batch.crashed),
            "score_failed": len(batch.score_errors),
            "budget_stopped": batch.budget is not None,
            "reserve_stopped": batch.reserved > 0,
        }

    return await with_ledger_cost(session_factory, counts)


async def _run(settings: Settings, limit: int | None) -> dict:
    return await run_locked(settings, lambda sf, llm: analyze(sf, llm, settings, limit))


def main(argv: list[str] | None = None) -> None:
    backfill_main(CLI, argv, run=lambda s, args: _run(s, args.limit), setup=configure_logging)


if __name__ == "__main__":
    main()
