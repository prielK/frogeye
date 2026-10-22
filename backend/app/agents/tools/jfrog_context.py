from pydantic import BaseModel, ConfigDict, Field

from app.agents.tools.base import ToolDeps, ToolResult, ToolSpec, register_tool
from app.models.enums import RetrievalKind
from app.rag.indexing import embed_batched
from app.rag.search import hybrid_search, make_snippet

K = 4
SNIPPET = 300


class JfrogContextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=500)


async def get_jfrog_context(args: JfrogContextArgs, deps: ToolDeps) -> ToolResult:
    topic = args.topic.strip()
    [vector] = await embed_batched(deps.llm, [topic], ctx=deps.run_ctx, task="tool_embed")
    async with deps.session_factory() as session:
        hits = await hybrid_search(
            session,
            query=topic,
            query_embedding=vector,
            kinds=[RetrievalKind.jfrog_kb],
            k=K,
        )
    if not hits:
        return ToolResult(content="No JFrog knowledge-base passages found.", data=[])
    lines = [f"[K{h.ref_id}#{h.chunk_no}] {make_snippet(h.text, topic, SNIPPET)}" for h in hits]
    return ToolResult(content="\n".join(lines), data=[h.item_id for h in hits])


register_tool(
    ToolSpec(
        "get_jfrog_context",
        "Look up what JFrog's own docs say about a topic (JFrog knowledge base passages).",
        JfrogContextArgs,
        get_jfrog_context,
    )
)
