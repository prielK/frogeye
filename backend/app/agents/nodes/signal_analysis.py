"""Stage-B signal analysis: one smart call writes a signal's full cited analysis, then
deterministic checks and one fact-check pass verify it before it is stored.

Inputs, every scraped or derived text inside `untrusted()`, each tagged with its ref:
- the stage-A record (fact, impact, scores, tags, grade) and its verified quotes (not
  citable: orientation only);
- up to 4 member documents (the analyst's `_load_members`: primary first, <= 1500 words
  each): `doc:<id>`;
- JFrog KB excerpts: the indexed chunks of the pages the signal's capabilities map
  to (CAPABILITY_KB_PAGES) closest to the signal's centroid, KB_CHUNKS at most, KB_PER_PAGE
  per page: `kb:<jfrog_kb_docs.id>`;
- the top PRIOR_K prior signals every reader can open (public statuses within the agent-visible
ones, never
  scope jfrog): `signal:<id>`;
- the JFrog context pack as the static, cacheable system prefix (trusted, not citable).
The refs tuple is exactly those ids, so the strict schema cannot carry another citation.

Verification (deterministic first, then one model pass, as in the newsroom):
1. stray ref markers are stripped from the text and a factual item without cites is dropped;
2. the number guard: a factual item's numbers must appear in ITS cited
   sources (a jfrog_impact rationale may also use the JFrog brief); an action's anywhere in
   the provided sources or the brief; a watch date no cited source names is nulled;
3. one smart-model fact-check numbers the survivors S1..Sn, each with a kind (claim |
   advice, the newsroom's shape) written after its reason: a factual item is judged against
   its own cited sources; an action and the confidence note against every provided source,
   the note also against the trusted evidence profile. An item passes when supported, an
   action also when it is advice; anything else, or an unjudged item, is dropped (a failed
   note is cleared).
An LLM error or refusal (schema-invalid output included), a failed fact-check call or a
dropped summary fails the analysis: nothing is stored and the signal keeps its stage-A
text. BudgetExceeded propagates.

Sessions are short: load and close, call, check, then persist and index in their own.
"""

import dataclasses
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.agents.tools  # noqa: F401  (registers the built-in tools)
from app.agents.analysis_schema import SignalAnalysisContent, make_analysis_model
from app.agents.analysis_store import needs_analysis, persist_analysis
from app.agents.briefing_schema import make_fact_check_model
from app.agents.nodes.analyst import DOC_CHARS, PRIOR_K, _load_members, _Members
from app.agents.nodes.score import analysis_blockers
from app.agents.nodes.verify import mentions_date, unknown_numbers_in
from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt, untrusted
from app.agents.runtime.types import Tier
from app.agents.schemas import RunEnums
from app.agents.tools.base import ToolDeps
from app.agents.tools.signals import SimilarSignalsArgs, get_similar_signals
from app.agents.verified_llm import (
    IncompleteFactCheck,
    calls_cost,
    fact_check,
    ledger_floor,
    ref_cleaner,
    unsupported,
)
from app.core.config import Settings
from app.models import Capability, Competitor, Kit
from app.models.enums import RetrievalKind, Scope, SignalStatus, StepKind
from app.models.gold import Evidence, Signal, SignalCapability, SignalCompetitor, SignalKit
from app.models.rag import JfrogKbDoc, RetrievalItem
from app.rag.indexing import embed_batched, index_signal
from app.rag.kb import kb_title, load_jfrog_context, load_kb_urls

logger = logging.getLogger(__name__)

