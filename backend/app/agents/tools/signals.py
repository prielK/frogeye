from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, literal, select

from app.agents.tools.base import ToolDeps, ToolResult, ToolSpec, register_tool
from app.models import Competitor, Document, Source
from app.models.enums import SignalType
from app.models.gold import Evidence, Signal, SignalCompetitor, SignalDocument
from app.rag.indexing import embed_batched

QUOTE_CHARS = 300
TOP_TITLES = 3
NONE_TOP = 10
# the signal statuses agents may see. Rejected and suppressed signals (scope "jfrog"
# among them) are never prior context, a search hit or a listed title.
AGENT_SIGNAL_STATUSES = ("pending", "auto", "needs_review", "approved")


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _signal_line(s) -> str:
    stype = s.signal_type or "unclassified"
    return f"[S{s.id}] {s.first_seen_at.date()} {stype} {s.title} (importance {s.importance})"


# ---- get_similar_signals ---------------------------------------------------------------


class SimilarSignalsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: int | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    days: int = Field(default=60, ge=1, le=365)
    k: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.signal_id is None) == (self.text is None):
            raise ValueError("provide exactly one of signal_id or text")
        return self


async def get_similar_signals(args: SimilarSignalsArgs, deps: ToolDeps) -> ToolResult:
    vector: list[float] | None = None
    if args.signal_id is not None:
        async with deps.session_factory() as session:
            vector = (
                await session.execute(select(Signal.centroid).where(Signal.id == args.signal_id))
            ).scalar_one_or_none()
        if vector is None:
            return ToolResult(content=f"Signal S{args.signal_id} not found or has no embedding.")
    else:
        [vector] = await embed_batched(deps.llm, [args.text], ctx=deps.run_ctx, task="tool_embed")
    dist = Signal.centroid.cosine_distance(list(vector))
    stmt = (
        select(Signal)
        .where(
            Signal.centroid.is_not(None),
            Signal.first_seen_at >= _since(args.days),
            Signal.status.in_(AGENT_SIGNAL_STATUSES),
        )
        .order_by(dist, Signal.id)
        .limit(args.k)
    )
    if args.signal_id is not None:
        stmt = stmt.where(Signal.id != args.signal_id)
    async with deps.session_factory() as session:
        sigs = list((await session.execute(stmt)).scalars())
    if not sigs:
        return ToolResult(content="No similar signals.", data=[])
    return ToolResult(content="\n".join(_signal_line(s) for s in sigs), data=[s.id for s in sigs])


# ---- get_signal ------------------------------------------------------------------------


class GetSignalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int


async def get_signal(args: GetSignalArgs, deps: ToolDeps) -> ToolResult:
    async with deps.session_factory() as session:
        sig = await session.get(Signal, args.id)
        if sig is None or sig.status not in AGENT_SIGNAL_STATUSES:  # hidden is absent
            return ToolResult(content=f"Signal S{args.id} not found.")
        docs = (
            await session.execute(
                select(Document.id, Document.title, Document.canonical_url, Source.name)
                .join(SignalDocument, SignalDocument.document_id == Document.id)
                .join(Source, Source.id == Document.source_id)
                .where(SignalDocument.signal_id == args.id)
                .order_by(SignalDocument.is_primary.desc(), Document.id)
            )
        ).all()
        evidence = (
            await session.execute(
                select(Evidence.document_id, Evidence.quote, Evidence.verified)
                .where(Evidence.signal_id == args.id)
                .order_by(Evidence.id)
            )
        ).all()
    lines = [
        _signal_line(sig),
        f"fact: {sig.fact or '-'}",
        f"impact: {sig.impact or '-'}",
        "sources:" if docs else "sources: none",
    ]
    lines += [f"- [D{d.id}] {d.name}: {d.title or 'untitled'} <{d.canonical_url}>" for d in docs]
    lines.append("evidence:" if evidence else "evidence: none")
    for e in evidence:
        mark = "verified" if e.verified else "unverified"
        quote = " ".join(e.quote.split())[:QUOTE_CHARS]
        lines.append(f'- [D{e.document_id}] ({mark}) "{quote}"')
    return ToolResult(content="\n".join(lines), data={"id": sig.id})


# ---- query_signals ---------------------------------------------------------------------


class QuerySignalsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competitor: str | None = Field(default=None, max_length=100)
    signal_type: SignalType | None = None
    days: int = Field(default=30, ge=1, le=365)
    group_by: Literal["competitor", "signal_type", "none"] = "none"


async def query_signals(args: QuerySignalsArgs, deps: ToolDeps) -> ToolResult:
    needs_comp = args.competitor is not None or args.group_by == "competitor"
    if args.group_by == "competitor":
        key = Competitor.slug
    elif args.group_by == "signal_type":
        key = func.coalesce(Signal.signal_type, "unclassified")
    else:
        key = literal("all")
    limit = NONE_TOP if args.group_by == "none" else TOP_TITLES
    ranked = select(
        key.label("grp"),
        Signal.id.label("id"),
        Signal.title.label("title"),
        func.count().over(partition_by=key).label("n"),
        func.row_number()
        .over(partition_by=key, order_by=(Signal.importance.desc(), Signal.id.desc()))
        .label("rn"),
    ).where(Signal.first_seen_at >= _since(args.days), Signal.status.in_(AGENT_SIGNAL_STATUSES))
    if needs_comp:
        ranked = ranked.join(SignalCompetitor, SignalCompetitor.signal_id == Signal.id).join(
            Competitor, Competitor.id == SignalCompetitor.competitor_id
        )
    if args.competitor is not None:
        ranked = ranked.where(Competitor.slug == args.competitor.strip())
    if args.signal_type is not None:
        ranked = ranked.where(Signal.signal_type == args.signal_type.value)
    sub = ranked.subquery()
    stmt = (
        select(sub.c.grp, sub.c.id, sub.c.title, sub.c.n)
        .where(sub.c.rn <= limit)
        .order_by(sub.c.n.desc(), sub.c.grp, sub.c.rn)
    )
    async with deps.session_factory() as session:
        rows = (await session.execute(stmt)).all()
    if not rows:
        return ToolResult(content=f"No signals in the last {args.days} days.", data=[])
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.grp, []).append(r)
    lines: list[str] = []
    for grp, items in groups.items():
        if args.group_by == "none":
            lines.append(f"{items[0].n} signals in the last {args.days} days; top titles:")
        else:
            lines.append(f"{grp}: {items[0].n} signals")
        lines += [f"  [S{r.id}] {r.title}" for r in items]
    return ToolResult(content="\n".join(lines), data={g: i[0].n for g, i in groups.items()})


register_tool(
    ToolSpec(
        "get_similar_signals",
        "Find existing signals similar to a given signal (signal_id) or free text (text), "
        "within the last N days. Provide exactly one of signal_id or text.",
        SimilarSignalsArgs,
        get_similar_signals,
    )
)
register_tool(
    ToolSpec(
        "get_signal",
        "Get one signal by id: fact, impact, source documents and evidence quotes.",
        GetSignalArgs,
        get_signal,
    )
)
register_tool(
    ToolSpec(
        "query_signals",
        "Count recent signals, optionally filtered by competitor slug / signal type and "
        "grouped by competitor or signal_type; returns top titles per group.",
        QuerySignalsArgs,
        query_signals,
    )
)
