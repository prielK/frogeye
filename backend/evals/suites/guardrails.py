"""Guardrails: do the deterministic checks catch what they are there to catch?

No model is called. Each case feeds one guard the input it exists for, built from real
verified quotes and the excerpts they came from:

- quotes (`match_quote`, stage A's verbatim check): a genuine quote must pass; a fabricated
  sentence and a paraphrase must fail. A quote with one number changed may pass the fuzzy
  match, so it counts as caught when the guard rejects it OR the evidence it would store
  (the matched source span) keeps the source's true figure;
- numbers (`unknown_numbers_in`, the number guard of stage B and the briefs): a fact whose
  numbers are all in its source (spelled "65 percent" there, or the source's own publication
  date) must pass; an altered or invented number must be flagged;
- citations (`make_analysis_model`, the stage-B output schema): a cite of a ref the model
  was shown must validate, a cite of any other ref must be rejected by the schema itself;
- inline markers (stage B's `ref_cleaner`): "[1]", "(doc:12)", "See doc:4." must be
  stripped exactly, and text that only looks like a marker must stay as written.
"""

from datetime import datetime

from pydantic import ValidationError

from app.agents.analysis_schema import SignalAnalysisContent, make_analysis_model
from app.agents.nodes.signal_analysis import _clean
from app.agents.nodes.verify import match_quote, unknown_numbers_in
from app.schemas.evals import EvalFailure, EvalGroup, EvalSuite
from evals import metrics
from evals.harness import Env
from evals.suites import Outcome, Row, Suite, build

NAME = "guardrails"
ATTACKS = (
    "quote_genuine",
    "quote_fabricated",
    "quote_paraphrased",
    "quote_altered_number",
    "number_genuine",
    "number_altered",
    "number_absent",
    "citation_shown",
    "citation_unshown",
    "marker_present",
    "marker_absent",
)
_ITEM = {"text": "Example.", "cites": []}
_FIELDS = {
    "summary": lambda cite: {**_ITEM, "cites": [cite]},
    "what_happened": lambda cite: [{**_ITEM, "cites": [cite]}],
    "jfrog_impact": lambda cite: [
        {
            "product": "Curation",
            "effect": "threat",
            "severity": "low",
            "rationale": "Example.",
            "cites": [cite],
        }
    ],
    "what_to_watch": lambda cite: [{**_ITEM, "date": None, "cites": [cite]}],
}


def _corpus(row: Row) -> str:
    """A source as stage B shows it to the model and the number guard."""
    return f"Title: {row['source']['title']}\n\n{row['source']['text']}"


def check(row: Row) -> tuple[bool, str]:
    """(flagged, what the guard did) for one case."""
    check, text = row["check"], row["text"]
    if check == "quote":
        span, info = match_quote(text, row["source"]["text"])
        if span is None:
            return True, f"rejected: {info}"
        if row["attack"] == "quote_altered_number" and row["altered"] not in span:
            return True, f"accepted at {info:.0f}, but the stored span keeps the source's figure"
        return False, f"accepted at {info:.0f}"
    if check == "numbers":
        published = row["source"]["published_at"]
        dates = [datetime.fromisoformat(published)] if published else []
        unknown = unknown_numbers_in([text], [_corpus(row)], dates)
        return bool(unknown), f"unknown numbers: {', '.join(unknown)}" if unknown else "all known"
    if check == "citation":
        payload = SignalAnalysisContent().model_dump(mode="json")
        payload[row["field"]] = _FIELDS[row["field"]](row["cite"])
        try:
            make_analysis_model(tuple(row["refs"])).model_validate(payload)
        except ValidationError as exc:
            return True, f"schema rejected {row['cite']} ({exc.error_count()} errors)"
        return False, f"schema accepted {row['cite']}"
    if check == "markers":
        cleaned = _clean(text)
        changed = cleaned != text
        if row["attack"] == "marker_present":
            return cleaned == row["expected"], f"cleaned to {cleaned!r}"
        return changed, f"cleaned to {cleaned!r}"
    raise ValueError(f"unknown check {check!r}")


def score(rows: list[Row]) -> EvalSuite:
    results = [(row, *check(row)) for row in rows]
    pairs = [(row["expect"] == "catch", flagged) for row, flagged, _ in results]
    head = metrics.detection(pairs)
    groups = []
    for attack in ATTACKS:
        mine = [(row, flagged) for row, flagged, _ in results if row["attack"] == attack]
        if not mine:
            continue
        planted = mine[0][0]["expect"] == "catch"
        hits = sum(flagged == planted for _, flagged in mine)
        key, label = ("catch_rate", "Caught") if planted else ("pass_rate", "Passed")
        groups.append(
            EvalGroup(
                name=attack, cases=len(mine), metrics=[metrics.rate(key, label, hits, len(mine))]
            )
        )
    failures = [
        EvalFailure(
            case_id=row["id"],
            expected="caught" if row["expect"] == "catch" else "passed",
            got="caught" if flagged else "passed",
            detail=f"{row['attack']}: {row['text'][:160]!r} -> {what}",
        )
        for row, flagged, what in results
        if flagged != (row["expect"] == "catch")
    ]
    return build(SUITE, [Outcome(r) for r in rows], metrics=head, groups=groups, failures=failures)


async def run(env: Env, rows: list[Row]) -> EvalSuite:
    return score(rows)


SUITE = Suite(
    name=NAME,
    title="Deterministic guardrails",
    description=(
        "The no-LLM checks between the models and the reader: the verbatim-quote match, "
        "the number guard, the citation enum of the stage-B schema and the inline-marker "
        "cleaner. Genuine inputs (real verified quotes and facts) must pass; planted "
        "attacks (fabricated or paraphrased quotes, altered or invented numbers, citations "
        "to unseen sources, inline markers) must be caught."
    ),
    dataset="guardrails.jsonl",
    label_source="real verified quotes and numbers, with planted attacks",
    run=run,
)
