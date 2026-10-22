"""Fact-checker: does stage B's checker remove unsupported claims, and only those?

Each case group is one mini analysis: real source excerpts and six claims citing them,
some supported and some with a planted error (a wrong number, a wrong company, an
overstated scope, a derived figure no source states, a claim cited to the wrong source, a
claim the source never makes). The group goes through stage B's own check: its items and
sources (`_Item`, `_doc_sources`), its prompt and schema and its re-ask (`_fact_check`),
and its fail-closed rule (`unsupported`), exactly as `analyze_signal_deep` calls them.
"""

from collections.abc import Awaitable, Callable
from datetime import date

from app.agents.nodes.analyst import _Members
from app.agents.nodes.signal_analysis import (
    _doc_sources,
    _fact_check,
    _Inputs,
    _Item,
    _profile,
)
from app.agents.runtime.accounted import AccountedLLM, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.verified_llm import IncompleteFactCheck, unsupported
from app.models.gold import Signal
from app.rag.kb import load_jfrog_context
from app.schemas.evals import EvalFailure, EvalGroup, EvalSuite
from evals import metrics
from evals.harness import Env
from evals.suites import Outcome, Row, Suite, build, run_cases

NAME = "fact_checker"
SECTION = "what_happened"


def _inputs(row: Row) -> _Inputs:
    docs = [
        {
            "id": int(s["ref"].split(":")[1]),
            "title": s["title"],
            "text": s["text"],
            "source": s["source"],
            "grade": "B2",
            "date": date.fromisoformat(s["published_at"][:10]).isoformat(),
        }
        for s in row["sources"]
    ]
    members = _Members(docs=docs, competitors=[], capabilities=[], kits=[])
    evidence = Signal(reliability="B", credibility=2, corroboration_count=1, hedged=False)
    return _Inputs(
        stage_a="",
        profile=_profile(evidence, len(docs)),
        docs=_doc_sources(members, {}),
        kb=[],
        prior=[],
    )


# (llm, items, inputs, JFrog brief, ctx) -> verdicts by item; stage B's own call by default
Checker = Callable[[AccountedLLM, list[_Item], _Inputs, str, RunCtx], Awaitable[dict]]


async def app_checker(
    llm: AccountedLLM, items: list[_Item], inputs: _Inputs, brief: str, ctx: RunCtx
) -> dict:
    return await _fact_check(llm, items, inputs, brief, keys=[], ctx=ctx)


async def check_group(
    env: Env, row: Row, checker: Checker = app_checker, llm: AccountedLLM | None = None
) -> dict:
    """One group through a checker (evals/experiments.py passes its variants)."""
    items = [
        _Item(SECTION, i, SECTION, it["text"], tuple(it["cites"]))
        for i, it in enumerate(row["items"])
    ]
    try:
        verdicts = await checker(llm or env.llm, items, _inputs(row), load_jfrog_context(), env.ctx)
    except (IncompleteFactCheck, LLMError, LLMRefusal) as exc:  # stage B stores nothing
        return {"dropped": set(range(len(items))), "reasons": {}, "note": str(exc)}
    dropped = {i for _, i in unsupported(items, verdicts)}
    reasons = {i: "; ".join(v.reason for v in vs) for i, vs in verdicts.items()}
    return {"dropped": dropped, "reasons": reasons, "note": None}


def score(outcomes: list[Outcome]) -> EvalSuite:
    claims = []  # (group outcome, item, dropped, reason)
    for o in outcomes:
        if o.stale:
            continue
        for i, item in enumerate(o.row["items"]):
            reason = o.value["reasons"].get(i) or o.value["note"] or "no verdict"
            claims.append((o, item, i in o.value["dropped"], reason))
    pairs = [(item["expected"] == "unsupported", dropped) for _, item, dropped, _ in claims]
    head = [
        *metrics.detection(
            pairs,
            caught=("unsupported_recall", "Unsupported claims removed"),
            false_alarm=("false_removal_rate", "Supported claims removed"),
        ),
        metrics.rate(
            "accuracy",
            "Accuracy",
            sum(should == did for should, did in pairs),
            len(pairs),
        ),
    ]
    attacks = sorted(
        {item["attack"] for _, item, _, _ in claims if item["expected"] == "unsupported"}
    )
    groups = []
    for attack in attacks:
        mine = [d for _, item, d, _ in claims if item["attack"] == attack]
        groups.append(
            EvalGroup(
                name=attack,
                cases=len(mine),
                metrics=[metrics.rate("caught", "Removed", sum(mine), len(mine))],
            )
        )
    kept = [d for _, item, d, _ in claims if item["expected"] == "supported"]
    groups.append(
        EvalGroup(
            name="supported",
            cases=len(kept),
            metrics=[metrics.rate("kept", "Kept", len(kept) - sum(kept), len(kept))],
        )
    )
    failures = [
        EvalFailure(
            case_id=f"{o.row['id']}/{item['id']}",
            expected="removed" if item["expected"] == "unsupported" else "kept",
            got="removed" if dropped else "kept",
            detail=f"checker: {reason} | {item['attack']}, cites {', '.join(item['cites'])}: "
            f"{item['text']!r}",
        )
        for o, item, dropped, reason in claims
        if dropped != (item["expected"] == "unsupported")
    ]
    cases = sum(len(o.row["items"]) for o in outcomes)
    stale_items = sum(len(o.row["items"]) for o in outcomes if o.stale)
    suite = build(SUITE, outcomes, metrics=head, groups=groups, failures=failures, cases=cases)
    return suite.model_copy(update={"scored": cases - stale_items, "stale": stale_items})


async def run(env: Env, rows: list[Row]) -> EvalSuite:
    return score(await run_cases(env, rows, lambda r: check_group(env, r), suite=NAME))


SUITE = Suite(
    name=NAME,
    title="Stage-B fact-checker",
    description=(
        "Stage B's model checker (the smart model since the experiments), which deletes "
        "claims their cited sources do not support. 30 claims over real excerpts, 16 with a "
        "planted error. Unsupported recall "
        "is the share of planted errors it removes; false removal is the share of true "
        "claims it deletes (lost content, since the check fails closed)."
    ),
    dataset="fact_checker.jsonl",
    label_source="claims written against real excerpts, with planted errors",
    run=run,
)
