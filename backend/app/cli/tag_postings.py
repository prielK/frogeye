"""Backfill job-posting tags (the daily graph's `tag_postings` node) for untagged postings.

Usage: python -m app.cli.tag_postings [--max-usd X] [--limit N]

  --max-usd X  raise the daily LLM cap to X USD for this process only. It only ever raises it
               (max of X and the configured cap); the total cap still applies.
  --limit N    tag at most N postings (default: the configured `posting_tag_cap`). Run it
               again for the next batch: tagged postings are never picked.

Prints one JSON line `{"tagged", "failed", "given_up", "reserved", "budget_stopped",
"cost_usd"}` and exits 0, also after a budget stop (the tags written before it are kept).
`given_up`: the failed postings whose third failed day it was, never asked again.
`reserved`: the postings not started because the day's remaining budget is the briefing's
reserve. `cost_usd` is the LLM spend the ledger recorded during the run.

It takes the same advisory lock as a daily run, so it never runs beside one: exit 1 when a
run holds it. Its steps and calls carry no agent run (like analyze_signals). Exit 2 without
OPENAI_API_KEY, before any call. The cap, lock and exit-code plumbing is shared with the
other backfills (app/cli/_backfill.py).
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.nodes.posting_tags import tag_postings_batch
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.cli._backfill import Backfill, backfill_main, run_locked, with_ledger_cost
from app.core.config import Settings
from app.core.logging import configure_logging

NODE = "tag_postings"
AGENT = "posting_tagger"
CLI = Backfill(prog="python -m app.cli.tag_postings", idle="nothing tagged", limit_noun="postings")


async def tag(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    settings: Settings,
    limit: int | None,
) -> dict:
    """One batch and its JSON result; `cost_usd` is what the ledger booked meanwhile."""

    async def counts() -> dict:
        ctx = RunCtx(agent_run_id=None, node=NODE, agent=AGENT)
        batch = await tag_postings_batch(session_factory, llm, settings, ctx, limit=limit)
        return {
            "tagged": batch.tagged,
            "failed": batch.failed,
            "given_up": batch.given_up,
            "reserved": batch.reserved,
            "budget_stopped": batch.budget_stopped,
        }

    return await with_ledger_cost(session_factory, counts)


async def _run(settings: Settings, limit: int | None) -> dict:
    return await run_locked(settings, lambda sf, llm: tag(sf, llm, settings, limit))


def main(argv: list[str] | None = None) -> None:
    backfill_main(CLI, argv, run=lambda s, args: _run(s, args.limit), setup=configure_logging)


if __name__ == "__main__":
    main()
