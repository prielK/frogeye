"""Run the eval suites: `python -m evals.run [--suite NAME ...] [--mode replay|record]
[--max-usd X]`.

replay (the default) answers every model call from evals/recordings, costs nothing, needs
no API key, writes results/replay.{json,md} and exits 1 unless it reproduces the committed
results/latest.json exactly (for the suites it ran). record makes the calls the recordings
lack (live, within --max-usd) and writes results/latest.{json,md}.
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from app.schemas.evals import EvalCost, EvalModels, EvalsOut, EvalSuite
from evals import report
from evals.harness import DATASETS, RESULTS, load_manifest, open_env, read_jsonl, write_manifest
from evals.recorder import CORPUS
from evals.suites import (
    Suite,
    disambiguation,
    fact_checker,
    faithfulness,
    guardrails,
    retrieval,
    triage,
)

SUITES: dict[str, Suite] = {
    s.name: s
    for s in (
        triage.SUITE,
        disambiguation.SUITE,
        retrieval.SUITE,
        guardrails.SUITE,
        fact_checker.SUITE,
        faithfulness.SUITE,
    )
}
RECORD_MAX_USD = 0.30


def _args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    p.add_argument("--suite", action="append", choices=list(SUITES), help="repeatable; default all")
    p.add_argument("--mode", choices=("replay", "record"), default="replay")
    p.add_argument(
        "--max-usd",
        type=float,
        default=None,
        help=f"record mode: live spend cap for this run (default {RECORD_MAX_USD})",
    )
    p.add_argument("--limit", type=int, default=None, help="first N cases per suite (a dry run)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    names = args.suite or list(SUITES)
    max_usd = args.max_usd if args.max_usd is not None else RECORD_MAX_USD
    results: list[EvalSuite] = []
    async with open_env(args.mode, max_usd) as env:
        for name in names:
            suite = SUITES[name]
            rows = read_jsonl(DATASETS / suite.dataset)[: args.limit]
            print(f"{name}: {len(rows)} cases ...", flush=True)
            results.append(await suite.run(env, rows))
        live_usd = await env.llm.spent_total()  # the run's own ledger (a fresh schema)
        backend, settings = env.backend, env.settings

    stale = sum(s.stale for s in results)
    if args.mode == "record":
        if backend.live_calls or load_manifest() is None:
            write_manifest(settings, live_usd=live_usd, live_calls=backend.live_calls)
        if not args.suite and args.limit is None and not stale:
            dropped = backend.prune({CORPUS, *SUITES})  # evals.experiments keeps its own
            print(f"recordings: dropped {dropped} unused entries")
    manifest = load_manifest()
    out = EvalsOut(
        run_at=datetime.now(UTC).replace(microsecond=0),
        mode=args.mode,
        recorded_at=datetime.fromisoformat(manifest["recorded_at"]) if manifest else None,
        models=EvalModels(
            fast=settings.model_fast,
            smart=settings.model_smart,
            embedding=settings.embedding_model,
            judge=settings.model_smart,
        ),
        cost=EvalCost(
            live_usd=round(live_usd, 6),
            recorded_usd=round(backend.replayed_usd, 6),
            live_calls=backend.live_calls,
            replayed_calls=backend.replayed_calls,
        ),
        stale_cases=stale,
        suites=results,
    )
    print()
    print(report.headline(out))
    print()
    print(
        f"live spend ${live_usd:.4f} (ledger; backend tally ${backend.live_usd:.4f}) over "
        f"{backend.live_calls} calls; {backend.replayed_calls} calls replayed; "
        f"{stale} cases not recorded"
    )

    latest = RESULTS / "latest.json"
    if args.mode == "record":
        if args.limit is not None:
            print("dry run (--limit): results not written")
            return 0
        if args.suite and (prior := report.load(latest)) is not None:  # keep the other suites
            kept = [s for s in prior.suites if s.name not in names]
            order = list(SUITES)
            merged = sorted([*kept, *results], key=lambda s: order.index(s.name))
            out = out.model_copy(
                update={"suites": merged, "stale_cases": sum(s.stale for s in merged)}
            )
        report.write(out, latest, RESULTS / "latest.md")
        print(f"wrote {latest}")
        return 0
    report.write(out, RESULTS / "replay.json", RESULTS / "replay.md")
    if args.limit is not None:
        return 0
    diffs = report.differences(out, report.load(latest))
    if diffs:
        print("replay does NOT reproduce results/latest.json:")
        for d in diffs:
            print(f"  - {d}")
        return 1
    print("replay reproduces results/latest.json exactly")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = _args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
