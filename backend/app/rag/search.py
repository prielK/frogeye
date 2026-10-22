"""Hybrid retrieval: pgvector + two FTS configs fused with Reciprocal Rank Fusion, plus
optional LLM query understanding and listwise rerank.

`hybrid_search` is pure SQL and takes a session. `search` awaits LLM calls, so it
takes a `session_factory` and only ever holds short sessions between LLM awaits. Without an
LLM, or when the query embedding fails (an LLM error, a spent budget), `search` runs
keyword-only and says so in `SearchResult.mode` (as /signals?q does).

API callers narrow the candidates in SQL, before the candidate cap and the vector floor:
`signal_statuses` keeps signal hits whose CURRENT status is listed,
`document_statuses` keeps article hits whose document status is listed (the index
holds every embedded document, triage-rejected and never-triaged ones too, because
documents are embedded before triage), `hide_self_documents` drops article hits on
JFrog-self news (the Feed's predicate; JFrog's own docs, kind jfrog_kb, stay) and
`ref_ids` keeps only the listed refs. The agents' search_intel tool passes only
`signal_statuses` (never a rejected or suppressed signal) and otherwise keeps the
full index.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, ConfigDict, create_model
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt, untrusted
from app.models import Competitor
from app.models.enums import RetrievalKind
from app.models.gold import EMBED_DIMS
from app.processing.self_news import self_news_document_ids_sql
from app.rag.indexing import embed_batched

logger = logging.getLogger(__name__)

RRF_K = 60
# for API callers, a vector-only hit (no keyword-arm match) stays only at cosine
# similarity >= max(VECTOR_FLOOR_MIN, best vector similarity - VECTOR_FLOOR_GAP). Tuned on
# the live corpus (120 signals, text-embedding-3-small sims ~0.2-0.6); re-tune as
# the corpus grows.
VECTOR_FLOOR_MIN = 0.30
VECTOR_FLOOR_GAP = 0.12
RERANK_POOL = 30
MAX_VARIANTS = 2
SNIPPET_CHARS = 300
RERANK_SNIPPET_CHARS = 300
_LLM_FAILURES = (LLMError, LLMRefusal, BudgetExceeded)


@dataclass(frozen=True)
class SearchHit:
    item_id: int
    kind: str
    ref_id: int
    chunk_no: int
    text: str
    score: float
    rank_vector: int | None
    rank_keyword: int | None
    published_at: datetime | None
    competitor_ids: list[int] = field(default_factory=list)
    similarity: float | None = None  # cosine similarity to the query (None: no vector arm)


@dataclass(frozen=True)
class SearchFilters:
    """Explicit user filters. They override whatever query understanding extracts.

    `date_from` is inclusive and `date_to` exclusive (both UTC datetimes).
    """

    kinds: list[str] | None = None
    competitor_slugs: list[str] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    approved_only: bool = False
    signal_statuses: list[str] | None = None  # signal hits by their current status
    document_statuses: list[str] | None = None  # article hits by document status
    hide_self_documents: bool = False  # no article hit on JFrog-self news


class QueryPlan(BaseModel):
    """Structured query understanding. `build_query_plan_schema` narrows the slug enum."""

    model_config = ConfigDict(extra="forbid")

    rewritten: str
    variants: list[str]
    competitor_slugs: list[str]
    date_from: date | None
    date_to: date | None
    kinds: list[RetrievalKind]


def build_query_plan_schema(slugs: list[str]) -> type[QueryPlan]:
    """QueryPlan with `competitor_slugs` constrained to a Literal over the known slugs."""
    if not slugs:
        return QueryPlan
    slug_type = Literal[*slugs]  # type: ignore[valid-type]
    return create_model(  # type: ignore[call-overload]
        "QueryPlan", __base__=QueryPlan, competitor_slugs=(list[slug_type], ...)
    )


class RerankOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: list[int]


SearchMode = Literal["hybrid", "keyword"]


@dataclass(frozen=True)
class SearchResult:
    plan: QueryPlan | None
    hits: list[SearchHit]
    mode: SearchMode = "hybrid"  # "keyword": no query embedding could be had


# ---- hybrid SQL ------------------------------------------------------------------------


def _filter_sql(
    kinds: list[str] | None,
    competitor_ids: list[int] | None,
    date_from: datetime | None,
    date_to: datetime | None,
    approved_only: bool,
    *,
    ref_ids: Sequence[int] | None = None,
    signal_statuses: Sequence[str] | None = None,
    document_statuses: Sequence[str] | None = None,
    hide_self_documents: bool = False,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if hide_self_documents:
        clauses.append(
            f"(ri.kind <> :kind_article OR ri.ref_id NOT IN ({self_news_document_ids_sql()}))"
        )
        params["kind_article"] = str(RetrievalKind.article_chunk)
    if ref_ids is not None:
        clauses.append("ri.ref_id = ANY(CAST(:ref_ids AS integer[]))")
        params["ref_ids"] = list(ref_ids)
    if signal_statuses is not None:
        clauses.append(
            "(ri.kind <> :kind_signal OR EXISTS (SELECT 1 FROM signals s"
            " WHERE s.id = ri.ref_id AND s.status = ANY(CAST(:signal_statuses AS text[]))))"
        )
        params |= {
            "kind_signal": str(RetrievalKind.signal),
            "signal_statuses": list(signal_statuses),
        }
    if document_statuses is not None:
        clauses.append(
            "(ri.kind <> :kind_article OR EXISTS (SELECT 1 FROM documents d"
            " WHERE d.id = ri.ref_id AND d.status = ANY(CAST(:document_statuses AS text[]))))"
        )
        params |= {
            "kind_article": str(RetrievalKind.article_chunk),
            "document_statuses": list(document_statuses),
        }
    if kinds:
        clauses.append("ri.kind = ANY(:kinds)")
        params["kinds"] = [str(k) for k in kinds]
    if competitor_ids:
        clauses.append("ri.competitor_ids && CAST(:comp_ids AS integer[])")
        params["comp_ids"] = list(competitor_ids)
    if date_from is not None:
        clauses.append("ri.published_at >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        clauses.append("ri.published_at < :date_to")
        params["date_to"] = date_to
    if approved_only:
        clauses.append("ri.approved")
    return "".join(f" AND {c}" for c in clauses), params


_VECTOR_ARM = """
    SELECT id, ROW_NUMBER() OVER (ORDER BY dist, id) AS r, 1 - dist AS sim
    FROM (
        SELECT ri.id AS id, ri.embedding <=> CAST(:qvec AS vector) AS dist
        FROM retrieval_items ri
        WHERE ri.embedding IS NOT NULL{filters}
        ORDER BY ri.embedding <=> CAST(:qvec AS vector)
        LIMIT :cand
    ) v
