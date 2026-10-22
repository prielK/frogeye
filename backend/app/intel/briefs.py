"""Weekly cited briefs: the "bottom line for JFrog" on a competitor,
and the competitive picture around a JFrog product. Each is one smart call over evidence
FrogEye already holds (app/intel/brief_inputs.py), verified like a stage-B analysis before
it is stored.

Competitor brief (`generate_competitor_brief`): `{bottom_line: 3-5 cited items,
product_edges: [{product, jfrog_product, edge}], watch: <= 3 cited items}`. `product` is a
Literal over the competitor's seeded product names and `jfrog_product` over JFROG_PRODUCTS;
a competitor with no seeded products gets no product_edges field in the schema and stores
[]. Refs: `signal:`, `fact:`, `posting:`, `page:`.

Product brief (`generate_product_brief`): `{bottom_line: 2-4 cited items, gaps_parity:
cited items, actions: {sales, pm, exec, pmm_devrel: [str]}}`. Refs: `signal:`.

Refs: every `cites` item is a Literal over exactly the refs shown, so the
strict schema rejects any other; refs are stored raw ("signal:12") and the API numbers
them.

Verification, as stage B (the shared helpers are in app/agents/verified_llm.py):
1. stray ref markers are stripped from the text and a cited item without cites is
   dropped; an edge whose JFrog product is not one of its product's seeded counterparts, or
   that repeats a pair, is dropped;
2. the number guard: a cited item's numbers must appear in ITS cited
   sources (an edge or a gaps_parity item, JFrog's side, may also use the JFrog brief); an
   action's in any provided source or the JFrog brief;
3. one fast fact-check (`brief_fact_checker`) numbers the survivors S1..Sn, each with a kind
   (claim | advice): a cited item is judged against its cited sources, an action against
   every source and the JFrog brief (advice passes, an unsupported claim is dropped).
   The checker is shown only the union of the sources its items cite (every source when an
   action is among them), the job-board summary only with a posting. Items left
   without a verdict are asked again once, each in a call of its own (the fast checker can
   answer a multi-item ask with one verdict), the same way; still missing fails the brief.
The JFrog brief every step reads is `jfrog_brief_context()`, without JFrog's company figures, and
both writers and the checker forbid them. Then the first BOTTOM_LINE_MAX (competitor) or
PRODUCT_BOTTOM_LINE_MAX (product) bottom-line, WATCH_MAX watch, GAPS_MAX gaps_parity and ACTIONS_MAX
per-role survivors are kept, in the model's order. An empty bottom line after
the checks, an LLM error or refusal (schema-invalid output included) or a failed
fact-check fails the brief: nothing is stored and the previous version stays current. BudgetExceeded
propagates.

Skips (no call, nothing stored, `skipped` says why): the self competitor; fewer than
`brief_min_signals` public signals in the lookback ("not enough evidence for a
brief yet"); no citable evidence; an `input_hash` equal to the latest version's (unchanged evidence
costs nothing; `ok` with that version; the hash carries a 4-week age bucket, so a brief is written
again at least every BRIEF_REFRESH_DAYS days); and a day whose remaining budget is under the
newsroom's reserve.

Storage: versioned rows in `competitor_briefs` / `product_briefs` (version = max + 1) with
the smart model, both prompts' versions ("1+check1") and the cost of the brief's own calls
from the ledger. Sessions are short: the inputs are read and closed before any call.
`generate_briefs` runs a batch one brief at a time; a budget stop ends it.
"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from functools import lru_cache, partial
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.analysis_schema import JFROG_PRODUCTS, CitedText
from app.agents.briefing_schema import make_fact_check_model
from app.agents.nodes.verify import unknown_numbers_in
from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import Prompt, load_prompt, untrusted
from app.agents.verified_llm import (
    IncompleteFactCheck,
    calls_cost,
    fact_check,
    ledger_floor,
    ref_cleaner,
    reserve_reached,
    unsupported,
)
from app.core.config import Settings
from app.core.days import local_day
from app.intel.brief_inputs import (
    BriefSource,
    competitor_inputs,
    jfrog_brief_context,
    job_board_view,
    plural,
    product_inputs,
)
from app.models import Competitor, CompetitorBrief, ProductBrief
from app.models.enums import StepKind

logger = logging.getLogger(__name__)

COMPETITOR_TASK = "competitor_brief"  # smart tier; also the prompt name
PRODUCT_TASK = "product_brief"  # smart tier; also the prompt name
CHECK_TASK = "brief_fact_check"  # fast tier
CHECK_PROMPT = "brief_fact_checker"

BOTTOM_LINE_MAX = 5  # a competitor brief asks for 3 to 5 (competitor_brief.md)
PRODUCT_BOTTOM_LINE_MAX = 4  # a product brief asks for 2 to 4 (product_brief.md)
WATCH_MAX = 3
GAPS_MAX = 5
ACTIONS_MAX = 4
BRIEF_REFRESH_DAYS = 28  # an unchanged brief is still written again every 4 weeks
ITEMS_CHARS = 60_000
ERROR_CHARS = 300

ROLES = ("sales", "pm", "exec", "pmm_devrel")
_ROLE_LABELS = {"sales": "sales", "pm": "PM", "exec": "exec", "pmm_devrel": "PMM/DevRel"}
UNCHANGED = "unchanged"  # BriefOutcome.skipped prefixes generate_briefs counts apart
RESERVED = "reserved"
# The ref cleaner for the brief refs (doc: and kb: too, should a model echo one).
_clean = ref_cleaner(("signal", "fact", "posting", "page", "doc", "kb"))


class UnknownBriefTarget(ValueError):
    """A competitor slug or JFrog product name that does not exist."""


# ---- persisted content (what the brief tables store and readers load) ------------------

_READER = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)
_STRICT = ConfigDict(extra="forbid")


class ProductEdge(BaseModel):
    model_config = _READER
    product: str = ""  # the competitor's seeded product
    jfrog_product: str = ""  # one of its JFrog counterparts
    edge: CitedText = Field(default_factory=CitedText)


class CompetitorBriefContent(BaseModel):
    """`competitor_briefs.content`. Tolerant like SignalAnalysisContent: rows outlive prompt
    versions, so missing keys take defaults and unknown keys are ignored."""

    model_config = _READER
    bottom_line: list[CitedText] = Field(default_factory=list)
    product_edges: list[ProductEdge] = Field(default_factory=list)
    watch: list[CitedText] = Field(default_factory=list)
    evidence: dict[str, int] = Field(default_factory=dict)  # sources shown, by ref kind
    input_hash: str = ""


class BriefActions(BaseModel):
    model_config = _READER
    sales: list[str] = Field(default_factory=list)
    pm: list[str] = Field(default_factory=list)
    exec: list[str] = Field(default_factory=list)
    pmm_devrel: list[str] = Field(default_factory=list)


class ProductBriefContent(BaseModel):
    """`product_briefs.content` (tolerant, as CompetitorBriefContent)."""

    model_config = _READER
    bottom_line: list[CitedText] = Field(default_factory=list)
    gaps_parity: list[CitedText] = Field(default_factory=list)
    actions: BriefActions = Field(default_factory=BriefActions)
    evidence: dict[str, int] = Field(default_factory=dict)
    input_hash: str = ""


def competitor_brief_content(row: CompetitorBrief | None) -> CompetitorBriefContent | None:
    """The row's content when it parses and has a bottom line, else None (a row from another
    prompt version that no longer parses is no brief, never an error)."""
    if row is None:
        return None
    try:
        content = CompetitorBriefContent.model_validate(row.content)
    except ValidationError:
        logger.warning("competitor brief %s v%s does not parse", row.id, row.version)
        return None
    return content if _has_text(content.bottom_line) else None


def product_brief_content(row: ProductBrief | None) -> ProductBriefContent | None:
    """As `competitor_brief_content`, for a product brief."""
    if row is None:
        return None
    try:
        content = ProductBriefContent.model_validate(row.content)
    except ValidationError:
        logger.warning("product brief %s v%s does not parse", row.id, row.version)
        return None
    return content if _has_text(content.bottom_line) else None


def _has_text(items: Iterable[CitedText]) -> bool:
    return any(item.text.strip() for item in items)


async def latest_competitor_brief(
    session: AsyncSession, competitor_id: int
) -> CompetitorBrief | None:
    """The current (highest-version) brief of a competitor."""
    return await session.scalar(
        select(CompetitorBrief)
        .where(CompetitorBrief.competitor_id == competitor_id)
        .order_by(CompetitorBrief.version.desc())
        .limit(1)
    )


async def latest_product_brief(session: AsyncSession, product: str) -> ProductBrief | None:
    return await session.scalar(
        select(ProductBrief)
        .where(ProductBrief.product == product)
        .order_by(ProductBrief.version.desc())
        .limit(1)
    )


# ---- strict LLM schemas -------------------------------------------------------------


def _strict(name: str, **fields: Any) -> type[BaseModel]:
    """Every field required, no extras."""
    required: dict[str, Any] = {k: (t, ...) for k, t in fields.items()}
    return create_model(name, __config__=_STRICT, **required)


def _cited(refs: tuple[str, ...]) -> type[BaseModel]:
    if not refs:  # never called without sources: a brief with none is skipped
        raise ValueError("a brief needs at least one citable source")
    return _strict("BriefText", text=str, cites=list[Literal[refs]])  # type: ignore[valid-type]


@lru_cache(maxsize=128)
def make_competitor_brief_model(
    refs: tuple[str, ...], products: tuple[str, ...]
) -> type[BaseModel]:
    """The competitor brief's schema: cites over `refs`, edges over the seeded `products`;
    without seeded products there is no product_edges field at all."""
    cited = _cited(refs)
    fields: dict[str, Any] = {"bottom_line": list[cited]}  # type: ignore[valid-type]
    if products:
        edge = _strict(
            "ProductEdge",
            product=Literal[products],  # type: ignore[valid-type]
            jfrog_product=Literal[JFROG_PRODUCTS],  # type: ignore[valid-type]
            edge=cited,
        )
        fields["product_edges"] = list[edge]  # type: ignore[valid-type]
    fields["watch"] = list[cited]  # type: ignore[valid-type]
    return _strict("CompetitorBriefOut", **fields)


@lru_cache(maxsize=64)
def make_product_brief_model(refs: tuple[str, ...]) -> type[BaseModel]:
    cited = _cited(refs)
    actions = _strict("BriefActions", **dict.fromkeys(ROLES, list[str]))
    return _strict(
        "ProductBriefOut",
        bottom_line=list[cited],  # type: ignore[valid-type]
        gaps_parity=list[cited],  # type: ignore[valid-type]
        actions=actions,
    )


# ---- outcomes -----------------------------------------------------------------------------


@dataclass(frozen=True)
class BriefOutcome:
    """ok: a current brief reflects the evidence (just written, or unchanged). A skip has
    no error and says why in `skipped`; a failure has the error."""

    ok: bool
    version: int | None
    removed: int
    error: str | None = None
    skipped: str | None = None


@dataclass
class BriefsRun:
    """What one batch did (the weekly task and the CLI share it)."""

    generated: int = 0
    unchanged: int = 0  # the evidence equals the latest version's
    skipped: int = 0  # JFrog itself, too few public signals, nothing citable
    failed: int = 0  # nothing stored; the previous version stays current
    reserved: int = 0  # not started: the rest of today's budget is the briefing's
    removed: int = 0  # items the checks dropped from the briefs written
    budget_stopped: bool = False
    failures: list[str] = field(default_factory=list)

    def add(self, label: str, outcome: BriefOutcome) -> None:
        if outcome.ok:
            if outcome.skipped:
                self.unchanged += 1
            else:
                self.generated += 1
                self.removed += outcome.removed
        elif outcome.error:
            self.failed += 1
            self.failures.append(f"{label}: {outcome.error}"[:ERROR_CHARS])
        elif (outcome.skipped or "").startswith(RESERVED):
            self.reserved += 1
        else:
            self.skipped += 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- verification -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Item:
    """A checked item: where it lives in the content, its text and its evidence. A cited
    item is judged against its cites; an action (no cites) against every source and may
    pass as advice."""

    section: str
    index: int
    label: str
    text: str
    cites: tuple[str, ...] = ()
    jfrog_side: bool = False  # its numbers may also come from the JFrog brief
    advice_ok: bool = False


def _clean_cited(item: dict[str, Any]) -> dict[str, Any]:
    return {"text": _clean(item["text"]), "cites": list(dict.fromkeys(item["cites"]))}


def _cited_items(
    data: dict[str, Any], section: str, label: str, *, jfrog_side: bool = False
) -> list[_Item]:
    return [
        _Item(section, i, label, p["text"], tuple(p["cites"]), jfrog_side=jfrog_side)
        for i, p in enumerate(data[section])
    ]


def _competitor_items(data: dict[str, Any]) -> list[_Item]:
    """Every item in display order (the fact-check numbering)."""
    items = _cited_items(data, "bottom_line", "bottom line")
    items += [
        _Item(
            "product_edges",
            i,
            f"edge: {e['product']} vs JFrog {e['jfrog_product']}",
            e["edge"]["text"],
            tuple(e["edge"]["cites"]),
            jfrog_side=True,
        )
        for i, e in enumerate(data["product_edges"])
    ]
    return items + _cited_items(data, "watch", "what to watch")


def _product_items(data: dict[str, Any]) -> list[_Item]:
    items = _cited_items(data, "bottom_line", "bottom line")
    items += _cited_items(data, "gaps_parity", "gap or parity", jfrog_side=True)
    for role in ROLES:
        items += [
            _Item(f"actions.{role}", i, f"action: {_ROLE_LABELS[role]}", t, advice_ok=True)
            for i, t in enumerate(data["actions"][role])
        ]
    return items


class _Checker:
    """Deterministic checks of one brief against the sources it was shown."""

    def __init__(self, sources: dict[str, BriefSource], brief: str, posting_note: str) -> None:
        self.sources = sources
        self.brief = brief
        self.posting_note = posting_note

    def corpus(self, refs: Iterable[str]) -> list[str]:
        refs = list(refs)
        out = [self.sources[r].body for r in refs if r in self.sources]
        if self.posting_note and any(r.startswith("posting:") for r in refs):
            out.append(self.posting_note)  # the job-board counts go with posting refs
        return out

    def published(self, refs: Iterable[str]) -> list[datetime]:
        dates = (self.sources[r].published for r in refs if r in self.sources)
        return [d for d in dates if d is not None]

    def ok(self, item: _Item) -> bool:
        if not item.text:
            return False
        if item.advice_ok:  # advice needs no cites; its numbers are still checked
            refs: Iterable[str] = self.sources
            corpus = [*self.corpus(refs), self.brief]
        elif not item.cites:
            return False
        else:
            refs = item.cites
            corpus = self.corpus(refs) + ([self.brief] if item.jfrog_side else [])
        return not unknown_numbers_in([item.text], corpus, self.published(refs))


Dropped = set[tuple[str, int | None]]


def _drop(data: dict[str, Any], dropped: Dropped) -> dict[str, Any]:
    """The content without the dropped list entries (actions per role included)."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == "actions":
            out[key] = {
                role: [t for i, t in enumerate(texts) if (f"actions.{role}", i) not in dropped]
                for role, texts in value.items()
            }
        elif isinstance(value, list):
            out[key] = [v for i, v in enumerate(value) if (key, i) not in dropped]
        else:
            out[key] = value
    return out


