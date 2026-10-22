"""Morning Briefing content: the persisted shape and the per-run strict agent schemas.

`BriefingContent` (signal ids as plain ints) is what `briefings.content` stores and the
API serves. Every newsroom agent exits through a per-run strict schema
whose signal id fields are `Literal`s over the signals selected for this briefing, so a
model cannot cite a signal it was not shown. No free-form dicts: the editor's theme
assignment is a list of {signal_id, theme} pairs and fact-check verdicts point at
enumerated sentence ids. `quiet_competitors` is computed in code, never by a model.

Card text: a takeaway's summary, why_it_matters, products and actions,
and a section item's summary and why_it_matters, are copied by the newsroom from the
signal's verified stage-B analysis (or its stage-A fact, impact and role actions when it has
none). The newsroom's models write only the headline, the lead and the takeaway titles, and
choose themes and order. A role edition's takeaways are chosen in code, and its cards say
why each story matters from the role's own section of the analysis (`angle`). The schema
is additive: `so_what` / `now_what` are kept, with "" defaults, so briefings filed before
the change still validate and render; new briefings leave them "" (and older rows read
`angle` "jfrog"). Defaults are listed as required in the response schema, since the API
always sends them.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.agents.analysis_schema import Effect, Level

# news about JFrog itself is never shown. The persisted `Theme` still accepts the legacy
# value so briefings filed earlier validate (they are redacted at read time); `EDITOR_THEMES`
# is what the newsroom's LLM schemas offer and `THEMES` is the display / section order.
LEGACY_SELF_THEME = "JFrog in the news"
EDITOR_THEMES = Literal[
    "Competitive moves",
    "Product & releases",
    "Market & money",
    "Industry pulse",
    "Community pulse",
]
Theme = Literal[
    "Competitive moves",
    "Product & releases",
    "Market & money",
    "Industry pulse",
    "JFrog in the news",
    "Community pulse",
]
THEMES: list[str] = list(get_args(EDITOR_THEMES))
# the theme of a selected signal the editor left unthemed, from its signal_type (the
# editor prompt's own theme definitions); every SignalType value is listed.
DEFAULT_THEME = "Industry pulse"
SIGNAL_TYPE_THEME: dict[str, str] = {
    "product_launch": "Product & releases",
    "feature_update": "Product & releases",
    "deprecation": "Product & releases",
    "pricing_change": "Competitive moves",
    "messaging_change": "Competitive moves",
    "partnership": "Competitive moves",
    "acquisition": "Competitive moves",
    "customer_win_loss": "Competitive moves",
    "funding": "Market & money",
    "earnings_financials": "Market & money",
    "leadership_change": "Market & money",
    "hiring_trend": "Market & money",
    "analyst_recognition": "Market & money",
    "security_incident": "Industry pulse",
    "legal_regulatory": "Industry pulse",
    "industry_trend": "Industry pulse",
    "standard_spec": "Industry pulse",
    "community_buzz": "Community pulse",
    "event_conference": "Community pulse",
    "other": DEFAULT_THEME,
}


def theme_for(signal_type: str | None) -> str:
    return SIGNAL_TYPE_THEME.get(signal_type or "", DEFAULT_THEME)


Confidence = Literal["high", "medium", "low"]
ActionRole = Literal["sales", "pm", "exec", "pmm_devrel"]
# Where a card's why_it_matters comes from: the JFrog product impact, or an edition role's
# own section of the analysis.
Angle = Literal["jfrog", "sales", "pm", "exec", "pmm_devrel"]

EDITION_ROLES: tuple[str, ...] = get_args(ActionRole)

# Mirrors frontend/src/lib/roles.ts: keep both in step.
ROLE_LABEL: dict[str, str] = {
    "analyst": "CI analyst",
    "sales": "Sales",
    "pm": "Product",
    "exec": "Executive",
    "pmm_devrel": "PMM & DevRel",
}
# The label of a card's why_it_matters line, by its `angle` (the app shows the same copy).
ANGLE_LABEL: dict[str, str] = {
    "jfrog": "Why it matters for JFrog",
    "sales": "In your deals",
    "pm": "Product angle",
    "exec": "Strategic angle",
    "pmm_devrel": "Messaging angle",
}
ROLE_FOCUS: dict[str, str] = {
    "analyst": "Review queue, sources and pipeline health",
    "sales": "Competitor moves to know before the next call",
    "pm": "Competitor releases and capability gaps",
    "exec": "Strategic shifts, funding and M&A",
    "pmm_devrel": "Messaging, launches and community buzz",
}

_STRICT = ConfigDict(extra="forbid")
# the persisted shape strips leading/trailing whitespace from every string, so text an
# LLM wrote (" UBS target…") is clean wherever a BriefingContent is built: the newsroom's
# result, a checkpointed result on resume, the persisted row and the email.
_PERSISTED = ConfigDict(
    extra="forbid", str_strip_whitespace=True, json_schema_serialization_defaults_required=True
)


# ---- persisted shape (briefings.content) -----------------------------------------------

_FROM_ANALYSIS = (
    "True when the body is copied from the signal's current stage-B analysis; false when it "
    "is the stage-A fact and impact (mark it) and on briefings filed before."
)


class ProductImpactRef(BaseModel):
    """A JFrog product the story affects, as the analysis's jfrog_impact rates it."""

    model_config = _PERSISTED

    product: str
    effect: Effect
    severity: Level


