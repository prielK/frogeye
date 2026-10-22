"""Experiments on the stage-B fact-checker: `python -m evals.experiments [--mode
replay|record] [--max-usd X]`.

The fact_checker suite's 30 claims (16 with a planted error) go through three checker
variants, each with stage B's own items, sources, re-ask and fail-closed rule:

- a: the checker as it was (prompt v3, the verdict before its reason), fast model;
- b: the reason before the verdict in the output schema, and a prompt (v4) that is explicit
  about widened scope, derived or rounded figures, the wrong actor and facts found only in
  an uncited source, fast model;
- c: b on the smart model.

Like the suites, it records and replays (recordings/experiments.jsonl, which keeps its own
copy of every answer it compares), and writes results/experiments.md.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.agents.briefing_schema import make_fact_check_model
from app.agents.nodes.signal_analysis import CHECK_PROMPT, CHECK_TASK, _fact_check_request
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.prompts import Prompt, load_prompt
from app.agents.verified_llm import fact_check
from evals.harness import DATASETS, PROMPTS, RESULTS, Env, open_env, read_jsonl
from evals.suites import fact_checker, run_cases

NAME = "experiments"
EXPERIMENT_PROMPTS = PROMPTS / "experiments"
MAX_USD = 0.15
# Production stage B, from the main stack's ledger on 2026-10-21: 127 stored analyses;
# the fact-check (gpt-5-nano) cost $0.0815 in all and the writer (gpt-6-luna) $0.2087.
PROD_ANALYSES = 127
PROD_CHECK_USD = 0.0815
PROD_WRITER_USD = 0.2087


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    prompt: Prompt
    reason_first: bool
    smart: bool


def _variants() -> list[Variant]:
    v3 = load_prompt("analysis_fact_checker_v3", directory=EXPERIMENT_PROMPTS)
    v4 = load_prompt(CHECK_PROMPT)  # adopted: the app's prompt is v4
    return [
        Variant("a", "verdict first, prompt v3, fast model", v3, False, False),
        Variant("b", "reason first, prompt v4, fast model", v4, True, False),
        Variant("c", "reason first, prompt v4, smart model", v4, True, True),
    ]


def _checker(variant: Variant) -> Callable[..., Awaitable[dict]]:
    async def check(llm, items, inputs, brief, ctx) -> dict:
        def request(pending: list[int]):
            schema = make_fact_check_model(len(pending), reason_first=variant.reason_first)
            return schema, _fact_check_request([items[i] for i in pending], inputs, brief)[1]

        return await fact_check(
            llm,
            len(items),
            request,
            task=CHECK_TASK,
            prompt=variant.prompt,
            keys=[],
            ctx=ctx,
        )

    return check


def _smart_llm(env: Env) -> AccountedLLM:
    """The same ledger and recordings, with the fast tier served by the smart model."""
    s = env.settings
    settings = s.model_copy(
        update={"model_fast": s.model_smart, "reasoning_effort_fast": s.reasoning_effort_smart}
    )
    return AccountedLLM(env.backend, env.session_factory, settings)


async def _run(args: argparse.Namespace) -> int:
    rows = read_jsonl(DATASETS / fact_checker.SUITE.dataset)
    lines = []
    async with open_env(args.mode, args.max_usd) as env:
        before_used = set(env.backend.used)
        smart = _smart_llm(env)
        for variant in _variants():
            spent = env.backend.live_usd + env.backend.replayed_usd
            llm = smart if variant.smart else env.llm
            check = _checker(variant)
            outcomes = await run_cases(
                env,
                rows,
                lambda r, c=check, m=llm: fact_checker.check_group(env, r, c, m),
                suite=NAME,
            )
            cost = env.backend.live_usd + env.backend.replayed_usd - spent
            suite = fact_checker.score(outcomes)
            m = {x.key: x for x in suite.metrics}
            lines.append((variant, m, cost, suite.stale))
        if args.mode == "record":
            env.backend.adopt(env.backend.used - before_used, NAME)
        live = await env.llm.spent_total()

    base = lines[0][2]
    table = [
        "| Variant | Unsupported claims removed | Supported claims removed | Check cost, 5 groups "
        "| Est. check cost per analysis | Est. stage-B cost per analysis |",
        "|---|---|---|---|---|---|",
    ]
    for variant, m, cost, stale in lines:
        check = PROD_CHECK_USD / PROD_ANALYSES * (cost / base if base else 0)
        total = check + PROD_WRITER_USD / PROD_ANALYSES
        recall, removal = m["unsupported_recall"], m["false_removal_rate"]
        note = f" ({stale} not recorded)" if stale else ""
        table.append(
            f"| {variant.key}: {variant.label}{note} | {recall.value:.1%} "
            f"({recall.numerator}/{recall.denominator}) | {removal.value:.1%} "
            f"({removal.numerator}/{removal.denominator}) | ${cost:.4f} | ${check:.5f} "
            f"| ${total:.4f} |"
        )
    text = "\n".join(
        [
            "# Fact-checker experiments",
            "",
            "The fact_checker suite's 30 claims (16 planted errors, 14 true) through three "
            "checker variants (evals/experiments.py). The per-analysis estimates scale "
            "production's measured stage-B costs (127 analyses: check $0.0815, writer "
            "$0.2087) by each variant's measured check cost against variant a.",
            "",
            *table,
            "",
        ]
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "experiments.md").write_text(text, encoding="utf-8", newline="\n")
    print(text)
    print(f"live spend ${live:.4f} (ledger)")
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m evals.experiments")
    p.add_argument("--mode", choices=("replay", "record"), default="replay")
    p.add_argument("--max-usd", type=float, default=MAX_USD)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