def _edge_drops(
    data: dict[str, Any], dropped: Dropped, product_map: dict[str, tuple[str, ...]]
) -> Dropped:
    """Edges pairing a product with a JFrog product its seed does not list, or repeating a
    pair already kept (one edge per pair)."""
    out: Dropped = set()
    seen: set[tuple[str, str]] = set()
    for i, e in enumerate(data["product_edges"]):
        if ("product_edges", i) in dropped:
            continue
        pair = (e["product"], e["jfrog_product"])
        if e["jfrog_product"] not in product_map.get(e["product"], ()) or pair in seen:
            out.add(("product_edges", i))
        seen.add(pair)
    return out


def _items_block(items: list[_Item]) -> str:
    lines = (
        f"S{n} ({it.label}; "
        + (f"cites {', '.join(it.cites)}" if it.cites else "evidence: all sources")
        + f"): {it.text}"
        for n, it in enumerate(items, 1)
    )
    return untrusted("items", "\n" + "\n".join(lines) + "\n", max_chars=ITEMS_CHARS)


def input_hash(
    prompt: Prompt,
    check: Prompt,
    settings: Settings,
    material: Any,
    *,
    context: str,
    today: date,
) -> str:
    """Everything a brief is written from: the evidence, both prompts (name, version
    and wording, so an unbumped edit counts too), both models and the JFrog brief. The date
    counts only by its BRIEF_REFRESH_DAYS bucket: the same evidence next week costs
    nothing, yet a quiet competitor's brief (and its "watch" items) is written again at
    least every four weeks."""
    blob = json.dumps(
        {
            "prompts": [[p.name, p.version, p.system, p.user] for p in (prompt, check)],
            "models": [settings.model_smart, settings.model_fast],
            "jfrog_context": hashlib.sha256(context.encode()).hexdigest(),
            "refresh": today.toordinal() // BRIEF_REFRESH_DAYS,
            "material": material,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


async def _store(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[CompetitorBrief] | type[ProductBrief],
    key: dict[str, Any],
    content: BaseModel,
    *,
    model_name: str,
    prompt_version: str,
    cost: float,
) -> int:
    """Write the next version (max + 1); UNIQUE(.., version) guards a concurrent writer."""
    [(column, value)] = key.items()
    async with session_factory() as session:
        current = await session.scalar(
            select(func.max(model.version)).where(getattr(model, column) == value)
        )
        version = (current or 0) + 1
        session.add(
            model(
                **key,
                version=version,
                content=content.model_dump(mode="json"),
                model=model_name,
                prompt_version=prompt_version,
                cost_usd=cost,
            )
        )
        await session.commit()
    return version


# ---- the two briefs -----------------------------------------------------------------------


async def _note(llm: AccountedLLM, ctx: RunCtx, label: str, summary: str) -> None:
    await llm.log_step(ctx, kind=StepKind.node, args={"brief": label}, result_summary=summary)


@dataclass(frozen=True)
class _Spec:
    """What differs between the two briefs; the pipeline in `_write` is shared."""

    label: str
    task: str
    sources: dict[str, BriefSource]
    posting_note: str
    schema: type[BaseModel]
    variables: dict[str, Any]
    items: Callable[[dict[str, Any]], list[_Item]]
    finish: Callable[[dict[str, Any]], BaseModel]  # trims and builds the persisted content
    store: Callable[[BaseModel, str, float], Awaitable[int]]
    product_map: dict[str, tuple[str, ...]] | None = None  # competitor briefs only


async def _write(
    spec: _Spec,
    *,
    llm: AccountedLLM,
    session_factory: async_sessionmaker[AsyncSession],
    prompt: Prompt,
    ctx: RunCtx,
) -> BriefOutcome:
    """Call, check (deterministic, then the fact-check), store."""
    label = spec.label
    floor = await ledger_floor(session_factory)

    async def fail(error: str, removed: int = 0) -> BriefOutcome:
        logger.warning("%s brief: %s", label, error)
        await _note(llm, ctx, label, f"error: {error}"[:ERROR_CHARS])
        return BriefOutcome(False, None, removed, error[:ERROR_CHARS])

    brief = spec.variables["jfrog_context"]
    keys = [llm.request_key("smart", spec.schema, prompt=prompt, variables=spec.variables)]
    try:
        out = await llm.structured(
            spec.task, "smart", spec.schema, prompt=prompt, variables=spec.variables, ctx=ctx
        )
    except (LLMRefusal, LLMError) as exc:  # includes a citation outside the shown refs
        return await fail(f"{type(exc).__name__}: {exc}")

    data = out.model_dump(mode="json")
    if spec.product_map is not None:  # a competitor brief; no seeded products, no field
        data.setdefault("product_edges", [])
    data = _normalised(data)
    checker = _Checker(spec.sources, brief, spec.posting_note)
    dropped: Dropped = {(it.section, it.index) for it in spec.items(data) if not checker.ok(it)}
    if spec.product_map is not None:
        dropped |= _edge_drops(data, dropped, spec.product_map)
    data = _drop(data, dropped)
    removed = len(dropped)
    if not data["bottom_line"]:
        return await fail("no bottom-line item passed the citation or number checks", removed)

    items = spec.items(data)
    check = load_prompt(CHECK_PROMPT)

    def request(pending: list[int]) -> tuple[type[BaseModel], dict[str, Any]]:
        """The checker reads only the sources the pending items cite (every source when
        one is an action, judged against all), the job-board summary only with a posting."""
        subset = [items[i] for i in pending]
        every = any(it.advice_ok for it in subset)
        cited = {r for it in subset for r in it.cites}
        shown = [s for ref, s in spec.sources.items() if every or ref in cited]
        note = spec.posting_note if any(s.ref.startswith("posting:") for s in shown) else ""
        variables = {
            "jfrog_context": brief,
            "sources": "\n\n".join(s.view() for s in shown),
            "posting_note": job_board_view(note) if note else "",
            "items": _items_block(subset),
        }
        return make_fact_check_model(len(subset)), variables

    try:
        verdicts = await fact_check(
            llm, len(items), request, task=CHECK_TASK, prompt=check, keys=keys, ctx=ctx, each=True
        )
    except (LLMRefusal, LLMError) as exc:  # fail closed: never store unchecked claims
        return await fail(f"fact-check failed: {type(exc).__name__}: {exc}", removed)
    except IncompleteFactCheck as exc:  # never store a half-checked brief
        return await fail(f"fact-check incomplete: {exc}", removed)
    failed_check = unsupported(items, verdicts)
    data = _drop(data, failed_check)
    removed += len(failed_check)
    if not data["bottom_line"]:
        return await fail("the fact-check supported no bottom-line item", removed)

    content = spec.finish(data)
    cost = await calls_cost(session_factory, floor, keys)  # this brief's own calls
    try:
        version = await spec.store(content, f"{prompt.version}+check{check.version}", cost)
    except IntegrityError as exc:  # the competitor vanished, or a concurrent writer won
        return await fail(f"not stored: {type(exc).__name__}", removed)
    await _note(llm, ctx, label, f"brief v{version}: {len(items)} items checked, {removed} removed")
    return BriefOutcome(True, version, removed)


def _normalised(data: dict[str, Any]) -> dict[str, Any]:
    """Every text without stray ref markers or blank runs, every cites list deduplicated."""
    out = dict(data)
    for key in ("bottom_line", "watch", "gaps_parity"):
        if key in out:
            out[key] = [_clean_cited(p) for p in out[key]]
    if "product_edges" in out:
        out["product_edges"] = [
            {**e, "edge": _clean_cited(e["edge"])} for e in out["product_edges"]
        ]
    if "actions" in out:
        out["actions"] = {role: [_clean(t) for t in out["actions"][role]] for role in ROLES}
    return out


async def _gate(
    llm: AccountedLLM,
    ctx: RunCtx,
    label: str,
    *,
    public_signals: int,
    sources: dict[str, BriefSource],
    input_hash: str,
    latest: CompetitorBrief | ProductBrief | None,
    settings: Settings,
) -> BriefOutcome | None:
    """The skips before any call (see the module docstring), or None to go ahead. Only an
    unchanged brief is `ok`: its current version still reflects the evidence."""
    days, needed = settings.brief_lookback_days, settings.brief_min_signals
    current: int | None = None
    if public_signals < needed:
        why = (
            f"{plural(public_signals, 'public signal')} in the last {days} days ({needed} "
            "needed): not enough evidence for a brief yet"
        )
    elif not sources:
        why = "no citable evidence: no current analysis, fact, posting or page change"
    elif latest is not None and (latest.content or {}).get("input_hash") == input_hash:
        why = f"{UNCHANGED}: the evidence is the same as version {latest.version}'s"
        current = latest.version
    elif await reserve_reached(llm, settings):
        why = f"{RESERVED}: the rest of today's budget is kept for the briefing (R159)"
    else:
        return None
    await _note(llm, ctx, label, f"skipped: {why}")
    return BriefOutcome(current is not None, current, 0, skipped=why)


async def generate_competitor_brief(
    competitor_id: int,
    *,
    llm: AccountedLLM,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    ctx: RunCtx,
    now: datetime | None = None,
) -> BriefOutcome:
    """Write, verify and store one competitor's weekly brief (see the module docstring).
    Raises BudgetExceeded."""
    now = now or datetime.now(UTC)
    async with session_factory() as session:  # read and closed before any call
        comp = await session.get(Competitor, competitor_id)
        if comp is None or comp.is_self:
            why = "unknown competitor" if comp is None else "JFrog itself never gets a brief (U5)"
            await _note(llm, ctx, f"competitor {competitor_id}", f"skipped: {why}")
            return BriefOutcome(False, None, 0, skipped=why)
        inputs = await competitor_inputs(session, comp, settings, now)
        latest = await latest_competitor_brief(session, competitor_id)

    label = f"competitor {inputs.slug}"
    today = local_day(now, settings.timezone)
    context = jfrog_brief_context()
    prompt = load_prompt(COMPETITOR_TASK)
    digest = input_hash(
        prompt,
        load_prompt(CHECK_PROMPT),
        settings,
        inputs.material(),
        context=context,
        today=today,
    )
    gate = await _gate(
        llm,
        ctx,
        label,
        public_signals=inputs.public_signals,
        sources=inputs.sources,
        input_hash=digest,
        latest=latest,
        settings=settings,
    )
    if gate is not None:
        return gate

    refs = tuple(inputs.sources)
    variables = {
        "jfrog_context": context,
        "today": today.isoformat(),
        "competitor": inputs.profile,
        "refs": ", ".join(refs),
        "products": inputs.products,
        "signals": [s.view() for s in inputs.signals],
        "facts": [s.view() for s in inputs.facts],
        "hiring": inputs.hiring_view(),
        "postings": [s.view() for s in inputs.postings],
        "pages": [s.view() for s in inputs.pages],
    }

    def finish(data: dict[str, Any]) -> BaseModel:
        return CompetitorBriefContent(
            bottom_line=data["bottom_line"][:BOTTOM_LINE_MAX],
            product_edges=data["product_edges"],
            watch=data["watch"][:WATCH_MAX],
            evidence=inputs.evidence(),
            input_hash=digest,
        )

    async def store(content: BaseModel, prompt_version: str, cost: float) -> int:
        return await _store(
            session_factory,
            CompetitorBrief,
            {"competitor_id": competitor_id},
            content,
            model_name=settings.model_smart,
            prompt_version=prompt_version,
            cost=cost,
        )

    spec = _Spec(
        label=label,
        task=COMPETITOR_TASK,
        sources=inputs.sources,
        posting_note=inputs.posting_note,
        schema=make_competitor_brief_model(refs, tuple(inputs.product_map)),
        variables=variables,
        items=_competitor_items,
        finish=finish,
        store=store,
        product_map=inputs.product_map,
    )
    return await _write(spec, llm=llm, session_factory=session_factory, prompt=prompt, ctx=ctx)


async def generate_product_brief(
    product: str,
    *,
    llm: AccountedLLM,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    ctx: RunCtx,
    now: datetime | None = None,
) -> BriefOutcome:
    """Write, verify and store one JFrog product's weekly brief. Raises UnknownBriefTarget
    for a name outside JFROG_PRODUCTS, and BudgetExceeded."""
    if product not in JFROG_PRODUCTS:
        raise UnknownBriefTarget(f"unknown JFrog product {product!r}")
    now = now or datetime.now(UTC)
    async with session_factory() as session:  # read and closed before any call
        inputs = await product_inputs(session, product, settings, now)
        latest = await latest_product_brief(session, product)

    label = f"product {product}"
    today = local_day(now, settings.timezone)
    context = jfrog_brief_context()
    prompt = load_prompt(PRODUCT_TASK)
    digest = input_hash(
        prompt,
        load_prompt(CHECK_PROMPT),
        settings,
        inputs.material(),
        context=context,
        today=today,
    )
    gate = await _gate(
        llm,
        ctx,
        label,
        public_signals=inputs.public_signals,
        sources=inputs.sources,
        input_hash=digest,
        latest=latest,
        settings=settings,
    )
    if gate is not None:
        return gate

    refs = tuple(inputs.sources)
    variables = {
        "jfrog_context": context,
        "today": today.isoformat(),
        "product": product,
        "refs": ", ".join(refs),
        "competitors": inputs.competitors,
        "signals": [s.view() for s in inputs.signals],
    }

    def finish(data: dict[str, Any]) -> BaseModel:
        return ProductBriefContent(
            bottom_line=data["bottom_line"][:PRODUCT_BOTTOM_LINE_MAX],
            gaps_parity=data["gaps_parity"][:GAPS_MAX],
            actions=BriefActions(**{r: data["actions"][r][:ACTIONS_MAX] for r in ROLES}),
            evidence=inputs.evidence(),
            input_hash=digest,
        )

    async def store(content: BaseModel, prompt_version: str, cost: float) -> int:
        return await _store(
            session_factory,
            ProductBrief,
            {"product": product},
            content,
            model_name=settings.model_smart,
            prompt_version=prompt_version,
            cost=cost,
        )

    spec = _Spec(
        label=label,
        task=PRODUCT_TASK,
        sources=inputs.sources,
        posting_note="",
        schema=make_product_brief_model(refs),
        variables=variables,
        items=_product_items,
        finish=finish,
        store=store,
    )
    return await _write(spec, llm=llm, session_factory=session_factory, prompt=prompt, ctx=ctx)


# ---- the weekly batch ---------------------------------------------------------------------


async def _competitor_targets(
    session_factory: async_sessionmaker[AsyncSession], slugs: Sequence[str] | None
) -> list[tuple[int, str]]:
    """(id, slug) to brief: every competitor but JFrog itself when `slugs` is None (by tier,
    then slug), else the named ones in the order given (JFrog itself is then skipped by its
    brief). An unknown slug raises before anything runs."""
    async with session_factory() as session:
        if slugs is None:
            rows = await session.execute(
                select(Competitor.id, Competitor.slug)
                .where(Competitor.is_self.is_(False))
                .order_by(Competitor.tier, Competitor.slug)
            )
            return [(cid, slug) for cid, slug in rows.all()]
        wanted = list(dict.fromkeys(slugs))
        found = dict(
            (
                await session.execute(
                    select(Competitor.slug, Competitor.id).where(Competitor.slug.in_(wanted))
                )
            ).all()
        )
    unknown = [s for s in wanted if s not in found]
    if unknown:
        raise UnknownBriefTarget(f"unknown competitor slug(s): {', '.join(unknown)}")
    return [(found[s], s) for s in wanted]


def _product_targets(products: Sequence[str] | None) -> list[str]:
    if products is None:
        return list(JFROG_PRODUCTS)
    wanted = list(dict.fromkeys(products))
    unknown = [p for p in wanted if p not in JFROG_PRODUCTS]
    if unknown:
        raise UnknownBriefTarget(f"unknown JFrog product(s): {', '.join(unknown)}")
    return wanted


async def generate_briefs(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    settings: Settings,
    ctx: RunCtx,
    *,
    competitors: list[str] | None = None,
    products: list[str] | None = None,
    now: datetime | None = None,
) -> BriefsRun:
    """The weekly batch: a brief for each named competitor (slugs) and JFrog product (names);
    None means every one (JFrog itself is never listed), [] none. Unknown names raise
    UnknownBriefTarget before any call. One brief at a time; a budget stop ends the batch
    and keeps what was written before it."""
    now = now or datetime.now(UTC)
    targets = await _competitor_targets(session_factory, competitors)
    names = _product_targets(products)
    common: dict[str, Any] = {
        "llm": llm,
        "session_factory": session_factory,
        "settings": settings,
        "ctx": ctx,
        "now": now,
    }
    jobs: list[tuple[str, Callable[[], Awaitable[BriefOutcome]]]] = [
        (f"competitor {slug}", partial(generate_competitor_brief, cid, **common))
        for cid, slug in targets
    ]
    jobs += [(f"product {name}", partial(generate_product_brief, name, **common)) for name in names]
    run = BriefsRun()
    for label, job in jobs:
        try:
            outcome = await job()
        except BudgetExceeded as exc:
            logger.warning("weekly briefs stopped at %s: the %s budget is spent", label, exc.scope)
            run.budget_stopped = True
            break
        run.add(label, outcome)
    return run