class RoleAction(BaseModel):
    """One recommended action, copied from the analysis (or stage A), with its role."""

    model_config = _PERSISTED

    role: ActionRole
    text: str


class Takeaway(BaseModel):
    model_config = _PERSISTED

    title: str
    so_what: str = Field(
        "",
        description='Legacy body of briefings filed before `summary` existed; "" on newer '
        "ones -- show it only when `summary` is empty.",
    )
    now_what: str = Field(
        "",
        description='Legacy action of older briefings; may be "" -- show it (as one action) '
        "only when `actions` is empty, and hide the line when empty.",
    )
    signal_ids: list[int]
    confidence: Confidence
    summary: str = Field("", description="The card body: the analysis summary, verbatim.")
    why_it_matters: str = Field(
        "",
        description='Why it matters, from the source `angle` names; may be "" -- hide the '
        "line when empty.",
    )
    products: list[ProductImpactRef] = Field(
        default_factory=list, description="The JFrog products the story affects."
    )
    actions: list[RoleAction] = Field(
        default_factory=list,
        description="Up to 3: the edition role's own actions (less the one `why_it_matters` "
        "shows), or on the master the first action of each role that has one.",
    )
    from_analysis: bool = Field(False, description=_FROM_ANALYSIS)
    angle: Angle = Field(
        "jfrog",
        description="Where `why_it_matters` comes from, and its label: jfrog = the JFrog "
        'product impact ("Why it matters for JFrog": the master, older briefings, and an '
        "edition card whose analysis has no line for the role); sales = the first objection "
        'or talk-track line ("In your deals"); pm = the first assessment ("Product angle"); '
        'exec = the first implication ("Strategic angle"); pmm_devrel = the first messaging '
        'line ("Messaging angle"). An edition card without an analysis shows its stage-A line '
        "for the role, under that role's label.",
    )
    risk_level: Level | None = Field(
        None,
        description="Exec edition cards: the analysis's risk level; null hides the chip "
        '("Risk: high").',
    )


class SectionItem(BaseModel):
    model_config = _PERSISTED

    signal_id: int
    one_liner: str
    why_it_matters: str
    summary: str = Field(
        "", description='The analysis summary (rows show its first sentence); "" on older ones.'
    )
    from_analysis: bool = Field(False, description=_FROM_ANALYSIS)


class Section(BaseModel):
    model_config = _PERSISTED

    theme: Theme
    items: list[SectionItem]


class WatchItem(BaseModel):
    model_config = _PERSISTED

    signal_id: int
    note: str


class BriefingContent(BaseModel):
    model_config = _PERSISTED

    headline: str
    lead: str
    top_takeaways: list[Takeaway]
    sections: list[Section]
    quiet_competitors: list[str]  # slugs of tier-1 competitors with no signal in the window
    watchlist: list[WatchItem]


def content_signal_ids(content: BriefingContent) -> list[int]:
    """Every signal a briefing shows (items, takeaways, watchlist), sorted."""
    ids = {i.signal_id for s in content.sections for i in s.items}
    ids |= {sid for t in content.top_takeaways for sid in t.signal_ids}
    ids |= {w.signal_id for w in content.watchlist}
    return sorted(ids)


# ---- per-run agent schemas -------------------------------------------------------------


@dataclass(frozen=True)
class BriefingModels:
    signal_ids: tuple[int, ...]
    BriefingContent: type[BaseModel]  # final-content guard: ids must be selected ones
    EditorPlan: type[BaseModel]

    def role_edition(self, chosen: tuple[int, ...]) -> type[BaseModel]:
        """A role adapter's schema: a title for each of its edition's code-chosen takeaways."""
        return make_role_edition_model(self.signal_ids, chosen)


