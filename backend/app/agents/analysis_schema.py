"""Stage-B analysis: the strict LLM output schema and the persisted reader model.

Two shapes with the same fields:

* `make_analysis_model(refs)` is the strict schema handed to the LLM: every field is
  required, `X | None` marks the optional ones, `extra="forbid"` at EVERY nested model, enums
  are Literals, no defaults, no free dicts. Every `cites` item is a `Literal` over the source
  refs the model was shown (`"doc:<id>"`, `"kb:<id>"`, `"signal:<id>"`), so it cannot invent
  a citation.
* `SignalAnalysisContent` is what `signal_analyses.content` stores and readers load. Rows
  outlive prompt versions, so it is tolerant: every field has a default, unknown keys are
  ignored, and `cites` is a plain `list[str]` (a ref may later point at a deleted source).
"""

import datetime as dt
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

JFROG_PRODUCTS = (
    "Artifactory",
    "Xray",
    "Advanced Security",
    "Curation",
    "Distribution",
    "AppTrust",
    "Runtime",
    "JFrog ML",
    "AI Catalog",
    "MCP Registry",
    "Container Registry",
    "Connect",
    "Platform",
)

Effect = Literal["threat", "opportunity", "neutral"]
Level = Literal["high", "medium", "low"]
RiskKind = Literal["risk", "opportunity"]

_STRICT = ConfigDict(extra="forbid")
# the API always sends every field, so its schema lists them as required (the
# defaults only make old rows parse), as the briefing schemas do.
_READER = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)
_Product = Literal[JFROG_PRODUCTS]  # type: ignore[valid-type]


# ---- persisted reader model: tolerant of older and newer content ------------------------


class CitedText(BaseModel):
    model_config = _READER
    text: str = ""
    cites: list[str] = Field(default_factory=list)


class ProductImpact(BaseModel):
    model_config = _READER
    product: str = ""
    effect: Effect = "neutral"
    severity: Level = "low"
    rationale: str = ""
    cites: list[str] = Field(default_factory=list)


class Objection(BaseModel):
    model_config = _READER
    objection: str = ""
    response: str = ""


class SalesActions(BaseModel):
    model_config = _READER
    talk_track: list[str] = Field(default_factory=list)
    discovery_questions: list[str] = Field(default_factory=list)
    objections: list[Objection] = Field(default_factory=list)
    landmines: list[str] = Field(default_factory=list)


class PmActions(BaseModel):
    model_config = _READER
    assessment: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


class ExecActions(BaseModel):
    model_config = _READER
    implications: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    risk_level: Level | None = None


class PmmDevrelActions(BaseModel):
    model_config = _READER
    messaging: list[str] = Field(default_factory=list)
    content_ideas: list[str] = Field(default_factory=list)


class Actions(BaseModel):
    model_config = _READER
    sales: SalesActions = Field(default_factory=SalesActions)
    pm: PmActions = Field(default_factory=PmActions)
    exec: ExecActions = Field(default_factory=ExecActions)
    pmm_devrel: PmmDevrelActions = Field(default_factory=PmmDevrelActions)


class RiskOpportunity(BaseModel):
    model_config = _READER
    kind: RiskKind = "risk"
    text: str = ""
    cites: list[str] = Field(default_factory=list)


class WatchItem(BaseModel):
    model_config = _READER
    text: str = ""
    date: dt.date | None = None
    cites: list[str] = Field(default_factory=list)


class SignalAnalysisContent(BaseModel):
    """The persisted analysis. Missing keys take defaults; unknown keys are dropped."""

    model_config = _READER
    summary: CitedText = Field(default_factory=CitedText)
    what_happened: list[CitedText] = Field(default_factory=list)
    background: list[CitedText] = Field(default_factory=list)
    what_changed: list[CitedText] = Field(default_factory=list)
    jfrog_impact: list[ProductImpact] = Field(default_factory=list)
    competitive_implications: list[CitedText] = Field(default_factory=list)
    recommended_actions: Actions = Field(default_factory=Actions)
    risks_opportunities: list[RiskOpportunity] = Field(default_factory=list)
    what_to_watch: list[WatchItem] = Field(default_factory=list)
    confidence_note: str = ""


# ---- strict LLM schema ------------------------------------------------------------------


def _strict(name: str, **fields) -> type[BaseModel]:
    return create_model(name, __config__=_STRICT, **{k: (t, ...) for k, t in fields.items()})


@lru_cache(maxsize=64)
def make_analysis_model(refs: tuple[str, ...]) -> type[BaseModel]:
    """The strict output schema for ONE signal's analysis.

    `refs` are the source ids shown to the model. Each `cites` entry is a `Literal` over
    them. With NO refs (a call without sources is not expected) every `cites` list is capped
    at zero items rather than falling back to free strings, so the schema still builds and
    still cannot carry an invented citation.
    """
    if refs:
        cites = list[Literal[refs]]  # type: ignore[valid-type]
    else:
        cites = Annotated[list[str], Field(max_length=0)]
    cited = _strict("CitedText", text=str, cites=cites)
    impact = _strict(
        "ProductImpact",
        product=_Product,
        effect=Effect,
        severity=Level,
        rationale=str,
        cites=cites,
    )
    actions = _strict(
        "Actions",
        sales=_strict(
            "SalesActions",
            talk_track=list[str],
            discovery_questions=list[str],
            objections=list[_strict("Objection", objection=str, response=str)],
            landmines=list[str],
        ),
        pm=_strict("PmActions", assessment=list[str], follow_ups=list[str]),
        exec=_strict(
            "ExecActions",
            implications=list[str],
            decisions=list[str],
            risk_level=Level | None,
        ),
        pmm_devrel=_strict("PmmDevrelActions", messaging=list[str], content_ideas=list[str]),
    )
    return _strict(
        "SignalAnalysis",
        summary=cited,
        what_happened=list[cited],
        background=list[cited],
        what_changed=list[cited],
        jfrog_impact=list[impact],
        competitive_implications=list[cited],
        recommended_actions=actions,
        risks_opportunities=list[_strict("RiskOpportunity", kind=RiskKind, text=str, cites=cites)],
        what_to_watch=list[_strict("WatchItem", text=str, date=dt.date | None, cites=cites)],
        confidence_note=str,
    )