TASK = "signal_analysis"  # smart tier; also the prompt name
CHECK_TASK = "analysis_fact_check"
# The checker runs on the smart tier, its verdict after its reason (evals/README.md,
# "Experiments": on the eval's planted errors the fast model removed 8 of 16, reason-first
# with prompt v4 12, the smart model 16, none of 14 true claims, for about $0.0005 more per
# analysis).
CHECK_TIER: Tier = "smart"
CHECK_PROMPT = "analysis_fact_checker"
EMBED_TASK = "analysis_embed"  # the signal vector when it has no centroid
# up to 6 KB chunks (<= 600 tokens each, so <= ~3.6k tokens) with at most 2 from one
# page. A story touches 1 to 3 JFrog products, so this gives each mapped product page its
# two passages closest to the story, and one long page cannot crowd out the others.
KB_CHUNKS = 6
KB_PER_PAGE = 2
SELECTABLE = (SignalStatus.auto, SignalStatus.approved)  # needs_review waits for approval
# Prior signals become citations readers follow, so only statuses every reader can open
# (the public statuses, inside the agent-visible ones) and never JFrog-self news.
PRIOR_STATUSES = (SignalStatus.auto, SignalStatus.approved)
PRIOR_POOL = 10  # nearest agent-visible candidates fetched before the reader filter
STAGE_A_CHARS = 4000
ITEMS_CHARS = 60_000  # the fact-check items block is never truncated in practice
# every inline citation marker a model writes despite the prompt is removed from the
# text before the number guard and before storing (citations live in `cites`); a bare ref
# used as a word ("as doc:12 reports") reads "the source" (app/agents/verified_llm.py).
_clean = ref_cleaner(("doc", "kb", "signal"))
_CITED_SECTIONS = ("what_happened", "background", "what_changed")
_STORY_SECTIONS = ("summary", "what_happened")  # must cite a member document
# background is earlier moves in the market (the story's documents or prior
# signals); JFrog's own capabilities belong in jfrog_impact, so a kb:-only item is dropped.
_BACKGROUND_REFS = ("doc:", "signal:")
_LATE_SECTIONS = ("competitive_implications", "risks_opportunities", "what_to_watch")
_ACTION_LISTS = {
    "sales": ("talk_track", "discovery_questions", "landmines"),
    "pm": ("assessment", "follow_ups"),
    "exec": ("implications", "decisions"),
    "pmm_devrel": ("messaging", "content_ideas"),
}
_ACTION_LABELS = {
    "sales.talk_track": "sales talk track",
    "sales.discovery_questions": "sales discovery question",
    "sales.landmines": "sales landmine question",
    "sales.objections": "sales objection and response",
    "pm.assessment": "PM assessment",
    "pm.follow_ups": "PM follow-up",
    "exec.implications": "exec implication",
    "exec.decisions": "exec decision",
    "pmm_devrel.messaging": "PMM/DevRel messaging",
    "pmm_devrel.content_ideas": "PMM/DevRel content idea",
}

_JFROG = "https://jfrog.com"
# Capability slug (app/seed/capabilities.yaml) -> the JFrog KB pages (app/seed/jfrog_kb.yaml)
# where JFrog's side of that capability is stated, most relevant first, as checked against
# the crawled page text (Connect is IoT device management, not toolchain integrations;
# malicious packages are Curation's). No capability->product map is stored anywhere else; a
# test keeps this one in step with both seed files.
CAPABILITY_KB_PAGES: dict[str, tuple[str, ...]] = {
    slug: tuple(f"{_JFROG}{path}" for path in paths)
    for slug, paths in {
        # artifact management
        "package-type-breadth": ("/artifactory/",),
        "remote-proxy-caching": ("/artifactory/",),
        "replication-federation": ("/artifactory/", "/solutions/global-enterprise-scale/"),
        "container-registry": ("/container-registry/", "/artifactory/"),
        "deployment-options": ("/platform/", "/solutions/global-enterprise-scale/"),
        "scale-ha": ("/solutions/global-enterprise-scale/", "/artifactory/"),
        # supply chain security
        "sca": ("/xray/",),
        "contextual-analysis": ("/advanced-security/",),
        "malicious-package-blocking": ("/curation/",),
        "secrets-detection": ("/advanced-security/",),
        "sast": ("/advanced-security/",),
        "iac-scanning": ("/advanced-security/",),
        "license-compliance": ("/xray/", "/curation/"),
        "sbom": ("/xray/", "/apptrust/"),
        "container-scanning": ("/xray/", "/container-registry/"),
        # runtime and images
        "runtime-security": ("/runtime/",),
        "hardened-images": ("/container-registry/", "/curation/"),
        "auto-remediation": ("/xray/", "/curation/"),  # Curation serves the next safe version
        # release and distribution
        "release-bundles": ("/distribution/", "/apptrust/"),
        "edge-distribution": ("/distribution/", "/connect/"),
        "signing-attestation": ("/apptrust/", "/distribution/"),
        # governance and compliance
        "policy-engine": ("/apptrust/", "/xray/", "/curation/"),
        "release-governance": ("/apptrust/",),
        "audit-evidence": ("/apptrust/",),
        "certifications": ("/platform/", "/solutions/global-enterprise-scale/"),
        # AI/ML supply chain
        "model-registry": ("/jfrog-ml/", "/ai-catalog/"),
        "model-scanning": ("/ai-catalog/", "/curation/"),
        "ai-catalog-mcp-registry": ("/ai-catalog/", "/ai-catalog/mcp-registry/"),
        "agent-governance": ("/ai-catalog/mcp-registry/", "/ai-catalog/"),
        "mlops-serving": ("/jfrog-ml/",),
        # developer experience
        "cicd-integrations": ("/artifactory/", "/platform/"),  # "100s of ecosystem integrations"
        "ide-cli": ("/xray/", "/platform/"),  # Xray: remediation "directly in your IDE/CLI"
        "ai-coding-agent-integrations": ("/ai-catalog/mcp-registry/", "/platform/"),
        "cloud-marketplaces": ("/platform/", "/pricing/"),
        # business model
        "pricing-transparency": ("/pricing/",),
        "free-tier": ("/pricing/",),
        "packaging": ("/pricing/", "/platform/"),
    }.items()
}


