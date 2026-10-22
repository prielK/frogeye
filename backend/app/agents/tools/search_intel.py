from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agents.tools.base import ToolDeps, ToolResult, ToolSpec, register_tool
from app.agents.tools.signals import AGENT_SIGNAL_STATUSES
from app.models import Document, Source
from app.models.enums import RetrievalKind
from app.rag.search import SearchFilters, SearchHit, make_snippet, search

K = 6
SNIPPET = 300


class SearchIntelArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    kinds: list[RetrievalKind] | None = None
    competitors: list[str] | None = Field(default=None, max_length=10)
    days: int | None = Field(default=None, ge=1, le=90)


def utcnow() -> datetime:
    """The clock the recency filter counts back from (the eval harness pins it)."""
    return datetime.now(UTC)


def _fmt(hit: SearchHit, query: str, titles: dict[int, str]) -> str:
    date = hit.published_at.date().isoformat() if hit.published_at else "undated"
    snippet = make_snippet(hit.text, query, SNIPPET)
    if hit.kind == RetrievalKind.signal:
        return f"[S{hit.ref_id}] {date}: {snippet}"
    if hit.kind == RetrievalKind.jfrog_kb:
        return f"[K{hit.ref_id}#{hit.chunk_no}] {date}: {snippet}"
    label = titles.get(hit.ref_id, "")
    return f"[A{hit.ref_id}#{hit.chunk_no}] {date} {label}: {snippet}"


async def search_intel(args: SearchIntelArgs, deps: ToolDeps) -> ToolResult:
    date_from = utcnow() - timedelta(days=args.days) if args.days else None
    filters = SearchFilters(
        kinds=[str(k) for k in args.kinds] if args.kinds else None,
        competitor_slugs=args.competitors or None,
        date_from=date_from,
        signal_statuses=list(AGENT_SIGNAL_STATUSES),  # never rejected or suppressed
    )
    result = await search(
        deps.session_factory,
        deps.llm,
        args.query,
        filters=filters,
        understand=False,
        ctx=deps.run_ctx,
        k=K,
    )
    hits = result.hits
    if not hits:
        return ToolResult(content="No results.", data=[])
    doc_ids = [h.ref_id for h in hits if h.kind == RetrievalKind.article_chunk]
    titles: dict[int, str] = {}
    if doc_ids:
        async with deps.session_factory() as session:
            rows = await session.execute(
                select(Document.id, Source.name, Document.title)
                .join(Source, Source.id == Document.source_id)
                .where(Document.id.in_(doc_ids))
            )
            titles = {i: f"{src} / {title or 'untitled'}" for i, src, title in rows}
    return ToolResult(
        content="\n".join(_fmt(h, args.query, titles) for h in hits),
        data=[h.item_id for h in hits],
    )


register_tool(
    ToolSpec(
        "search_intel",
        "Search collected competitive intelligence (signals and article chunks). Optional "
        "filters: kinds, competitor slugs, and recency in days (max 90). Results carry "
        "citable IDs: [S<id>] signals, [A<doc>#<chunk>] articles.",
        SearchIntelArgs,
        search_intel,
    )
)
