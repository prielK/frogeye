"""Write the weekly competitor and product briefs now (the `intel_briefs` task, on demand).

Usage: python -m app.cli.generate_briefs [--max-usd X] [--competitor SLUG]... [--product NAME]...

  --max-usd X         raise the daily LLM cap to X USD for this process only. It only ever
                      raises it (max of X and the configured cap); the total cap still
                      applies.
  --competitor SLUG   brief this competitor (repeatable).
  --product NAME      brief this JFrog product (repeatable): its name in any case, or its
                      kebab-case slug ("advanced-security").

With neither filter it writes every brief: each competitor but JFrog itself, and each JFrog
product. With a filter it writes only what is named. A brief whose evidence is unchanged
since its latest version costs nothing; one with too little evidence is skipped.

Prints one JSON line `{"generated", "unchanged", "skipped", "failed", "reserved", "removed",
"budget_stopped", "failures", "cost_usd"}` and exits 0, also after a budget stop (the briefs
written before it are kept). `cost_usd` is the LLM spend the ledger recorded during the run;
`reserved` counts the briefs not started because the day's remaining budget is the
briefing's reserve; `failures` says why each failed brief stored nothing.

It takes the same advisory lock as a daily run, so it never runs beside one: exit 1 when a
run holds it. Exit 2 without OPENAI_API_KEY, before any call, and on an unknown competitor
slug or product name. Its steps and calls carry no agent run. The cap, lock and exit-code
plumbing is shared with the other backfills (app/cli/_backfill.py).
"""

import argparse
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.cli._backfill import Backfill, backfill_main, run_locked, with_ledger_cost
from app.core.config import Settings
from app.core.logging import configure_logging
from app.intel.briefs import UnknownBriefTarget, generate_briefs

NODE = "intel_briefs"
AGENT = "brief_writer"
_PRODUCTS = {
    key: name for name in JFROG_PRODUCTS for key in (name.lower(), "-".join(name.lower().split()))
}


def _product(value: str) -> str:
    name = _PRODUCTS.get(value.strip().lower())
    if name is None:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a JFrog product (one of: {', '.join(JFROG_PRODUCTS)})"
        )
    return name


def _arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--competitor", action="append", default=None, metavar="SLUG")
    parser.add_argument("--product", action="append", default=None, metavar="NAME", type=_product)


CLI = Backfill(
    prog="python -m app.cli.generate_briefs", idle="no brief written", add_arguments=_arguments
)


async def generate(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    settings: Settings,
    competitors: list[str] | None,
    products: list[str] | None,
) -> dict:
    """One batch and its JSON result; `cost_usd` is what the ledger booked meanwhile."""

    async def counts() -> dict:
        ctx = RunCtx(agent_run_id=None, node=NODE, agent=AGENT)
        run = await generate_briefs(
            session_factory, llm, settings, ctx, competitors=competitors, products=products
        )
        return run.as_dict()

    return await with_ledger_cost(session_factory, counts)


async def _run(
    settings: Settings, competitors: list[str] | None, products: list[str] | None
) -> dict:
    return await run_locked(
        settings, lambda sf, llm: generate(sf, llm, settings, competitors, products)
    )


def _targets(args: argparse.Namespace) -> tuple[list[str] | None, list[str] | None]:
    """No filter: everything (None, None). Any filter: only what is named."""
    if args.competitor is None and args.product is None:
        return None, None
    return list(args.competitor or []), list(args.product or [])


def main(argv: list[str] | None = None) -> None:
    try:
        backfill_main(
            CLI, argv, run=lambda s, args: _run(s, *_targets(args)), setup=configure_logging
        )
    except UnknownBriefTarget as exc:
        print(f"nothing run: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