@dataclass
class DeepOutcome:
    signal_id: int
    ok: bool
    version: int | None
    removed: int
    error: str | None = None


# ---- selection --------------------------------------------------------------------------


async def select_for_deep_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    limit: int | None = None,
) -> list[int]:
    """Signals due a stage-B analysis, most important first, at most `limit` or
    `settings.deep_analysis_cap`: status auto/approved, scope not jfrog, analysed by stage A, not
    blocked by `analysis_blockers` (the rule the read side's analysis state shares: importance >=
    `analysis_min_importance`, and evidence with real text), and with no current, usable analysis.
    Ties go to the newer
    signal."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Signal.id, Signal.importance)
                .where(
                    Signal.status.in_(SELECTABLE),
                    Signal.scope != Scope.jfrog,
                    Signal.analyzed_at.is_not(None),
                    # the floor again here only to keep the scan small; the rule is below
                    Signal.importance >= settings.analysis_min_importance,
                )
                .order_by(Signal.importance.desc(), Signal.id.desc())
            )
        ).all()
        ids = [sid for sid, _ in rows]
        blocked = await analysis_blockers(session, dict(rows), settings.analysis_min_importance)
        due = await needs_analysis(session, ids)
    return [i for i in ids if i in due and i not in blocked][: limit or settings.deep_analysis_cap]


# ---- inputs -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Source:
    """One citable source: a trusted header, the untrusted body the model and the checker
    see (also the number-guard corpus), and the day a claim may name."""

    ref: str
    header: str
    body: str
    published: datetime | None = None

    def view(self) -> str:
        return f"{self.header}\n{untrusted(self.ref, self.body, max_chars=DOC_CHARS)}"


@dataclass(frozen=True)
class _Inputs:
    stage_a: str
    profile: str  # trusted evidence line (grade, corroboration, hedged): the note's evidence
    docs: list[_Source]
    kb: list[_Source]
    prior: list[_Source]

    @property
    def sources(self) -> dict[str, _Source]:
        return {s.ref: s for s in (*self.docs, *self.kb, *self.prior)}

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(self.sources)


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


@cache
def _kb_order() -> dict[str, int]:
    return {url: i for i, url in enumerate(load_kb_urls())}


def kb_pages_for(capability_slugs: Iterable[str]) -> list[str]:
    """KB page URLs these capabilities map to (the pages whose chunks may ground JFrog's
    side), pages more of them map to first, then in the order of jfrog_kb.yaml."""
    score: dict[str, int] = {}
    for slug in capability_slugs:
        for url in CAPABILITY_KB_PAGES.get(slug, ()):
            score[url] = score.get(url, 0) + 1
    order = _kb_order()
    return sorted(score, key=lambda u: (-score[u], order.get(u, len(order))))


def _doc_sources(members: _Members, quotes: dict[int, list[str]]) -> list[_Source]:
    out = []
    for d in members.docs:
        body = f"Title: {d['title']}\n\n{d['text']}"
        shown = _flat(body)
        extra = [q for q in quotes.get(d["id"], []) if _flat(q) not in shown]
        if extra:  # a verified quote from beyond the shown words: show it with its document
            body += "\n\nFurther verified excerpts:\n" + "\n".join(f'"{q}"' for q in extra)
        day = date.fromisoformat(d["date"]) if d["date"] != "unknown" else None
        out.append(
            _Source(
                ref=f"doc:{d['id']}",
                header=f"doc:{d['id']} (source: {d['source']}, grade {d['grade']}, "
                f"published {d['date']})",
                body=body,
                published=datetime.combine(day, time(12), UTC) if day else None,
            )
        )
    return out


@dataclass(frozen=True)
class _Record:
    """The signal's stage-A record as persisted, plus the KB pages its capabilities map to."""

    signal: Signal
    companies: list[str]  # "Name (relationship, tier n)", JFrog itself never
    capabilities: list[tuple[str, str]]  # (slug, name)
    kits: list[str]
    quotes: list[tuple[int, str]]  # verified evidence (document id, quote)
    kb_pages: dict[int, JfrogKbDoc]  # the mapped pages present in jfrog_kb_docs, by id


def _grade(sig: Signal) -> str:
    if sig.reliability and sig.credibility is not None:
        return f"{sig.reliability}{sig.credibility}"
    return "ungraded"


def _profile(sig: Signal, n_docs: int) -> str:
    return (
        f"Evidence profile: grade {_grade(sig)}; independent sources: "
        f"{sig.corroboration_count}; hedged: {'yes' if sig.hedged else 'no'}; "
        f"member documents shown: {n_docs}"
    )