"""
# Keyword-only search (no query embedding): an empty vector arm, so RRF reduces to FTS rank.
_NO_VECTOR_ARM = (
    "SELECT NULL::integer AS id, NULL::bigint AS r, NULL::double precision AS sim WHERE false"
)
# keyword-arm hits always pass; vector-only hits need a similarity close to the best.
_VECTOR_FLOOR = """
    WHERE kw.id IS NOT NULL
       OR vec.sim >= GREATEST(:floor_min, (SELECT max(sim) FROM vec) - :floor_gap)
"""

_KEYWORD_ARM = """
    SELECT id, ROW_NUMBER() OVER (ORDER BY rnk DESC, id) AS r
    FROM (
        SELECT ri.id AS id,
               ts_rank_cd(ri.tsv_en, websearch_to_tsquery('english', :q))
                 + ts_rank_cd(ri.tsv_simple, plainto_tsquery('simple', :q)) AS rnk
        FROM retrieval_items ri
        WHERE (ri.tsv_en @@ websearch_to_tsquery('english', :q)
               OR ri.tsv_simple @@ plainto_tsquery('simple', :q)){filters}
        ORDER BY rnk DESC, ri.id
        LIMIT :cand
    ) k
"""
# Vector-only search (the eval harness's vector mode): an empty keyword arm.
_NO_KEYWORD_ARM = "SELECT NULL::integer AS id, NULL::bigint AS r WHERE false"

_HYBRID_SQL = """
WITH vec AS ({vector_arm}),
kw AS ({keyword_arm}),
fused AS (
    SELECT COALESCE(vec.id, kw.id) AS id,
           vec.r AS rank_vector,
           kw.r AS rank_keyword,
           vec.sim AS sim,
           COALESCE(1.0 / (:rrf_k + vec.r), 0) + COALESCE(1.0 / (:rrf_k + kw.r), 0) AS score
    FROM vec FULL OUTER JOIN kw ON vec.id = kw.id{floor}
)
SELECT ri.id, ri.kind, ri.ref_id, ri.chunk_no, ri.text, ri.published_at, ri.competitor_ids,
       f.score, f.rank_vector, f.rank_keyword, f.sim
