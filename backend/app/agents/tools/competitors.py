from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agents.tools.base import ToolDeps, ToolResult, ToolSpec, register_tool
from app.models import Competitor


class LookupCompetitorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_or_alias: str = Field(min_length=1, max_length=200)


def _match(comps: list[Competitor], needle: str) -> Competitor | None:
    """Exact slug/name/alias first, then a name substring; the competitor list is small."""
    for c in comps:
        if needle in {c.slug.lower(), c.name.lower(), *(a.lower() for a in c.aliases)}:
            return c
    return next((c for c in comps if needle in c.name.lower()), None)


async def lookup_competitor(args: LookupCompetitorArgs, deps: ToolDeps) -> ToolResult:
    async with deps.session_factory() as session:
        comps = list((await session.execute(select(Competitor).order_by(Competitor.id))).scalars())
    comp = _match(comps, args.name_or_alias.strip().lower())
    if comp is None:
        return ToolResult(content=f"No competitor matches '{args.name_or_alias}'.")
    aliases = ", ".join(comp.aliases) or "-"
    lines = [
        f"slug: {comp.slug}",
        f"name: {comp.name}",
        f"tier: {comp.tier}",
        f"relationship: {comp.relationship}",
        f"aliases: {aliases}",
        f"disambiguation_hint: {comp.disambiguation_hint or '-'}",
        f"website: {comp.website or '-'}",
    ]
    return ToolResult(content="\n".join(lines), data={"slug": comp.slug, "id": comp.id})


register_tool(
    ToolSpec(
        "lookup_competitor",
        "Look up a tracked competitor by name, slug or alias: slug, tier, relationship, "
        "aliases, disambiguation hint and website.",
        LookupCompetitorArgs,
        lookup_competitor,
    )
)