def _id_type(ids: tuple[int, ...]) -> Any:
    if not ids:
        raise ValueError("a briefing schema needs at least one signal id")
    return Literal[ids]  # type: ignore[valid-type]


def _strict(name: str, **fields: Any) -> type[BaseModel]:
    return create_model(name, __config__=_STRICT, **{k: (t, ...) for k, t in fields.items()})


@lru_cache(maxsize=32)
def make_briefing_models(signal_ids: tuple[int, ...]) -> BriefingModels:
    """Schemas over the selected signal ids; cached so equal ids give equal classes.

    The editor plans (headline, lead, takeaway titles, theme assignment, watchlist ids) and
    a role adapter (`role_edition`) writes its edition's front and picks its items: neither
    writes card text. A model takeaway rests on exactly ONE signal id; the persisted
    `signal_ids` stays a list (one element on new rows) so older briefings still validate.
    """
    sid = _id_type(signal_ids)
    product = _strict("ProductImpactRef", product=str, effect=Effect, severity=Level)
    action = _strict("RoleAction", role=ActionRole, text=str)
    takeaway = _strict(
        "Takeaway",
        title=str,
        so_what=str,
        now_what=str,
        signal_ids=list[sid],
        confidence=Confidence,
        summary=str,
        why_it_matters=str,
        products=list[product],
        actions=list[action],
        from_analysis=bool,
        angle=Angle,
        risk_level=Level | None,
    )
    item = _strict(
        "SectionItem",
        signal_id=sid,
        one_liner=str,
        why_it_matters=str,
        summary=str,
        from_analysis=bool,
    )
    section = _strict("Section", theme=EDITOR_THEMES, items=list[item])
    watch = _strict("WatchItem", signal_id=sid, note=str)
    content = _strict(
        "BriefingContent",
        headline=str,
        lead=str,
        top_takeaways=list[takeaway],
        sections=list[section],
        quiet_competitors=list[str],
        watchlist=list[watch],
    )
    editor = _strict(
        "EditorPlan",
        headline=str,
        lead=str,
        top_takeaways=list[
            _strict("TakeawayPlan", signal_id=sid, title=str, confidence=Confidence)
        ],
        theme_assignment=list[_strict("ThemeAssignment", signal_id=sid, theme=EDITOR_THEMES)],
        watchlist=list[_strict("WatchPick", signal_id=sid)],
    )
    return BriefingModels(signal_ids, content, editor)


@lru_cache(maxsize=128)
def make_role_edition_model(
    signal_ids: tuple[int, ...], chosen: tuple[int, ...]
) -> type[BaseModel]:
    """A role adapter's output: its edition's headline and lead, titles for the takeaways
    code chose for it (`chosen`, a `Literal`: it cannot put a takeaway on any other story) and the
    master signals it shows as items, in order (`pick`). No count keywords (strict-mode support for
    minItems/maxItems is unconfirmed and a rejection would omit every edition): the newsroom
    enforces one title per chosen story in code. With
    nothing chosen, `top_takeaways` is not offered at all."""
    fields: dict[str, Any] = {"headline": str, "lead": str}
    if chosen:
        card = _strict("EditionTakeaway", signal_id=_id_type(chosen), title=str)
        fields["top_takeaways"] = list[card]
    fields["pick"] = list[_id_type(signal_ids)]  # the master signals shown as items, in order
    return _strict("RoleEdition", **fields)


SentenceKind = Literal["claim", "advice"]


@lru_cache(maxsize=128)
def make_fact_check_model(n_sentences: int, *, reason_first: bool = False) -> type[BaseModel]:
    """Verdicts over enumerated sentence ids S1..Sn (no echo-matching of text).

    `kind`: advice (a recommendation, question or talk track) is not a claim; whether
    advice may pass unsupported is the caller's rule (the newsroom's front never does).
    `reason_first` puts `reason` before `kind` and `supported`: a model writes the fields
    in schema order, so its verdict then follows its reasoning instead of preceding it.
    """
    if n_sentences < 1:
        raise ValueError("nothing to fact-check")
    sentence_id = Literal[tuple(f"S{i}" for i in range(1, n_sentences + 1))]  # type: ignore[valid-type]
    fields: dict[str, Any] = {"sentence_id": (sentence_id, ...)}
    if reason_first:
        fields["reason"] = (str, ...)
    fields |= {"kind": (SentenceKind, ...), "supported": (bool, ...)}
    if not reason_first:
        fields["reason"] = (str, ...)
    verdict = create_model("Verdict", __config__=_STRICT, **fields)
    return create_model("FactCheckOut", __config__=_STRICT, verdicts=(list[verdict], ...))
