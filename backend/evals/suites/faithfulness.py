"""Faithfulness: are the stored stage-B analyses backed by the sources they cite?

A frozen export of 20 real analyses from the running stack (the 20 most important signals
analysed with the current prompts). Every factual item, listed by stage B's own
`_factual_items` (summary, what happened, background, what changed, JFrog impact,
competitive implications, risks, what to watch), comes with its evidence: for each source
it cites, the passages of the text the stage-B writer was shown (a member document's first
1,500 words, a KB page's two chunks closest to the signal, a prior signal's record) that
best match the item, about 2,000 characters per item in all. An LLM judge on the smart
model with its own prompt (evals/prompts/faithfulness_judge.md, not the app's
fact-checker prompt) rates each item supported, partial or unsupported.

Citation validity is deterministic: an item's citation is valid when its ref resolves to a
source of that analysis (a doc: ref to one of the signal's member documents, a kb: or
signal: ref to a page or signal that exists).

Coverage is reported beside the ratings: the share of an item's words found in the text
the writer saw that the judge's passages also contain.

Limits: the judge is a model too (it can be wrong, and it shares the vendor of the models
it audits); the passages are a selection, so a rating can still miss evidence outside them;
the export is a snapshot, so new analyses need a new export (README).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, create_model

from app.agents.analysis_schema import SignalAnalysisContent
from app.agents.nodes.signal_analysis import _factual_items, _Item
from app.agents.runtime.prompts import load_prompt, untrusted
from app.agents.verified_llm import ask_until_judged
from app.schemas.evals import EvalFailure, EvalGroup, EvalSuite
from evals import metrics
from evals.harness import PROMPTS, Env
from evals.suites import Outcome, Row, Suite, build, run_cases

NAME = "faithfulness"
TASK = "eval_faithfulness_judge"
PROMPT = "faithfulness_judge"
Rating = Literal["supported", "partial", "unsupported"]
RATINGS = (
    ("supported", "Supported"),
    ("partial", "Partially supported"),
    ("unsupported", "Unsupported"),
)
SECTIONS = (
    "summary",
    "what_happened",
    "background",
    "what_changed",
    "jfrog_impact",
    "competitive_implications",
    "risks_opportunities",
    "what_to_watch",
)
EXCERPT_CHARS = 2500  # an item's passages are about 2,000 characters in all


def make_judge_model(n: int) -> type[BaseModel]:
    """Verdicts over S1..Sn (the shape `ask_until_judged` reads)."""
    sentence_id = Literal[tuple(f"S{i}" for i in range(1, n + 1))]  # type: ignore[valid-type]
    strict = ConfigDict(extra="forbid")
    verdict = create_model(
        "JudgeVerdict",
        __config__=strict,
        sentence_id=(sentence_id, ...),
        reason=(str, ...),  # before the rating, so the rating follows the reasoning
        rating=(Rating, ...),
    )
    return create_model("JudgeOut", __config__=strict, verdicts=(list[verdict], ...))


def items_of(row: Row) -> list[_Item]:
    content = SignalAnalysisContent.model_validate(row["content"]).model_dump(mode="json")
    return _factual_items(content)


def valid_cite(row: Row, ref: str) -> bool:
    if ref not in row["sources"]:
        return False
    return not ref.startswith("doc:") or ref in row["members"]


def _evidence(row: Row) -> dict[tuple[str, int | None], dict]:
    return {(it["section"], it["index"]): it for it in row["items"]}


def _items_block(row: Row, items: list[_Item]) -> str:
    """Each item, then the passages of each source it cites, all as untrusted data."""
    evidence = _evidence(row)
    blocks = []
    for n, it in enumerate(items, 1):
        lines = [
            f"S{n} ({it.label}; cites {', '.join(it.cites)})",
            untrusted(f"S{n}", it.text, max_chars=EXCERPT_CHARS),
        ]
        for ref, text in evidence[(it.section, it.index)]["evidence"].items():
            s = row["sources"][ref]
            when = f", published {s['published_at'][:10]}" if s["published_at"] else ""
            lines.append(f"{ref} ({s['source']}{when}; passages)")
            lines.append(untrusted(f"S{n} {ref}", text, max_chars=EXCERPT_CHARS))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def _judge(env: Env, row: Row) -> dict[int, tuple[str, str]]:
    items = items_of(row)
    prompt = load_prompt(PROMPT, directory=PROMPTS)

    async def ask(pending: list[int]):
        shown = [items[i] for i in pending]
        out = await env.llm.structured(
            TASK,
            "smart",
            make_judge_model(len(shown)),
            prompt=prompt,
            variables={"items": _items_block(row, shown)},
            ctx=env.ctx,
        )
        return out.verdicts

    found, _ = await ask_until_judged(len(items), ask)
    worst = {"supported": 0, "partial": 1, "unsupported": 2}
    out = {}
    for i, verdicts in found.items():  # a repeated id keeps its harshest rating
        v = max(verdicts, key=lambda v: worst[v.rating])
        out[i] = (v.rating, v.reason)
    return out


def score(outcomes: list[Outcome]) -> EvalSuite:
    rated: list[tuple[Row, int, _Item, str, str]] = []
    unjudged: list[tuple[Row, int, _Item]] = []
    all_items: list[tuple[Row, int, _Item]] = []
    for o in outcomes:
        for i, item in enumerate(items_of(o.row)):
            all_items.append((o.row, i, item))
            if o.stale:
                continue
            if i in (o.value or {}):
                rated.append((o.row, i, item, *o.value[i]))
            else:
                unjudged.append((o.row, i, item))
    ratings = [r for *_, r, _ in rated]
    cites = [(row, c) for row, _, item in all_items for c in item.cites]
    items_valid = sum(all(valid_cite(row, c) for c in item.cites) for row, _, item in all_items)
    head = [
        *metrics.shares(ratings, RATINGS),
        metrics.rate(
            "citation_validity", "Items whose citations all resolve", items_valid, len(all_items)
        ),
        metrics.rate(
            "cite_validity",
            "Citations that resolve",
            sum(valid_cite(row, c) for row, c in cites),
            len(cites),
        ),
        metrics.rate(
            "unjudged", "Items the judge left unrated", len(unjudged), len(rated) + len(unjudged)
        ),
        metrics.rate(
            "excerpt_share",
            "Item words in the cited text the writer saw that the judge also sees",
            sum(it["words"]["shown"] for o in outcomes for it in o.row["items"]),
            sum(it["words"]["seen"] for o in outcomes for it in o.row["items"]),
        ),
    ]
    groups = []
    for section in SECTIONS:
        mine = [r for _, _, item, r, _ in rated if item.section == section]
        if mine:
            groups.append(
                EvalGroup(
                    name=section,
                    cases=len(mine),
                    metrics=[
                        metrics.rate(
                            "supported_share", "Supported", mine.count("supported"), len(mine)
                        )
                    ],
                )
            )
    failures = [
        EvalFailure(
            case_id=f"{row['id']}/S{i + 1}",
            expected="supported",
            got=rating,
            detail=f"judge: {reason} | {item.label}, cites {', '.join(item.cites)}: "
            f"{item.text[:200]!r}",
        )
        for row, i, item, rating, reason in rated
        if rating != "supported"
    ]
    failures += [
        EvalFailure(
            case_id=f"{row['id']}/S{i + 1}",
            expected="citations that resolve",
            got=", ".join(c for c in item.cites if not valid_cite(row, c)),
            detail=f"{item.label}: {item.text[:160]!r}",
        )
        for row, i, item in all_items
        if not all(valid_cite(row, c) for c in item.cites)
    ]
    stale_items = sum(len(items_of(o.row)) for o in outcomes if o.stale)
    suite = build(
        SUITE, outcomes, metrics=head, groups=groups, failures=failures, cases=len(all_items)
    )
    return suite.model_copy(update={"scored": len(all_items) - stale_items, "stale": stale_items})


async def run(env: Env, rows: list[Row]) -> EvalSuite:
    return score(await run_cases(env, rows, lambda r: _judge(env, r), suite=NAME))


SUITE = Suite(
    name=NAME,
    title="Faithfulness of stored analyses",
    description=(
        "Every factual item of 20 real stored stage-B analyses, rated by an independent "
        "LLM judge (the smart model, its own prompt) against the passages of its cited "
        "sources that best match it, taken from the text the stage-B writer was shown "
        "(about 2,000 characters per item), plus a deterministic check that every citation "
        "resolves to a source the analysis had. The judge is a model too."
    ),
    dataset="faithfulness.jsonl",
    label_source="rated by a separate judge model; frozen export of stored analyses",
    run=run,
)
