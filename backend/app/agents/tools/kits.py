from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agents.tools.base import ToolDeps, ToolResult, ToolSpec, register_tool
from app.agents.tools.signals import AGENT_SIGNAL_STATUSES
from app.models import Kit
from app.models.gold import Signal, SignalKit

RECENT = 5


class GetKitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=100)


async def get_kit(args: GetKitArgs, deps: ToolDeps) -> ToolResult:
    async with deps.session_factory() as session:
        kit = (
            await session.execute(select(Kit).where(Kit.slug == args.slug.strip()))
        ).scalar_one_or_none()
        if kit is None:
            return ToolResult(content=f"No KIT with slug '{args.slug}'.")
        sigs = (
            await session.execute(
                select(Signal.id, Signal.first_seen_at, Signal.title, Signal.importance)
                .join(SignalKit, SignalKit.signal_id == Signal.id)
                .where(SignalKit.kit_id == kit.id, Signal.status.in_(AGENT_SIGNAL_STATUSES))
                .order_by(Signal.first_seen_at.desc(), Signal.id.desc())
                .limit(RECENT)
            )
        ).all()
    keywords = ", ".join(kit.keywords) or "-"
    lines = [
        f"KIT {kit.slug}: {kit.title}",
        f"question: {kit.question}",
        f"keywords: {keywords}",
        "recent signals:" if sigs else "recent signals: none",
    ]
    lines += [
        f"[S{s.id}] {s.first_seen_at.date()} {s.title} (importance {s.importance})" for s in sigs
    ]
    return ToolResult(content="\n".join(lines), data={"kit_id": kit.id})


register_tool(
    ToolSpec(
        "get_kit",
        "Get a Key Intelligence Topic by slug: its question, keywords and 5 most recent signals.",
        GetKitArgs,
        get_kit,
    )
)