def _stage_a(rec: _Record, shown: set[int]) -> str:
    sig = rec.signal
    grade = _grade(sig)
    event = sig.event_date.isoformat() if sig.event_date else "not stated"
    lines = [
        f"Title: {sig.title}",
        f"Type: {sig.signal_type or 'unclassified'}; stance for JFrog: "
        f"{sig.stance or 'unknown'}; event date: {event}",
        f"Companies: {', '.join(rec.companies) or 'none identified'}",
        f"Grade: {grade}; independent sources: {sig.corroboration_count}; "
        f"hedged: {'yes' if sig.hedged else 'no'}",
        f"Internal scores: impact {sig.impact_score or '-'} of 5, novelty "
        f"{sig.novelty_score or '-'} of 5",
        f"Capabilities: {', '.join(name for _, name in rec.capabilities) or 'none tagged'}",
        f"Key intelligence topics: {'; '.join(rec.kits) or 'none'}",
        f"Fact: {sig.fact or '-'}",
        f"Impact: {sig.impact or '-'}",
    ]
    quotes = [(d, q) for d, q in rec.quotes if d in shown]  # only citable documents
    if quotes:
        lines.append("Verified quotes (verbatim from the named document):")
        lines += [f'- doc:{doc_id}: "{_flat(q)}"' for doc_id, q in quotes]
    return untrusted("stage-a", "\n".join(lines), max_chars=STAGE_A_CHARS)


async def _load_record(
    session_factory: async_sessionmaker[AsyncSession], signal_id: int
) -> _Record | None:
    async with session_factory() as session:
        sig = await session.get(Signal, signal_id)
        if sig is None:
            return None
        companies = [
            f"{r.name} ({r.relationship}, tier {r.tier})"
            for r in await session.execute(
                select(Competitor.name, Competitor.relationship, Competitor.tier)
                .join(SignalCompetitor, SignalCompetitor.competitor_id == Competitor.id)
                .where(SignalCompetitor.signal_id == signal_id, ~Competitor.is_self)
                .order_by(Competitor.tier, Competitor.name)
            )
        ]
        caps = [
            (r.slug, r.name)
            for r in await session.execute(
                select(Capability.slug, Capability.name)
                .join(SignalCapability, SignalCapability.capability_id == Capability.id)
                .where(SignalCapability.signal_id == signal_id)
                .order_by(Capability.slug)
            )
        ]
        kits = list(
            await session.scalars(
                select(Kit.title)
                .join(SignalKit, SignalKit.kit_id == Kit.id)
                .where(SignalKit.signal_id == signal_id)
                .order_by(Kit.slug)
            )
        )
        quotes = [
            (r.document_id, r.quote)
            for r in await session.execute(
                select(Evidence.document_id, Evidence.quote)
                .where(Evidence.signal_id == signal_id, Evidence.verified.is_(True))
                .order_by(Evidence.id)
            )
        ]
        urls = kb_pages_for(slug for slug, _ in caps)
        pages = {
            kb.id: kb
            for kb in await session.scalars(select(JfrogKbDoc).where(JfrogKbDoc.url.in_(urls)))
        }
    return _Record(sig, companies, caps, kits, quotes, pages)


async def _kb_sources(
    rec: _Record, llm: AccountedLLM, deps: ToolDeps, ctx: RunCtx
) -> list[_Source]:
    """The mapped pages' indexed chunks closest to the signal: its centroid, or (without
    one) an embedding of its title and fact. KB_CHUNKS at most, KB_PER_PAGE per page; a page
    is shown as one `kb:<id>` source with its chunks in reading order, the closest page first.
    The candidate set is a handful of pages, so the ranking is an exact sort, not an index scan.
    """
    if not rec.kb_pages:
        return []
    sig = rec.signal
    vector = sig.centroid
    if vector is None:  # no session is open across the embed
        text = "\n".join(p for p in (sig.title, sig.fact) if p)
        try:
            [vector] = await embed_batched(llm, [text], ctx=ctx, task=EMBED_TASK)
        except (LLMRefusal, LLMError) as exc:  # costs the excerpts, not the analysis
            logger.warning("KB excerpts skipped for signal %s: %s", sig.id, exc)
            return []
    dist = RetrievalItem.embedding.cosine_distance(list(vector))
    async with deps.session_factory() as session:
        rows = (
            await session.execute(
                select(RetrievalItem.ref_id, RetrievalItem.chunk_no, RetrievalItem.text)
                .where(
                    RetrievalItem.kind == RetrievalKind.jfrog_kb,
                    RetrievalItem.ref_id.in_(list(rec.kb_pages)),
                    RetrievalItem.embedding.is_not(None),
                )
                .order_by(dist, RetrievalItem.ref_id, RetrievalItem.chunk_no)
            )
        ).all()
    picked: dict[int, list[tuple[int, str]]] = {}  # page -> chunks; pages by closest chunk
    taken = 0
    for ref_id, chunk_no, text in rows:
        chunks = picked.get(ref_id, [])
        if taken < KB_CHUNKS and len(chunks) < KB_PER_PAGE and text.strip():
            picked[ref_id] = [*chunks, (chunk_no, text.strip())]
            taken += 1
    out = []
    for ref_id, chunks in picked.items():
        kb = rec.kb_pages[ref_id]
        excerpts = "\n\n[...]\n\n".join(text for _, text in sorted(chunks))
        out.append(
            _Source(
                ref=f"kb:{kb.id}",
                header=f"kb:{kb.id} (JFrog knowledge base: {kb.url}; excerpts)",
                body=f"Title: {kb_title(kb.title, kb.url)}\n\n{excerpts}",
            )
        )
    return out