FROM fused f JOIN retrieval_items ri ON ri.id = f.id
ORDER BY f.score DESC, ri.id
LIMIT :k
"""


async def _tune_hnsw(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    try:
        async with session.begin_nested():  # a failure must not abort the outer transaction
            await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
    except Exception:  # pgvector < 0.8 has no such setting
        logger.debug("hnsw.iterative_scan unavailable; continuing without it")


async def hybrid_search(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float] | None,
    kinds: list[RetrievalKind] | list[str] | None = None,
    competitor_ids: list[int] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    approved_only: bool = False,
    k: int = 8,
    candidates: int = 50,
    vector_floor: bool = False,
    ref_ids: Sequence[int] | None = None,
    signal_statuses: Sequence[str] | None = None,
    document_statuses: Sequence[str] | None = None,
    hide_self_documents: bool = False,
    keyword: bool = True,
) -> list[SearchHit]:
    """Vector + keyword candidates fused with RRF (score = sum of 1/(60+rank)).

    `query_embedding=None` skips the vector arm (keyword-only search, e.g. without an
    OpenAI key): hits then carry `rank_vector=None` and `similarity=None`. `keyword=False`
    skips the keyword arm instead (vector-only, so the eval harness can score each arm on
    its own); it needs a query embedding. `date_from` is
    inclusive, `date_to` exclusive. `approved_only` keeps only items whose `approved` flag
    is set. `vector_floor` (for API callers; off by default so agent tools keep
    their breadth) drops vector-only hits whose similarity is below
    max(VECTOR_FLOOR_MIN, best similarity in the filtered pool - VECTOR_FLOOR_GAP); a hit
    the keyword arm matched always stays. `ref_ids`, `signal_statuses`,
    `document_statuses` and `hide_self_documents` (API callers, see the module docstring)
    apply in both arms, so the candidate cap and the floor only ever see what the caller may
    show. No LLM call happens here."""
    filters, params = _filter_sql(
        kinds,
        competitor_ids,
        date_from,
        date_to,
        approved_only,
        ref_ids=ref_ids,
        signal_statuses=signal_statuses,
        document_statuses=document_statuses,
        hide_self_documents=hide_self_documents,
    )
    if query_embedding is None and not keyword:
        raise ValueError("a vector-only search needs a query embedding")
    params |= {"q": query, "cand": candidates, "k": k, "rrf_k": RRF_K}
    keyword_arm = _KEYWORD_ARM.format(filters=filters) if keyword else _NO_KEYWORD_ARM
    if query_embedding is None:
        sql = _HYBRID_SQL.format(
            vector_arm=_NO_VECTOR_ARM, keyword_arm=keyword_arm, filters=filters, floor=""
        )
        stmt = text(sql)
    else:
        await _tune_hnsw(session)
        vector_arm = _VECTOR_ARM.format(filters=filters)
        floor = ""
        if vector_floor:
            floor = _VECTOR_FLOOR
            params |= {"floor_min": VECTOR_FLOOR_MIN, "floor_gap": VECTOR_FLOOR_GAP}
        sql = _HYBRID_SQL.format(
            vector_arm=vector_arm, keyword_arm=keyword_arm, filters=filters, floor=floor
        )
        stmt = text(sql).bindparams(bindparam("qvec", type_=Vector(EMBED_DIMS)))
        params["qvec"] = query_embedding
    rows = (await session.execute(stmt, params)).all()
    return [
        SearchHit(
            item_id=r.id,
            kind=r.kind,
            ref_id=r.ref_id,
            chunk_no=r.chunk_no,
            text=r.text,
            score=float(r.score),
            rank_vector=r.rank_vector,
            rank_keyword=r.rank_keyword,
            published_at=r.published_at,
            competitor_ids=list(r.competitor_ids or []),
            similarity=float(r.sim) if r.sim is not None else None,
        )
        for r in rows
    ]


# ---- snippets --------------------------------------------------------------------------


def make_snippet(body: str, query: str, limit: int = SNIPPET_CHARS) -> str:
    """At most `limit` chars of `body` around the earliest case-insensitive query-word match
    (else the start of the body)."""
    flat = re.sub(r"\s+", " ", body).strip()
    if len(flat) <= limit:
        return flat
    lowered = flat.lower()
    positions = [
        p
        for w in re.findall(r"\w[\w.\-]*\w|\w", query.lower())
        if len(w) >= 2 and (p := lowered.find(w)) >= 0
    ]
    start = max(0, min(positions) - 80) if positions else 0
    lead = "…" if start else ""
    return lead + flat[start : start + limit - len(lead)]


# ---- search ----------------------------------------------------------------------------


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


async def _understand(
    llm: AccountedLLM, q: str, slugs: list[str], *, ctx: RunCtx
) -> QueryPlan | None:
    schema = build_query_plan_schema(slugs)
    try:
        plan = await llm.structured(
            "query_understanding",
            "fast",
            schema,
            prompt=load_prompt("query_understanding"),
            variables={
                "query": untrusted("query", q, 1000),
                "slugs": ", ".join(slugs) or "(none)",
                "today": datetime.now(UTC).date().isoformat(),
            },
            ctx=ctx,
        )
    except _LLM_FAILURES as exc:
        logger.warning("query understanding failed (%s); using the raw query", exc)
        return None
    variants = [v.strip() for v in plan.variants if v.strip() and v.strip() != q][:MAX_VARIANTS]
    return plan.model_copy(update={"rewritten": plan.rewritten.strip() or q, "variants": variants})


async def _rerank(
    llm: AccountedLLM, q: str, hits: list[SearchHit], *, ctx: RunCtx
) -> list[SearchHit]:
    candidates = "\n".join(
        untrusted(i, make_snippet(h.text, q, RERANK_SNIPPET_CHARS), RERANK_SNIPPET_CHARS)
        for i, h in enumerate(hits)
    )
    try:
        out = await llm.structured(
            "rerank",
            "fast",
            RerankOut,
            prompt=load_prompt("rerank"),
            variables={"query": untrusted("query", q, 1000), "candidates": candidates},
            ctx=ctx,
        )
    except _LLM_FAILURES as exc:
        logger.warning("rerank failed (%s); keeping fused order", exc)
        return hits
    order = list(dict.fromkeys(i for i in out.order if 0 <= i < len(hits)))
    order += [i for i in range(len(hits)) if i not in set(order)]
    return [hits[i] for i in order]


async def _embed_queries(
    llm: AccountedLLM | None, texts: list[str], *, ctx: RunCtx
) -> list[list[float] | None]:
    """One vector per text, or None for each (keyword-only) without an LLM or when the
    embedding call fails or the budget is spent."""
    if llm is None:
        return [None] * len(texts)
    try:
        return list(await embed_batched(llm, texts, ctx=ctx, task="search_embed"))
    except (LLMError, BudgetExceeded) as exc:
        logger.warning("search: no query embedding (%s); keyword-only", type(exc).__name__)
        return [None] * len(texts)


async def search(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM | None,
    q: str,
    *,
    filters: SearchFilters | None = None,
    understand: bool = True,
    rerank: bool | None = None,
    vector_floor: bool = False,
    ctx: RunCtx,
    k: int = 8,
) -> SearchResult:
    """Understand -> embed -> hybrid per variant -> merge -> optional listwise rerank.

    Explicit `filters` always win over what query understanding extracts. LLM failures in
    the understanding or rerank steps degrade to the plain query / fused order; a failed
    query embedding (or `llm=None`) degrades to keyword-only search, `mode="keyword"`.
    `vector_floor` is passed to every `hybrid_search` (/search sets it; the agents'
    search_intel tool does not)."""
    filters = filters or SearchFilters()
    if llm is None:
        understand, rerank = False, False
    async with session_factory() as session:
        slug_ids = {
            slug: cid for cid, slug in await session.execute(select(Competitor.id, Competitor.slug))
        }

    plan = await _understand(llm, q, sorted(slug_ids), ctx=ctx) if understand else None

    kinds = filters.kinds or ([str(x) for x in plan.kinds] if plan else None) or None
    slugs = filters.competitor_slugs or (plan.competitor_slugs if plan else None) or None
    date_from = filters.date_from or (
        _day_start(plan.date_from) if plan and plan.date_from else None
    )
    date_to = filters.date_to or (
        _day_start(plan.date_to) + timedelta(days=1) if plan and plan.date_to else None
    )
    competitor_ids: list[int] | None = None
    if slugs:
        competitor_ids = [slug_ids[s] for s in slugs if s in slug_ids]
        if not competitor_ids:
            return SearchResult(plan=plan, hits=[], mode="hybrid" if llm else "keyword")

    texts = [plan.rewritten, *plan.variants] if plan else [q]
    vectors = await _embed_queries(llm, texts, ctx=ctx)
    mode: SearchMode = "keyword" if any(v is None for v in vectors) else "hybrid"

    best: dict[int, SearchHit] = {}
    async with session_factory() as session:
        for query_text, vector in zip(texts, vectors, strict=True):
            for hit in await hybrid_search(
                session,
                query=query_text,
                query_embedding=vector,
                kinds=kinds,
                competitor_ids=competitor_ids,
                date_from=date_from,
                date_to=date_to,
                approved_only=filters.approved_only,
                k=RERANK_POOL,
                vector_floor=vector_floor,
                signal_statuses=filters.signal_statuses,
                document_statuses=filters.document_statuses,
                hide_self_documents=filters.hide_self_documents,
            ):
                if hit.item_id not in best or hit.score > best[hit.item_id].score:
                    best[hit.item_id] = hit
    pool = sorted(best.values(), key=lambda h: (-h.score, h.item_id))[:RERANK_POOL]

    if llm is not None:
        use_rerank = rerank if rerank is not None else llm.settings.rag_rerank_default
        if use_rerank and len(pool) > 1:
            pool = await _rerank(llm, q, pool, ctx=ctx)
    return SearchResult(plan=plan, hits=pool[:k], mode=mode)


__all__ = [
    "QueryPlan",
    "RerankOut",
    "SearchFilters",
    "SearchHit",
    "SearchMode",
    "SearchResult",
    "build_query_plan_schema",
    "hybrid_search",
    "make_snippet",
    "search",
]
