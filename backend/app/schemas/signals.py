import datetime as dt
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.analysis_schema import Actions, Effect, Level, RiskKind

_ROLE_ACTION = 'What this role should do; null when never written and may be "" -- hide if empty.'


class RoleActions(BaseModel):
    """Per-role action lines from the analyst agent. Any value may be null or ""."""

    # Stored as JSONB ({} before analysis): missing keys read as null, and every key is
    # still listed as present in the response schema.
    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)

    sales: str | None = Field(None, description=_ROLE_ACTION)
    pm: str | None = Field(None, description=_ROLE_ACTION)
    exec: str | None = Field(None, description=_ROLE_ACTION)
    pmm_devrel: str | None = Field(None, description=_ROLE_ACTION)


AnalysisState = Literal["current", "stale", "pending", "thin", "below_threshold"]
_ANALYSIS_STATE = (
    'The full analysis, with its reason when there is none: "current"; "stale" '
    '(the story has new coverage since; still shown in full); "pending" (eligible, not '
    'written yet); "thin" (headline-only or too-short evidence: none will be written); '
    '"below_threshold" (importance under the stage-B floor: none will be written).'
)


class SignalCard(BaseModel):
    id: int
    title: str
    scope: str
    status: str
    signal_type: str | None
    stance: str | None
    importance: int
    reliability: str | None = Field(description="Admiralty source reliability, A (best) to F.")
    credibility: int | None = Field(description="Admiralty information credibility, 1 (best) to 6.")
    grade: str | None = Field(description='reliability + credibility, e.g. "B2"; null if unscored.')
    competitors: list[str] = Field(description="Competitor slugs.")
    fact: str | None
    impact: str | None
    role_actions: RoleActions
    first_seen_at: datetime
    primary_url: str | None = Field(description="Original URL of the primary source document.")
    primary_source: str | None = Field(description="Name of the primary document's source.")
    summary: str | None = Field(
        description="The latest analysis summary (current or stale); null when none."
    )
    analysis_state: AnalysisState = Field(description=_ANALYSIS_STATE)


class SignalPage(BaseModel):
    items: list[SignalCard]
    next_cursor: str | None = Field(description="Opaque keyset cursor; always null with q.")
    search_mode: Literal["hybrid", "keyword"] | None = Field(
        description='How q was searched: "keyword" when no embedding was available (no OpenAI '
        "key, or embedding failed / over budget); null without q."
    )


class EvidenceOut(BaseModel):
    quote: str
    verified: bool
    document_id: int
    url: str
    source_name: str


class SignalDocumentOut(BaseModel):
    id: int
    title: str | None
    url: str
    source_name: str
    published_at: datetime | None
    is_primary: bool


class CapabilityRef(BaseModel):
    slug: str
    name: str


class KitRef(BaseModel):
    slug: str
    title: str


class CitedSourceOut(BaseModel):
    n: int = Field(description="1-based source number; analysis `cites` refer to it.")
    kind: Literal["document", "kb", "signal"]
    ref: str = Field(description='"doc:<id>", "kb:<id>" or "signal:<id>".')
    title: str
    url: str | None = Field(description="Null for a cited signal the reader may not see.")
    outlet: str | None
    published_at: datetime | None
    is_primary: bool


class CitedTextOut(BaseModel):
    text: str
    cites: list[int] = Field(description="Source numbers (`sources[].n`).")


class ProductImpactOut(BaseModel):
    product: str
    effect: Effect
    severity: Level
    rationale: str
    cites: list[int]


class RiskOpportunityOut(BaseModel):
    kind: RiskKind
    text: str
    cites: list[int]


class WatchItemOut(BaseModel):
    text: str
    date: dt.date | None
    cites: list[int]


class SignalAnalysisOut(BaseModel):
    """`SignalAnalysisContent` with every `cites` turned into source numbers."""

    summary: CitedTextOut
    what_happened: list[CitedTextOut]
    background: list[CitedTextOut]
    what_changed: list[CitedTextOut]
    jfrog_impact: list[ProductImpactOut]
    competitive_implications: list[CitedTextOut]
    recommended_actions: Actions
    risks_opportunities: list[RiskOpportunityOut]
    what_to_watch: list[WatchItemOut]
    confidence_note: str


class SignalDetail(SignalCard):
    score_breakdown: dict[str, Any] = Field(
        description="Scoring components (value/weight/contribution), decay, age_days, "
        "evidence_depth ({thin, multiplier}: headline-only sources are scored x0.6) and "
        "importance, plus any verification/error notes."
    )
    evidence: list[EvidenceOut] = Field(description="Quotes, verified ones first.")
    documents: list[SignalDocumentOut] = Field(description="Member documents, primary first.")
    capabilities: list[CapabilityRef]
    kits: list[KitRef]
    related: list[SignalCard] = Field(description="Up to 5 most similar visible signals.")
    analysis: SignalAnalysisOut | None = Field(
        description="The latest parseable stage-B analysis, or null (fall back to fact/impact)."
    )
    sources: list[CitedSourceOut] = Field(
        description="Numbered sources: all member documents, then cited KB docs and signals."
    )