async def _prior(sig: Signal, deps: ToolDeps, llm: AccountedLLM, ctx: RunCtx) -> list[_Source]:
    """The analyst's prior-signal lookup (get_similar_signals), narrowed to signals
    every reader can open and that predate this story's latest update: top PRIOR_K."""
    try:
        result = await get_similar_signals(SimilarSignalsArgs(signal_id=sig.id, k=PRIOR_POOL), deps)
    except BudgetExceeded:
        raise
    except Exception as exc:  # a lookup problem must not sink the analysis
        logger.warning("prior signals lookup failed for %s: %s", sig.id, exc)
        return []
    await llm.log_step(
        ctx,
        kind=StepKind.tool,
        tool_name="get_similar_signals",
        args={"signal_id": sig.id, "k": PRIOR_POOL, "prefetch": True},
        result_summary=result.content,
    )
    ids = [i for i in result.data or [] if isinstance(i, int)]
    if not ids:
        return []
    async with deps.session_factory() as session:
        rows = {
            s.id: s
            for s in await session.scalars(
                select(Signal).where(
                    Signal.id.in_(ids),
                    Signal.status.in_(PRIOR_STATUSES),
                    Signal.scope != Scope.jfrog,
                    Signal.first_seen_at <= sig.last_updated_at,
                )
            )
        }
        names: dict[int, list[str]] = {}
        for sid, name in await session.execute(
            select(SignalCompetitor.signal_id, Competitor.name)
            .join(Competitor, Competitor.id == SignalCompetitor.competitor_id)
            .where(SignalCompetitor.signal_id.in_(list(rows)), ~Competitor.is_self)
            .order_by(Competitor.tier, Competitor.name)
        ):
            names.setdefault(sid, []).append(name)
    kept = [rows[i] for i in ids if i in rows][:PRIOR_K]
    return [
        _Source(
            ref=f"signal:{s.id}",
            header=f"signal:{s.id} (FrogEye signal first seen {s.first_seen_at.date()}; "
            f"companies: {', '.join(names.get(s.id, [])) or 'none'})",
            body=f"Title: {s.title}\nFact: {s.fact or '-'}\nImpact: {s.impact or '-'}",
            published=s.first_seen_at,
        )
        for s in kept
    ]


async def _gather(signal_id: int, deps: ToolDeps, llm: AccountedLLM, ctx: RunCtx) -> _Inputs | None:
    members = await _load_members(deps.session_factory, signal_id)  # session closed here
    if members is None:
        return None
    if not members.docs:  # nothing to analyse, and never a schema without refs
        return _Inputs("", "", [], [], [])
    rec = await _load_record(deps.session_factory, signal_id)
    if rec is None:  # deleted since its members were read
        return None
    by_doc: dict[int, list[str]] = {}
    for doc_id, quote in rec.quotes:
        by_doc.setdefault(doc_id, []).append(quote)
    return _Inputs(
        stage_a=_stage_a(rec, {d["id"] for d in members.docs}),
        profile=_profile(rec.signal, len(members.docs)),
        docs=_doc_sources(members, by_doc),
        kb=await _kb_sources(rec, llm, deps, ctx),
        prior=await _prior(rec.signal, deps, llm, ctx),
    )


# ---- verification -----------------------------------------------------------------------


@dataclass
class _Item:
    """A checked item: where it lives in the content, its text and its evidence. A factual
    item is judged against its cites; an action or the note (no cites) against every
    source. `advice_ok`: an action may pass as advice (as in the newsroom)."""

    section: str
    index: int | None
    label: str
    text: str
    cites: tuple[str, ...]
    advice_ok: bool = False
    evidence: str = ""  # for an item without cites: what it is judged against


def _factual_items(data: dict[str, Any]) -> list[_Item]:
    """Every cited item in display order (the fact-check numbering)."""
    summary = data["summary"]
    items = [_Item("summary", None, "summary", summary["text"], tuple(summary["cites"]))]
    for section in _CITED_SECTIONS:
        items += [
            _Item(section, i, section, p["text"], tuple(p["cites"]))
            for i, p in enumerate(data[section])
        ]
    items += [
        _Item(
            "jfrog_impact", i, f"impact on JFrog {p['product']}", p["rationale"], tuple(p["cites"])
        )
        for i, p in enumerate(data["jfrog_impact"])
    ]
    for section in _LATE_SECTIONS:
        for i, p in enumerate(data[section]):
            label = f"{section} {p['kind']}" if section == "risks_opportunities" else section
            items.append(_Item(section, i, label, p["text"], tuple(p["cites"])))
    return items


