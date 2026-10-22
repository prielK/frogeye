"""Company facts: verified, quoted statements about competitors.

Facts are funding rounds, valuations, headcount, layoffs, revenue and ARR, customers,
market share, acquisitions, leadership changes, partnerships and offices. They come from
what FrogEye already collects: relevant documents and about/careers page snapshots.

Selection (`select_fact_candidates`):
- relevant documents of the last `analysis_lookback_days` (a backfill may pass a longer
  `lookback_days`) and about/careers snapshots, both with `facts_extracted_at IS NULL`
  and not given up (`app.agents.retries`: the cap counts only sources still eligible), that
  pass the keyword screen (`SCREEN_TERMS`, the list): a document on its title
  and text, a page's first snapshot on its full text, a later one only on the lines it adds
  to the previous snapshot, from the two full texts (never cut at the stored diff's
  cap; a careers page that changes daily is not read again every day);
- documents of watched pages are never sources: the page judge files them with its
  own summary and the removed lines in their text; the snapshots are the pages' sources;
- the page judge's verdict (`meaningful`, `judged_at`) is ignored: a snapshot closed by the
  trivial-diff gate is still a fact source;
- a source that fails the screen, or whose companies are only the self one or none, is
  closed there (`facts_extracted_at` set, no call), so each is screened once;
- newest first, at most `limit` (default `settings.fact_extract_cap`).

Extraction (`extract_facts`): one smart-tier structured call per source. The schema is
strict and its `competitor` is a Literal over the source's non-self companies, so a
fact for any other company (JFrog included) fails the whole answer. The source reaches the
model only through `untrusted()`, as keyword-window excerpts when it is long. Each
fact is checked against the FULL source text by `app.intel.fact_checks.verify_fact` (the
pure checks, listed there) and dropped when a check fails. Every
source knows the self competitor's names, linked or not (it is never linked), so a JFrog
line filed under a competitor is dropped.
Only verified facts are stored (`verified=True`), one row per source
(`upsert_fact`): the same source again updates its row, another source adds a row,
and conflicting values stand side by side, never averaged. The store itself refuses a fact
for the self competitor, after the selection and the Literal.

`facts_extracted_at` is set once the model's answer is checked, whether its facts were
stored or dropped. An LLM error or refusal (a company outside the Literal included) leaves
it NULL and records the failure with its reason, so the source is tried again next run,
until its third failed day gives it up (`given_up`). A budget stop is reported,
never raised, and the briefing reserve applies: no source is started once the day's remaining budget
is under the newsroom's reserve. Sessions are short: the selection is read and closed
before any call, and each source's facts and its mark are written in one short transaction
after its call.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.agents.retries import ERROR_CHARS, RetryColumns, cleared, eligible, record_failure
from app.agents.runtime.accounted import AccountedLLM, BudgetExceeded, LLMError, LLMRefusal
from app.agents.runtime.context import RunCtx
from app.agents.runtime.prompts import load_prompt, untrusted
from app.agents.verified_llm import reserve_reached
from app.core.config import Settings
from app.intel.fact_checks import (
    EXCERPT_CHARS,
    FactSource,
    added_lines,
    fact_excerpt,
    normalise_label,
    passes_screen,
    verify_fact,
)
from app.intel.pages import added_text
from app.models import CompanyFact, Competitor, Document, DocumentCompetitor, PageSnapshot, Source
from app.models.enums import DocStatus, FactKind, FactSourceKind, PageKind, SourceKind

logger = logging.getLogger(__name__)

TASK = "fact_extractor"  # smart tier; also the prompt name
FACT_CONCURRENCY = 4
FACT_PAGES = (PageKind.about.value, PageKind.careers.value)  # pricing pages are not read
_KINDS = tuple(k.value for k in FactKind)
_RETRIES = {
    "document": RetryColumns(
        Document.facts_failures, Document.facts_failed_at, Document.facts_error
    ),
    "page_snapshot": RetryColumns(
        PageSnapshot.facts_failures, PageSnapshot.facts_failed_at, PageSnapshot.facts_error
    ),
}


# ---- sources ----------------------------------------------------------------------------


@dataclass(frozen=True)
class FactOutcome:
    stored: int  # fact rows written (a repeat inside one answer updates the same row)
    dropped: int  # facts that failed a check
    error: str | None = None  # an LLM error or refusal: nothing written, tried next run


@dataclass(frozen=True)
class FactBatch:
    """What one batch did (the daily node and the backfill CLI share it)."""

    sources: int = 0  # read by the model and closed
    stored: int = 0
    dropped: int = 0
    failed: int = 0  # sources left for the next run by an LLM error or refusal
    reserved: int = 0  # not started: the rest of today's budget is the briefing's
    budget_stopped: bool = False
    given_up: int = 0  # of the failed, those whose third failed day it was

    def summary(self) -> str:
        return (
            f"company facts: {self.sources} sources read, {self.stored} facts stored, "
            f"{self.dropped} dropped by the checks, {self.failed} failed"
            + (f" ({self.given_up} given up after their third failed day)" if self.given_up else "")
        )

    def reserve_note(self) -> str | None:
        if not self.reserved:
            return None
        return (
            f"company facts: {self.reserved} sources not started, the rest of today's budget "
            "is kept for the briefing; they wait for the next run"
        )


@dataclass(frozen=True)
class _Linked:
    id: int
    slug: str
    name: str
    is_self: bool
    names: tuple[str, ...]  # name, aliases and ambiguous aliases


def _names_of(name: str, aliases: list[str] | None, ambiguous: list[str] | None) -> tuple[str, ...]:
    """The names a company goes by: its name, aliases and ambiguous aliases."""
    every = (name, *(aliases or ()), *(ambiguous or ()))
    return tuple(dict.fromkeys(n for n in every if n))


async def _self_names(session: AsyncSession) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(slug, names) of the self competitor(s): known to every source, linked or not."""
    rows = await session.execute(
        select(
            Competitor.slug, Competitor.name, Competitor.aliases, Competitor.ambiguous_aliases
        ).where(Competitor.is_self)
    )
    return tuple(
        (slug, _names_of(name, aliases, ambiguous)) for slug, name, aliases, ambiguous in rows.all()
    )


