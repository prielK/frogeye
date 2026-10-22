"""Disambiguation: does the smart agent resolve an ambiguous name to the right vendor?

Each case is a document that mentions an ambiguous competitor name (Harness, Socket,
Docker, Mend, Wiz, Aqua, Black Duck, GitHub, Syft). The app's `triage_one` makes the first
decision, then the app's `disambiguate_one` re-judges it with its real tools
(`lookup_competitor`, and `search_intel` over the eval corpus). The label says whether the
document is about that competitor: a word-sense accident ("agent harness") and a passing
mention (Socket cited in a Sonatype post) are both "not about it".

The agent is run on every case, also where production would not route the document to it,
so the suite measures the agent itself; triage alone on the same cases is the baseline.
"""

from app.agents.nodes.disambiguate import disambiguate_one
from app.agents.nodes.triage import triage_one
from app.schemas.evals import EvalFailure, EvalGroup, EvalSuite
from evals import metrics
from evals.harness import Env
from evals.suites import Outcome, Row, Suite, build, run_cases, slugs, stored_view

NAME = "disambiguation"


def _about(out) -> list[str]:
    return sorted(set(out.competitor_slugs)) if out.relevant else []


async def _resolve(env: Env, row: Row) -> dict | None:
    first = await triage_one(
        row["doc_id"],
        llm=env.llm,
        session_factory=env.session_factory,
        enums=env.enums,
        ctx=env.ctx,
    )
    if first is None:
        return None
    final = await disambiguate_one(first, llm=env.llm, deps=env.deps, enums=env.enums, ctx=env.ctx)
    stored, _ = await stored_view(env, row["doc_id"], final)
    return {
        "first": _about(first.out),
        "final": _about(final),
        "stored": stored if final.relevant else [],
        "routed": first.needs_disambiguation,
        "reason": final.reason,
    }


def _right(row: Row, about: list[str]) -> bool:
    return (row["candidate"] in about) == (row["expected"] is not None)


def score(outcomes: list[Outcome]) -> EvalSuite:
    scored = [o for o in outcomes if not o.stale and o.value is not None]
    errors = [o for o in outcomes if not o.stale and o.value is None]
    final = [_right(o.row, o.value["final"]) for o in scored]
    first = [_right(o.row, o.value["first"]) for o in scored]
    stored = [_right(o.row, o.value["stored"]) for o in scored]
    fixed = sum(f and not t for f, t in zip(final, first, strict=True))
    broken = sum(t and not f for f, t in zip(final, first, strict=True))
    n = len(scored) + len(errors)  # an LLM error counts as a wrong answer
    head = [
        metrics.rate("accuracy", "Accuracy after disambiguation", sum(final), n),
        metrics.rate("triage_accuracy", "Accuracy of triage alone (baseline)", sum(first), n),
        metrics.rate(
            "stored_accuracy", "Accuracy as stored (own-feed competitor added)", sum(stored), n
        ),
        metrics.rate("fixed", "Cases the agent fixed", fixed, n),
        metrics.rate("broken", "Cases the agent broke", broken, n),
        metrics.rate(
            "routed", "Routed to the agent in production", sum(o.value["routed"] for o in scored), n
        ),
    ]
    groups = []
    for name, positive in (("about the competitor", True), ("not about it", False)):
        rows = [
            (o, r)
            for o, r in zip(scored, final, strict=True)
            if (o.row["expected"] is not None) == positive
        ]
        groups.append(
            EvalGroup(
                name=name,
                cases=len(rows),
                metrics=[metrics.rate("accuracy", "Accuracy", sum(r for _, r in rows), len(rows))],
            )
        )
    failures = [
        EvalFailure(
            case_id=o.row["id"],
            expected=f"about {o.row['candidate']}"
            if o.row["expected"]
            else f"not about {o.row['candidate']}",
            got=f"about {slugs(o.value['final'])} (triage alone: {slugs(o.value['first'])})",
            detail=f'doc {o.row["doc_id"]} "{o.row["title"]}" mentions {o.row["mention"]!r}; '
            f"agent: {o.value['reason']}",
        )
        for o, right in zip(scored, final, strict=True)
        if not right
    ] + [
        EvalFailure(case_id=o.row["id"], expected="a decision", got="no decision (LLM error)")
        for o in errors
    ]
    return build(SUITE, outcomes, metrics=head, groups=groups, failures=failures)


async def run(env: Env, rows: list[Row]) -> EvalSuite:
    return score(await run_cases(env, rows, lambda r: _resolve(env, r), suite=NAME))


SUITE = Suite(
    name=NAME,
    title="Competitor disambiguation",
    description=(
        "The smart tool-using agent that re-judges triage when a document names an "
        "ambiguous competitor. 28 real documents: the company itself, a same-name product "
        "of another vendor (Chainguard Harness, Microsoft's Agent Harness), a word sense "
        "(agent harness, network socket, 'docker run') or a passing mention."
    ),
    dataset="disambiguation.jsonl",
    label_source="labelled blind from each document's raw text, never from FrogEye's outputs",
    run=run,
)