def _action_items(data: dict[str, Any]) -> list[_Item]:
    """Every action and the confidence note, after the factual items."""
    items = []
    actions = data["recommended_actions"]
    for role, lists in _ACTION_LISTS.items():
        for name in lists:
            key = f"{role}.{name}"
            label = f"action: {_ACTION_LABELS[key]}"
            items += [
                _Item(key, i, label, text, (), advice_ok=True, evidence="all sources")
                for i, text in enumerate(actions[role][name])
            ]
    key = "sales.objections"
    items += [
        _Item(
            key,
            i,
            f"action: {_ACTION_LABELS[key]}",
            f'Customer objection: "{o["objection"]}" Seller response: "{o["response"]}"',
            (),
            advice_ok=True,
            evidence="all sources",
        )
        for i, o in enumerate(actions["sales"]["objections"])
    ]
    if data["confidence_note"]:
        items.append(
            _Item(
                "confidence_note",
                None,
                "confidence note",
                data["confidence_note"],
                (),
                evidence="all sources and the evidence profile",
            )
        )
    return items


def _normalise(data: dict[str, Any]) -> None:
    """In place: strip stray ref markers and blank runs from every text, dedupe cites."""
    data["summary"]["text"] = _clean(data["summary"]["text"])
    data["summary"]["cites"] = list(dict.fromkeys(data["summary"]["cites"]))
    for section in (*_CITED_SECTIONS, "jfrog_impact", *_LATE_SECTIONS):
        for p in data[section]:
            key = "rationale" if section == "jfrog_impact" else "text"
            p[key] = _clean(p[key])
            p["cites"] = list(dict.fromkeys(p["cites"]))
    actions = data["recommended_actions"]
    for role, lists in _ACTION_LISTS.items():
        for name in lists:
            actions[role][name] = [_clean(t) for t in actions[role][name]]
    actions["sales"]["objections"] = [
        {"objection": _clean(o["objection"]), "response": _clean(o["response"])}
        for o in actions["sales"]["objections"]
    ]
    data["confidence_note"] = _clean(data["confidence_note"])


class _Checker:
    """Deterministic checks of one analysis against the sources it was shown."""

    def __init__(self, inputs: _Inputs, brief: str) -> None:
        self.sources = inputs.sources
        self.brief = brief

    def corpus(self, refs: Iterable[str]) -> list[str]:
        return [self.sources[r].body for r in refs if r in self.sources]

    def published(self, refs: Iterable[str]) -> list[datetime]:
        dates = (self.sources[r].published for r in refs if r in self.sources)
        return [d for d in dates if d is not None]

    def item_ok(self, item: _Item) -> bool:
        if not item.text or not item.cites:
            return False
        if item.section in _STORY_SECTIONS and not any(c.startswith("doc:") for c in item.cites):
            return False  # what this story says rests on its own documents
        if item.section == "background" and not any(
            c.startswith(_BACKGROUND_REFS) for c in item.cites
        ):
            return False  # JFrog's capabilities go in jfrog_impact, not background
        corpus = self.corpus(item.cites)
        if item.section == "jfrog_impact":  # JFrog's side may use the brief (as stage A)
            corpus.append(self.brief)
        return not unknown_numbers_in([item.text], corpus, self.published(item.cites))

    def advice_ok(self, *texts: str) -> bool:
        """Advice needs no cites, but its numbers must be in some provided source."""
        everything = [*self.corpus(self.sources), self.brief]
        return all(texts) and not unknown_numbers_in(
            texts, everything, self.published(self.sources)
        )

    def watch_date(self, item: dict[str, Any]) -> str | None:
        if item["date"] is None:
            return None
        day = date.fromisoformat(item["date"])
        corpus = "\n".join(self.corpus(item["cites"]))
        return item["date"] if mentions_date(day, corpus, self.published(item["cites"])) else None


def _drop(data: dict[str, Any], dropped: set[tuple[str, int | None]]) -> dict[str, Any]:
    """The content without the dropped items: a dropped summary or confidence note is left
    empty, dropped list entries (factual items and actions) are removed."""
    out = dict(data)
    if ("summary", None) in dropped:
        out["summary"] = {"text": "", "cites": []}
    if ("confidence_note", None) in dropped:
        out["confidence_note"] = ""
    for section in (*_CITED_SECTIONS, "jfrog_impact", *_LATE_SECTIONS):
        out[section] = [p for i, p in enumerate(data[section]) if (section, i) not in dropped]
    actions = {role: dict(lists) for role, lists in data["recommended_actions"].items()}
    for role, lists in _ACTION_LISTS.items():
        for name in (*lists, *(("objections",) if role == "sales" else ())):
            key = f"{role}.{name}"
            actions[role][name] = [
                a for i, a in enumerate(actions[role][name]) if (key, i) not in dropped
            ]
    out["recommended_actions"] = actions
    return out


