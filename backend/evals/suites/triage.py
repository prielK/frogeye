"""Triage: does the fast model file the right documents, under the right vendors?

Runs the app's `triage_one` on each labelled document of the corpus (its prompt, its
schema, its 400-word view of the document) and scores:

- relevance of the model's call on its own (every case is sent to it, also documents the
  keyword screen would have stopped, so this is the model at its hardest);
- relevance as the pipeline decides it: the app's keyword screen (`AliasMatcher`, run on
  the excerpt at setup) first, then triage;
- attribution as stored: the competitor links `apply_triage` would write (the model's
  slugs plus the source's own-feed competitor, never JFrog), and in the model's own output.
"""

from app.agents.nodes.triage import triage_one
from app.schemas.evals import EvalFailure, EvalGroup, EvalSuite
from evals import metrics
from evals.harness import Env
from evals.suites import Outcome, Row, Suite, build, run_cases, slugs, stored_view

NAME = "triage"
KINDS = ("hard_negative", "hard_positive", "negative", "positive")
SLUG_DUMP = 5  # an output naming more vendors than this lists the enum, not the story


async def _triage(env: Env, row: Row) -> dict | None:
    decision = await triage_one(
        row["doc_id"],
        llm=env.llm,
        session_factory=env.session_factory,
        enums=env.enums,
        ctx=env.ctx,
    )
    if decision is None:  # an LLM error or refusal: the document would stay untriaged
        return None
    out = decision.out
    stored, screened = await stored_view(env, row["doc_id"], out)
    return {
        "relevant": out.relevant,
        "model": sorted(set(out.competitor_slugs)) if out.relevant else [],
        "listed": len(out.competitor_slugs),
        "stored": stored if out.relevant else [],
        "screened": screened,
        "reason": out.reason,
        "routed": decision.needs_disambiguation,
    }


def _relevant(o: Outcome) -> bool:
    return bool(o.value and o.value["relevant"])


def _pipeline(o: Outcome) -> bool:
    return _relevant(o) and o.value["screened"]


def _expected(row: Row) -> str:
    return f"relevant {slugs(row['competitors'])}" if row["relevant"] else "not relevant"


def _got(got: dict | None) -> str:
    if got is None:
        return "no decision (LLM error)"
    return f"relevant {slugs(got['stored'])}" if got["relevant"] else "not relevant"


def _attributed(row: Row, got: list[str]) -> bool:
    return metrics.attribution_ok(row["competitors"], row["also_ok"], got)


def score(outcomes: list[Outcome]) -> EvalSuite:
    scored = [o for o in outcomes if not o.stale]
    pairs = [(o.row["relevant"], _relevant(o)) for o in scored]
    pipeline = [(o.row["relevant"], _pipeline(o)) for o in scored]
    both = [o for o in scored if o.row["relevant"] and _relevant(o)]
    both_ids = {o.row["id"] for o in both}
    stored_ok = {o.row["id"] for o in both if _attributed(o.row, o.value["stored"])}
    model_ok = sum(_attributed(o.row, o.value["model"]) for o in both)
    dumps = sum(o.value["listed"] > SLUG_DUMP for o in scored if o.value)
    routed = sum(o.value["routed"] for o in scored if o.value)
    p_prec, p_rec, _, p_acc = metrics.classification(pipeline)
    head = [
        *metrics.classification(pairs),
        p_prec.model_copy(
            update={"key": "pipeline_precision", "label": "Precision, keyword screen first"}
        ),
        p_rec.model_copy(
            update={"key": "pipeline_recall", "label": "Recall, keyword screen first"}
        ),
        p_acc.model_copy(
            update={"key": "pipeline_accuracy", "label": "Accuracy, keyword screen first"}
        ),
        metrics.rate(
            "attribution_accuracy",
            "Competitor attribution as stored (relevant docs filed relevant)",
            len(stored_ok),
            len(both),
        ),
        metrics.rate(
            "model_attribution_accuracy",
            "Competitor attribution in the model's own output",
            model_ok,
            len(both),
        ),
        metrics.rate(
            "slug_dumps", f"Outputs naming more than {SLUG_DUMP} competitors", dumps, len(scored)
        ),
        metrics.rate("routed_to_disambiguation", "Sent on to disambiguation", routed, len(scored)),
    ]
    groups = []
    for kind in KINDS:
        rows = [o for o in scored if o.row["kind"] == kind]
        right = sum(o.row["relevant"] == _relevant(o) for o in rows)
        piped = sum(o.row["relevant"] == _pipeline(o) for o in rows)
        groups.append(
            EvalGroup(
                name=kind,
                cases=len(rows),
                metrics=[
                    metrics.rate("accuracy", "Accuracy", right, len(rows)),
                    metrics.rate("pipeline_accuracy", "With the keyword screen", piped, len(rows)),
                ],
            )
        )
    failures = []
    for o in scored:
        wrong_relevance = o.row["relevant"] != _relevant(o)
        wrong_vendor = o.row["id"] in both_ids and o.row["id"] not in stored_ok
        if not (wrong_relevance or wrong_vendor):
            continue
        v = o.value or {}
        extra = ""
        if v:
            extra = (
                f"; model listed {v['listed']} slugs {slugs(v['model'])}"
                f"; keyword screen {'passed' if v['screened'] else 'stopped it'}"
            )
        failures.append(
            EvalFailure(
                case_id=o.row["id"],
                expected=_expected(o.row),
                got=_got(o.value),
                detail=f'doc {o.row["doc_id"]} "{o.row["title"]}" ({o.row["kind"]}){extra}; '
                f"model: {v.get('reason', '-')}",
            )
        )
    return build(SUITE, outcomes, metrics=head, groups=groups, failures=failures)


async def run(env: Env, rows: list[Row]) -> EvalSuite:
    return score(await run_cases(env, rows, lambda r: _triage(env, r), suite=NAME))


SUITE = Suite(
    name=NAME,
    title="Triage",
    description=(
        "The fast model's relevance call and competitor attribution on 60 real collected "
        "documents, half of them hard cases: word-sense traps (an AI agent 'harness', network "
        "sockets, a 'docker run' line, an AWS 'wizard'), JFrog's own news, which triage must "
        "never file, and short or headline-only items. Precision and recall are of 'relevant', "
        "for the model alone and with the app's keyword screen in front of it."
    ),
    dataset="triage.jsonl",
    label_source="labelled blind from each document's raw text, never from FrogEye's outputs",
    run=run,
)
