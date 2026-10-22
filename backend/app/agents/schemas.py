"""Dynamic agent output schemas.

Agent outputs are OpenAI strict structured-output schemas: every field
required, enums as `Literal`, no free-form dicts. ID fields are `Literal`s over the slugs
that exist right now (`RunEnums`, read from the DB per run), so the model cannot invent one.
Later tasks add their models to `Models` / `make_models` in this module.
"""

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, Competitor, Kit
from app.models.enums import SignalType, Stance


@dataclass(frozen=True)
class RunEnums:
    competitor_slugs: tuple[str, ...]
    capability_slugs: tuple[str, ...]
    kit_slugs: tuple[str, ...]
    ambiguous_aliases: tuple[str, ...] = ()  # lowercased, all competitors


@dataclass(frozen=True)
class Models:
    TriageOut: type[BaseModel]


async def build_enums(session: AsyncSession) -> RunEnums:
    async def slugs(col: Any, *where: Any) -> tuple[str, ...]:
        return tuple((await session.execute(select(col).where(*where).order_by(col))).scalars())

    return RunEnums(
        # JFrog itself is never a signal's company, so no agent can pick its slug.
        competitor_slugs=await slugs(Competitor.slug, Competitor.is_self.is_(False)),
        capability_slugs=await slugs(Capability.slug),
        kit_slugs=await slugs(Kit.slug),
        ambiguous_aliases=await _ambiguous_aliases(session),
    )


def slug_type(slugs: tuple[str, ...]) -> Any:
    """`Literal` over the slugs (an empty enum falls back to plain `str`)."""
    return Literal[slugs] if slugs else str  # type: ignore[valid-type]


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


@lru_cache(maxsize=16)
def make_models(enums: RunEnums) -> Models:
    """Build the per-run models; cached so equal enums give the same class objects."""
    triage = create_model(
        "TriageOut",
        __config__=ConfigDict(extra="forbid"),
        __validators__={"_clamp": field_validator("confidence")(_clamp01)},
        relevant=(bool, ...),
        # never "jfrog"; the Scope enum and DB CHECK keep it for legacy rows only.
        scope=(Literal["competitor", "industry"], ...),
        competitor_slugs=(list[slug_type(enums.competitor_slugs)], ...),
        signal_type=(SignalType, ...),
        confidence=(float, ...),
        reason=(str, ...),
    )
    return Models(TriageOut=triage)


async def _ambiguous_aliases(session: AsyncSession) -> tuple[str, ...]:
    rows = (await session.execute(select(Competitor.ambiguous_aliases))).scalars().all()
    return tuple(sorted({a.lower() for aliases in rows for a in (aliases or [])}))


_STRICT = ConfigDict(extra="forbid")
Score = Literal[1, 2, 3, 4, 5]


@lru_cache(maxsize=64)
def make_extracted_signal_model(enums: RunEnums, doc_ids: tuple[int, ...]) -> type[BaseModel]:
    """The analyst's output schema for ONE signal.

    `DocId` is a `Literal` over the member documents shown to the model, so a quote cannot
    cite another document. Word caps live in the prompt only; nothing is truncated here.
    """
    doc_id = Literal[doc_ids] if doc_ids else int  # type: ignore[valid-type]
    role_actions = create_model(
        "RoleActions",
        __config__=_STRICT,
        sales=(str | None, ...),
        pm=(str | None, ...),
        exec=(str | None, ...),
        pmm_devrel=(str | None, ...),
    )
    quote = create_model(
        "ExtractedQuote", __config__=_STRICT, doc_id=(doc_id, ...), quote=(str, ...)
    )
    return create_model(
        "ExtractedSignal",
        __config__=_STRICT,
        title=(str, ...),
        fact=(str, ...),
        event_date=(date | None, ...),
        signal_type=(SignalType, ...),
        stance=(Stance, ...),
        capability_slugs=(list[slug_type(enums.capability_slugs)], ...),
        kit_slugs=(list[slug_type(enums.kit_slugs)], ...),
        impact=(str, ...),
        impact_score=(Score, ...),
        novelty_score=(Score, ...),
        role_actions=(role_actions, ...),
        quotes=(list[quote], ...),
        hedged=(bool, ...),
    )