def _clear_idle_risk_level(data: dict[str, Any]) -> None:
    exec_ = data["recommended_actions"]["exec"]
    if not exec_["implications"] and not exec_["decisions"]:
        exec_["risk_level"] = None  # no exec block to rate


def _check_actions(data: dict[str, Any], checker: _Checker) -> int:
    """In place: drop actions failing the number guard (or empty). Returns how many."""
    removed = 0
    actions = data["recommended_actions"]
    for role, lists in _ACTION_LISTS.items():
        for name in lists:
            kept = [t for t in actions[role][name] if checker.advice_ok(t)]
            removed += len(actions[role][name]) - len(kept)
            actions[role][name] = kept
    pairs = actions["sales"]["objections"]
    kept_pairs = [o for o in pairs if checker.advice_ok(o["objection"], o["response"])]
    removed += len(pairs) - len(kept_pairs)
    actions["sales"]["objections"] = kept_pairs
    if data["confidence_note"] and not checker.advice_ok(data["confidence_note"]):
        data["confidence_note"] = ""  # about the evidence; guarded like advice, not checked
        removed += 1
    return removed


def _deterministic(data: dict[str, Any], checker: _Checker) -> tuple[dict[str, Any], int]:
    """Cites and numbers first (no LLM). Returns the surviving content and items removed."""
    _normalise(data)
    for p in data["what_to_watch"]:
        p["date"] = checker.watch_date(p)
    dropped = {(it.section, it.index) for it in _factual_items(data) if not checker.item_ok(it)}
    seen: set[str] = set()
    for i, p in enumerate(data["jfrog_impact"]):  # one entry per product (Plan 3 aggregates)
        if ("jfrog_impact", i) not in dropped:
            if p["product"] in seen:
                dropped.add(("jfrog_impact", i))
            seen.add(p["product"])
    kept = _drop(data, dropped)
    removed = len(dropped) + _check_actions(kept, checker)
    _clear_idle_risk_level(kept)
    return kept, removed


def _items_block(items: list[_Item]) -> str:
    lines = (
        f"S{n} ({it.label}; "
        + (f"cites {', '.join(it.cites)}" if it.cites else f"evidence: {it.evidence}")
        + f"): {it.text}"
        for n, it in enumerate(items, 1)
    )
    return untrusted("items", "\n" + "\n".join(lines) + "\n", max_chars=ITEMS_CHARS)


def _fact_check_request(
    items: list[_Item], inputs: _Inputs, brief: str
) -> tuple[type[BaseModel], dict[str, Any]]:
    """Every source is sent: actions and the note are judged against all of them."""
    variables = {
        "jfrog_context": brief,
        "sources": "\n\n".join(s.view() for s in inputs.sources.values()),
        "profile": inputs.profile,
        "items": _items_block(items),
    }
    return make_fact_check_model(len(items), reason_first=True), variables


async def _fact_check(
    llm: AccountedLLM,
    items: list[_Item],
    inputs: _Inputs,
    brief: str,
    *,
    keys: list[str],
    ctx: RunCtx,
) -> dict[int, list[Any]]:
    """The verdicts of every item (by its index in `items`), with the re-ask of
    `verified_llm.fact_check`: still missing after it raises IncompleteFactCheck, so a gutted
    analysis is never stored. Each request's key joins `keys` (the signal's cost)."""
    return await fact_check(
        llm,
        len(items),
        lambda pending: _fact_check_request([items[i] for i in pending], inputs, brief),
        task=CHECK_TASK,
        prompt=load_prompt(CHECK_PROMPT),
        keys=keys,
        ctx=ctx,
        tier=CHECK_TIER,
    )


# ---- persistence ------------------------------------------------------------------------


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    signal_id: int,
    content: SignalAnalysisContent,
    *,
    model: str,
    prompt_version: str,
    cost: float,
) -> int:
    async with session_factory() as session:
        version = await persist_analysis(
            session,
            signal_id,
            content,
            model=model,
            prompt_version=prompt_version,
            cost_usd=cost,
        )
        await session.commit()
    return version


# ---- the agent --------------------------------------------------------------------------


def _today(llm: AccountedLLM) -> str:
    """The run's local date, in the user section: actions "this week" and future
    watch dates have a reference, and the cache key changes daily, so a signal whose
    analysis failed is asked afresh the next day (a same-day resume still hits the cache)."""
    return datetime.now(UTC).astimezone(ZoneInfo(llm.settings.timezone)).date().isoformat()