def _known(
    own: list[tuple[str, tuple[str, ...]]], selves: tuple[tuple[str, tuple[str, ...]], ...]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    found = dict(own)
    for slug, names in selves:
        found.setdefault(slug, names)
    return tuple(found.items())


async def _document_companies(
    session: AsyncSession, doc_ids: list[int]
) -> dict[int, list[_Linked]]:
    """Each document's linked companies, the self one included, by slug."""
    if not doc_ids:
        return {}
    rows = await session.execute(
        select(
            DocumentCompetitor.document_id,
            Competitor.id,
            Competitor.slug,
            Competitor.name,
            Competitor.is_self,
            Competitor.aliases,
            Competitor.ambiguous_aliases,
        )
        .join(Competitor, Competitor.id == DocumentCompetitor.competitor_id)
        .where(DocumentCompetitor.document_id.in_(doc_ids))
        .order_by(Competitor.slug)
    )
    out: dict[int, list[_Linked]] = {}
    for doc_id, cid, slug, name, is_self, aliases, ambiguous in rows.all():
        names = _names_of(name, aliases, ambiguous)
        out.setdefault(doc_id, []).append(_Linked(cid, slug, name, bool(is_self), names))
    return out


def _companies(found: list[tuple[int, str, str]]) -> dict[str, Any]:
    return {
        "competitor_ids": tuple(c[0] for c in found),
        "slugs": tuple(c[1] for c in found),
        "names": tuple(c[2] for c in found),
    }


async def select_fact_candidates(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    limit: int | None = None,
    now: datetime | None = None,
    lookback_days: int | None = None,
) -> list[FactSource]:
    """The next sources worth a model call, newest first (see the module docstring).
    `lookback_days` (default `analysis_lookback_days`) is how far back documents are read.

    Sources that fail the screen or have no non-self company are closed here, in one short
    transaction; nothing else is written."""
    wanted = settings.fact_extract_cap if limit is None else limit
    if wanted < 1:
        return []
    now = now or datetime.now(UTC)
    days = settings.analysis_lookback_days if lookback_days is None else lookback_days
    when = func.coalesce(Document.published_at, Document.created_at)
    found: list[tuple[datetime, FactSource]] = []
    closed_docs: list[int] = []
    closed_snaps: list[int] = []
    async with session_factory() as session:
        selves = await _self_names(session)
        docs = (
            await session.execute(
                select(
                    Document.id,
                    Document.title,
                    Document.clean_text,
                    Document.summary,
                    Document.published_at,
                    when,
                    Source.name,
                )
                .join(Source, Source.id == Document.source_id)
                .where(
                    Document.status == DocStatus.relevant,
                    Document.facts_extracted_at.is_(None),
                    eligible(_RETRIES["document"]),
                    when >= now - timedelta(days=days),
                    Source.kind != SourceKind.page_watch,  # the judge's own words
                )
            )
        ).all()
        companies = await _document_companies(session, [d[0] for d in docs])
        for doc_id, title, clean, summary, published, at, source_name in docs:
            parts = ((title or "").strip(), (clean or summary or "").strip())
            text = "\n\n".join(p for p in parts if p)
            linked = companies.get(doc_id, [])
            comps = [(c.id, c.slug, c.name) for c in linked if not c.is_self]
            if not comps or not passes_screen(text):
                closed_docs.append(doc_id)
                continue
            source = FactSource(
                kind="document",
                id=doc_id,
                text=text,
                published_at=published,
                focus=text,
                origin=f"{source_name}, document doc:{doc_id}",
                known=_known([(c.slug, c.names) for c in linked], selves),
                dated_at=at,
                **_companies(comps),
            )
            found.append((at, source))

        prev = aliased(PageSnapshot)
        previous_text = (  # the snapshot before it, whose text its new lines are read from
            select(prev.text)
            .where(prev.source_id == PageSnapshot.source_id, prev.id < PageSnapshot.id)
            .order_by(prev.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        snaps = (
            await session.execute(
                select(
                    PageSnapshot.id,
                    PageSnapshot.text,
                    PageSnapshot.diff_from_prev,
                    PageSnapshot.fetched_at,
                    PageSnapshot.page_kind,
                    Source.url,
                    Source.config,
                    Competitor.id,
                    Competitor.slug,
                    Competitor.name,
                    Competitor.is_self,
                    Competitor.aliases,
                    Competitor.ambiguous_aliases,
                    previous_text,
                )
                .join(Source, Source.id == PageSnapshot.source_id)
                .outerjoin(Competitor, Competitor.id == Source.competitor_id)
                .where(
                    PageSnapshot.facts_extracted_at.is_(None),
                    eligible(_RETRIES["page_snapshot"]),
                    PageSnapshot.page_kind.in_(FACT_PAGES),
                )
            )
        ).all()
        for row in snaps:
            sid, text, diff, fetched, kind, url, config = row[:7]
            cid, slug, name, is_self, aliases, ambiguous, before = row[7:]
            # a page's first snapshot is read whole, a later one by its new lines only,
            # all of them (from the two full texts, not the capped stored diff)
            if diff is None:
                focus = text
            elif before is not None:
                focus = added_text(before, text)
            else:
                focus = added_lines(diff)
            if cid is None or is_self or not passes_screen(focus):
                closed_snaps.append(sid)
                continue
            page_url = str((config or {}).get("url") or url)
            lines = "" if diff is None else ", its new lines only"
            source = FactSource(
                kind="page_snapshot",
                id=sid,
                text=text,
                published_at=fetched,
                focus=focus,
                origin=f"{name}'s {kind} page ({page_url}), snapshot page:{sid}{lines}",
                known=_known([(slug, _names_of(name, aliases, ambiguous))], selves),
                dated_at=fetched,
                **_companies([(cid, slug, name)]),
            )
            found.append((fetched, source))

        if closed_docs:
            await session.execute(
                update(Document)
                .where(Document.id.in_(closed_docs), Document.facts_extracted_at.is_(None))
                .values(facts_extracted_at=now)
            )
        if closed_snaps:
            await session.execute(
                update(PageSnapshot)
                .where(PageSnapshot.id.in_(closed_snaps), PageSnapshot.facts_extracted_at.is_(None))
                .values(facts_extracted_at=now)
            )
        if closed_docs or closed_snaps:
            await session.commit()
    found.sort(key=lambda t: (t[0], t[1].id), reverse=True)
    return [source for _, source in found[:wanted]]


# ---- the schema and the checks ------------------------------------------------------------


@lru_cache(maxsize=64)
def make_fact_model(slugs: tuple[str, ...]) -> type[BaseModel]:
    """The strict output schema: every field required, the company a Literal over the
    source's non-self slugs and the kind one of the fact kinds."""
    company = Literal[slugs]  # type: ignore[valid-type]
    kind = Literal[_KINDS]  # type: ignore[valid-type]
    fact = create_model(
        "ExtractedFact",
        __config__=ConfigDict(extra="forbid"),
        competitor=(company, ...),
        kind=(kind, ...),
        value_num=(float | None, ...),
        unit=(str | None, ...),
        currency=(str | None, ...),
        label=(str, ...),
        as_of=(date | None, ...),
        quote=(str, ...),
    )
    return create_model(
        "CompanyFacts",
        __config__=ConfigDict(extra="forbid"),
        facts=(list[fact], ...),  # type: ignore[valid-type]
    )


# ---- storage ------------------------------------------------------------------------------


async def upsert_fact(
    session: AsyncSession,
    *,
    competitor_id: int,
    kind: str,
    label: str,
    value_num: float | None,
    unit: str | None,
    currency: str | None,
    as_of: date | None,
    source_kind: str,
    source_id: int,
    quote: str,
    extractor_version: str,
) -> int:
    """Store one verified fact and return its id (flush only).

    One row per source: a fact with the same competitor, kind, normalised label,
    as-of month and source updates that row; any other source adds its own row, so two
    sources that disagree stay two points and nothing is ever averaged.
    Readers group rows by (competitor, kind, label_norm, as-of month).

    The self competitor never gets a fact: the store refuses one with ValueError, the
    last of the guards after the selection and the schema's Literal."""
    if await session.scalar(select(Competitor.is_self).where(Competitor.id == competitor_id)):
        raise ValueError("U5: the self competitor never gets company facts")
    norm = normalise_label(kind, label)
    same = [
        CompanyFact.competitor_id == competitor_id,
        CompanyFact.kind == kind,
        CompanyFact.label_norm == norm,
        CompanyFact.source_kind == source_kind,
        CompanyFact.source_id == source_id,
    ]
    if as_of is None:
        same.append(CompanyFact.as_of.is_(None))
    else:
        first = as_of.replace(day=1)
        same += [CompanyFact.as_of >= first, CompanyFact.as_of < _next_month(first)]
    row = (
        await session.execute(select(CompanyFact).where(*same).order_by(CompanyFact.id).limit(1))
    ).scalar_one_or_none()
    values = {
        "value_num": value_num,
        "unit": unit,
        "currency": currency,
        "label": label,
        "label_norm": norm,
        "as_of": as_of,
        "quote": quote,
        "verified": True,
        "extractor_version": extractor_version,
        "extracted_at": datetime.now(UTC),
    }
    if row is None:
        row = CompanyFact(
            competitor_id=competitor_id,
            kind=kind,
            source_kind=source_kind,
            source_id=source_id,
            **values,
        )
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    await session.flush()
    return row.id


def _next_month(first: date) -> date:
    return (first + timedelta(days=32)).replace(day=1)


async def _close(session: AsyncSession, source: FactSource) -> None:
    """Mark the source read; no failure of it stands any longer."""
    model = Document if source.kind == "document" else PageSnapshot
    await session.execute(
        update(model)
        .where(model.id == source.id)
        .values(facts_extracted_at=datetime.now(UTC), **cleared(_RETRIES[source.kind]))
    )


# ---- extraction ---------------------------------------------------------------------------


async def extract_facts(
    source: FactSource,
    *,
    llm: AccountedLLM,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: RunCtx,
) -> FactOutcome:
    """Ask the model for one source's facts, verify each, store the verified ones and close
    the source. An LLM error or refusal stores nothing and leaves the source open; a budget
    stop (BudgetExceeded) propagates."""
    if not source.slugs:  # the selection never returns one; closed all the same
        async with session_factory() as session:
            await _close(session, source)
            await session.commit()
        return FactOutcome(0, 0)
    prompt = load_prompt(TASK)
    day = source.published_at.date().isoformat() if source.published_at else "unknown"
    variables = {
        "source": source.origin or source.ref,
        "date": day,
        "companies": [
            {"slug": slug, "name": name}
            for slug, name in zip(source.slugs, source.names, strict=True)
        ],
        "document": untrusted(
            source.ref, fact_excerpt(source.focus or source.text), max_chars=EXCERPT_CHARS
        ),
    }
    schema = make_fact_model(source.slugs)
    try:
        out = await llm.structured(
            TASK, "smart", schema, prompt=prompt, variables=variables, ctx=ctx
        )
    except (LLMError, LLMRefusal) as exc:
        logger.warning("company facts: %s left for the next run: %s", source.ref, exc)
        return FactOutcome(0, 0, error=f"{type(exc).__name__}: {exc}"[:ERROR_CHARS])

    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in out.facts:  # type: ignore[attr-defined]
        fact, why = verify_fact(item, source)
        if fact is None:
            dropped += 1
            logger.info("company facts: %s dropped a %s fact: %s", source.ref, item.kind, why)
        else:
            kept.append(fact)
    source_kind = (
        FactSourceKind.document if source.kind == "document" else FactSourceKind.page_snapshot
    )
    ids: set[int] = set()
    async with session_factory() as session:
        for fact in kept:
            ids.add(
                await upsert_fact(
                    session,
                    **fact,
                    source_kind=str(source_kind),
                    source_id=source.id,
                    extractor_version=prompt.version,
                )
            )
        await _close(session, source)
        await session.commit()
    return FactOutcome(stored=len(ids), dropped=dropped)


async def extract_facts_batch(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    settings: Settings,
    ctx: RunCtx,
    *,
    limit: int | None = None,
    now: datetime | None = None,
    lookback_days: int | None = None,
) -> FactBatch:
    """Extract the facts of up to `limit` (default `fact_extract_cap`) sources, newest
    first, `FACT_CONCURRENCY` at a time. The work done before a budget stop is kept. `now`
    (default the clock) dates the selection's lookback and a failure's day; `lookback_days`
    (a backfill's) widens the documents' window."""
    moment = now or datetime.now(UTC)
    picked = await select_fact_candidates(
        session_factory, settings, limit=limit, now=moment, lookback_days=lookback_days
    )
    if not picked:
        return FactBatch()
    sem = asyncio.Semaphore(FACT_CONCURRENCY)
    counts = dict.fromkeys(("sources", "stored", "dropped", "failed", "reserved", "given_up"), 0)
    state = {"stopped": False}

    async def one(source: FactSource) -> None:
        async with sem:
            if state["stopped"]:
                return
            if await reserve_reached(llm, settings):
                counts["reserved"] += 1
                return
            try:
                out = await extract_facts(source, llm=llm, session_factory=session_factory, ctx=ctx)
            except BudgetExceeded:
                state["stopped"] = True
                return
        if out.error is not None:
            counts["failed"] += 1
            model = Document if source.kind == "document" else PageSnapshot
            async with session_factory() as session:
                gave_up = await record_failure(
                    session,
                    model,
                    _RETRIES[source.kind],
                    model.id == source.id,
                    reason=out.error,
                    now=moment,
                )
                await session.commit()
            if gave_up:
                logger.warning(
                    "company facts: %s given up after its third failed day: %s",
                    source.ref,
                    out.error,
                )
            counts["given_up"] += gave_up
            return
        counts["sources"] += 1
        counts["stored"] += out.stored
        counts["dropped"] += out.dropped

    results = await asyncio.gather(*(one(s) for s in picked), return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):  # a database error: logged, the rest of the batch stands
            logger.error("company facts: a source failed: %r", res)
            counts["failed"] += 1
        elif isinstance(res, BaseException):
            raise res
    return FactBatch(**counts, budget_stopped=state["stopped"])
