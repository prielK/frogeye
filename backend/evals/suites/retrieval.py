"""Retrieval: does search put the right documents and KB pages on top?

25 analyst queries over a fixed corpus (the eval's 86 documents, all indexed whatever their
relevance, plus 15 JFrog KB pages), each with the items an analyst would want, scored per
mode through the app's own search code:

- keyword: `search` without an LLM (the app's keyword fallback: the two FTS arms);
- vector: `hybrid_search` with only its vector arm (`keyword=False`);
- hybrid: `search` with the query embedded (vector + keyword fused with RRF, k=60);
- hybrid_rerank: the same plus the fast model's listwise rerank of the top 30.

Query understanding is off (its prompt carries today's date, so its answers cannot be
replayed) and so is the vector floor (it trims results for display; this measures ranking).
A competitor-filtered query passes the filter as the app's API does. Hits are scored per
item, so several chunks of one document count once.
"""

from sqlalchemy import select

from app.models import Competitor
from app.models.enums import RetrievalKind
from app.rag.indexing import embed_batched
from app.rag.search import SearchFilters, SearchHit, hybrid_search, search
from app.schemas.evals import EvalFailure, EvalGroup, EvalSuite
from evals import metrics
from evals.harness import Env
from evals.suites import Outcome, Row, Suite, build, run_cases

NAME = "retrieval"
MODES = ("keyword", "vector", "hybrid", "hybrid_rerank")
HEADLINE = "hybrid"  # the app's default mode (rerank is off by default)
K = 10
_PREFIX = {RetrievalKind.article_chunk: "doc", RetrievalKind.jfrog_kb: "kb"}


def _refs(hits: list[SearchHit]) -> list[str]:
    return metrics.dedupe(f"{_PREFIX.get(h.kind, h.kind)}:{h.ref_id}" for h in hits)


async def _mode(env: Env, row: Row, mode: str) -> list[str]:
    filters = SearchFilters(competitor_slugs=row["competitors"] or None)
    if mode == "vector":
        [vector] = await embed_batched(env.llm, [row["query"]], ctx=env.ctx, task="search_embed")
        async with env.session_factory() as session:
            ids = None
            if row["competitors"]:
                ids = list(
                    await session.scalars(
                        select(Competitor.id).where(Competitor.slug.in_(row["competitors"]))
                    )
                )
            hits = await hybrid_search(
                session,
                query=row["query"],
                query_embedding=vector,
                competitor_ids=ids,
                k=K,
                keyword=False,
            )
        return _refs(hits)
    result = await search(
        env.session_factory,
        None if mode == "keyword" else env.llm,
        row["query"],
        filters=filters,
        understand=False,
        rerank=mode == "hybrid_rerank",
        ctx=env.ctx,
        k=K,
    )
    return _refs(result.hits)


def score(by_mode: dict[str, list[Outcome]], cases: int) -> EvalSuite:
    groups = []
    head = []
    for mode in MODES:
        runs = [(o.value or [], set(o.row["relevant"])) for o in by_mode[mode] if not o.stale]
        groups.append(EvalGroup(name=mode, cases=len(runs), metrics=metrics.ranking(runs)))
        if mode == HEADLINE:
            head = metrics.ranking(runs)
    for style in ("keyword", "semantic", "filtered"):
        runs = [
            (o.value or [], set(o.row["relevant"]))
            for o in by_mode[HEADLINE]
            if not o.stale and o.row["style"] == style
        ]
        groups.append(
            EvalGroup(
                name=f"{HEADLINE} · {style} queries", cases=len(runs), metrics=metrics.ranking(runs)
            )
        )
    failures = []
    for o in by_mode[HEADLINE]:
        if o.stale:
            continue
        relevant, got = set(o.row["relevant"]), o.value or []
        if metrics.recall_at(got, relevant, 8) < 1:
            missing = [r for r in o.row["relevant"] if r not in got[:8]]
            failures.append(
                EvalFailure(
                    case_id=o.row["id"],
                    expected=", ".join(o.row["relevant"]),
                    got=", ".join(got[:8]) or (o.error or "(no hits)"),
                    detail=f"{o.row['query']!r} ({o.row['style']}"
                    + (
                        f", filter {', '.join(o.row['competitors'])}"
                        if o.row["competitors"]
                        else ""
                    )
                    + f"): not in the top 8: {', '.join(missing)}",
                )
            )
    # a query is stale when any of its modes is (the modes are compared on the same queries)
    stale_ids = {o.row["id"] for mode in MODES for o in by_mode[mode] if o.stale}
    outcomes = [
        Outcome(o.row, stale="not recorded" if o.row["id"] in stale_ids else None)
        for o in by_mode[HEADLINE]
    ]
    return build(SUITE, outcomes, metrics=head, groups=groups, failures=failures, cases=cases)


async def run(env: Env, rows: list[Row]) -> EvalSuite:
    if env.unindexed:  # an incomplete corpus would score every mode on the wrong pool
        reason = f"corpus not fully recorded ({len(env.unindexed)} items)"
        stale = {m: [Outcome(r, stale=reason) for r in rows] for m in MODES}
        return score(stale, len(rows))
    by_mode = {
        mode: await run_cases(env, rows, lambda r, m=mode: _mode(env, r, m), suite=NAME)
        for mode in MODES
    }
    return score(by_mode, len(rows))


SUITE = Suite(
    name=NAME,
    title="Retrieval",
    description=(
        "Search quality on 25 analyst queries (keyword-style, paraphrased/semantic and "
        "competitor-filtered) over a fixed corpus of 86 documents and 15 JFrog KB pages, "
        "per mode: keyword only, vector only, hybrid (the app's default) and hybrid with "
        "the LLM rerank. Recall@8, MRR@10 and nDCG@8 over the labelled relevant items."
    ),
    dataset="retrieval.jsonl",
    label_source="labelled blind from each document's raw text, never from FrogEye's outputs",
    run=run,
)