async def _note(llm: AccountedLLM, ctx: RunCtx, signal_id: int, summary: str) -> None:
    await llm.log_step(
        ctx, kind=StepKind.node, args={"signal_id": signal_id}, result_summary=summary
    )


async def analyze_signal_deep(
    signal_id: int,
    *,
    llm: AccountedLLM,
    deps: ToolDeps,
    enums: RunEnums,  # unused: the signature every per-signal agent node shares
    ctx: RunCtx,
) -> DeepOutcome:
    """Write, verify and store one signal's stage-B analysis, then re-index the signal.

    Returns ok=False (nothing stored) on a missing signal, no member documents, an LLM
    error or refusal (schema-invalid output included), a failed or incomplete fact-check
    (items still without a verdict after one re-ask), or a summary or every
    what_happened paragraph failing the checks. Raises BudgetExceeded."""
    deps = deps if deps.ctx is not None else dataclasses.replace(deps, ctx=ctx)

    async def fail(error: str, *, removed: int = 0, prefix: str = "error") -> DeepOutcome:
        logger.warning("signal analysis of %s: %s", signal_id, error)
        await _note(llm, ctx, signal_id, f"{prefix}: {error}")
        return DeepOutcome(signal_id, False, None, removed, error)

    inputs = await _gather(signal_id, deps, llm, ctx)
    if inputs is None:
        return await fail("signal not found", prefix="skipped")
    if not inputs.docs:
        return await fail("no member documents", prefix="skipped")

    brief = load_jfrog_context()
    prompt = load_prompt(TASK)
    schema = make_analysis_model(inputs.refs)
    variables = {
        "jfrog_context": brief,
        "today": _today(llm),
        "refs": ", ".join(inputs.refs),
        "signal": inputs.stage_a,
        "documents": [s.view() for s in inputs.docs],
        "kb": [s.view() for s in inputs.kb],
        "prior": [s.view() for s in inputs.prior],
    }
    keys = [llm.request_key("smart", schema, prompt=prompt, variables=variables)]
    floor = await ledger_floor(deps.session_factory)
    try:
        out = await llm.structured(
            TASK, "smart", schema, prompt=prompt, variables=variables, ctx=ctx
        )
    except (LLMRefusal, LLMError) as exc:  # includes a citation outside the shown refs
        return await fail(f"{type(exc).__name__}: {exc}")

    checker = _Checker(inputs, brief)
    data, removed = _deterministic(out.model_dump(mode="json"), checker)
    if not data["summary"]["text"]:
        return await fail("the summary failed the citation or number checks", removed=removed)
    if not data["what_happened"]:
        return await fail(
            "no what happened paragraph passed the citation or number checks", removed=removed
        )

    items = [*_factual_items(data), *_action_items(data)]
    try:
        verdicts = await _fact_check(llm, items, inputs, brief, keys=keys, ctx=ctx)
    except (LLMRefusal, LLMError) as exc:  # fail closed: never store unchecked claims
        return await fail(f"fact-check failed: {type(exc).__name__}: {exc}", removed=removed)
    except IncompleteFactCheck as exc:  # never store a half-checked analysis
        return await fail(f"fact-check incomplete: {exc}", removed=removed)
    dropped = unsupported(items, verdicts)
    data = _drop(data, dropped)
    _clear_idle_risk_level(data)
    removed += len(dropped)
    if not data["summary"]["text"]:
        return await fail("the fact-check did not support the summary", removed=removed)
    if not data["what_happened"]:  # a summary-only analysis is a failure
        return await fail("the fact-check supported no what happened paragraph", removed=removed)

    content = SignalAnalysisContent.model_validate(data)
    cost = await calls_cost(deps.session_factory, floor, keys)  # this signal's calls only
    try:
        version = await _persist(
            deps.session_factory,
            signal_id,
            content,
            model=llm.settings.model_smart,
            # Both prompts' versions, so a checker bump is visible in the rows
            prompt_version=f"{prompt.version}+check{load_prompt(CHECK_PROMPT).version}",
            cost=cost,
        )
    except IntegrityError as exc:  # the signal vanished, or a concurrent writer won the version
        return await fail(f"not stored: {type(exc).__name__}", removed=removed)

    try:
        await index_signal(deps.session_factory, llm, signal_id, ctx=ctx)
    except (LLMRefusal, LLMError) as exc:  # BudgetExceeded propagates; the row is kept
        logger.warning("indexing signal %s failed: %s", signal_id, exc)
        await _note(llm, ctx, signal_id, f"index error: {exc}")
    await _note(
        llm,
        ctx,
        signal_id,
        f"analysed v{version}: {len(_factual_items(data))} cited items, {removed} removed",
    )
    return DeepOutcome(signal_id, True, version, removed)
