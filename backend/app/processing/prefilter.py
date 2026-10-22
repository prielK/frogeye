"""Deterministic prefilter: which documents mention tracked competitors or industry topics."""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Competitor, Document, DocumentCompetitor, Kit, Source
from app.models.enums import DocStatus, Scope
from app.seed.loader import Keywords, load_keywords

logger = logging.getLogger(__name__)

# why a document was rejected, stored in `documents.prefilter` so ops can see it.
JFROG_ONLY = "jfrog_only"  # it named only JFrog itself (or came from a JFrog-own feed)


@dataclass
class PrefilterResult:
    passed: bool
    competitor_ids: list[int]
    scope_hint: Scope | None
    matched_terms: list[str]
    reason: str | None = None  # set on a rejection with a known cause (JFROG_ONLY)


@dataclass
class PrefilterStats:
    passed: int = 0
    rejected: int = 0


def _alt(terms: list[str], *, ignore_case: bool) -> re.Pattern[str] | None:
    """One alternation regex over terms, with the shared word-boundary guards."""
    terms = [t for t in dict.fromkeys(terms) if t]
    if not terms:
        return None
    # longest first so the most specific alias is reported
    body = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(r"(?<![\w.-])(?:" + body + r")(?![\w-])", re.IGNORECASE if ignore_case else 0)


def _found(pattern: re.Pattern[str] | None, haystack: str) -> list[str]:
    if pattern is None:
        return []
    return [m.group(0) for m in pattern.finditer(haystack)]


@dataclass
class _CompiledCompetitor:
    id: int
    strong: re.Pattern[str] | None
    ambiguous: re.Pattern[str] | None
    negative: re.Pattern[str] | None


class AliasMatcher:
    """JFrog itself (the `self` competitor) is never a document's company.

    Its aliases are not matched and its own feeds do not auto-pass, so a JFrog-only story
    does not pass, and "Sonatype vs JFrog" passes as Sonatype's news. Industry terms still
    count: such a story reaches triage, which rejects news about JFrog itself.
    """

    def __init__(self, competitors: list[Competitor], kits: list[Kit], keywords: Keywords):
        self._self_ids = {c.id for c in competitors if c.is_self}
        # self aliases are only used to explain a rejection, never to tag
        self._self = _alt(
            [a for c in competitors if c.is_self for a in (c.aliases or [])], ignore_case=True
        )
        self._competitors = [
            _CompiledCompetitor(
                id=c.id,
                strong=_alt(list(c.aliases or []), ignore_case=True),
                ambiguous=_alt(list(c.ambiguous_aliases or []), ignore_case=False),
                negative=_alt(list(c.negative_keywords or []), ignore_case=True),
            )
            for c in sorted(competitors, key=lambda c: c.id)
            if not c.is_self
        ]
        self._context = _alt(list(keywords.context_terms), ignore_case=True)
        industry_terms = list(keywords.industry) + [k for kit in kits for k in (kit.keywords or [])]
        self._industry = _alt(industry_terms, ignore_case=True)

    @classmethod
    async def from_db(cls, session: AsyncSession) -> "AliasMatcher":
        competitors = list((await session.execute(select(Competitor))).scalars().all())
        kits = list((await session.execute(select(Kit))).scalars().all())
        return cls(competitors, kits, load_keywords())

    def match(self, *, title: str, text: str, source: Source) -> PrefilterResult:
        haystack = f"{title or ''}\n{text or ''}"
        has_context = self._context is not None and self._context.search(haystack) is not None
        terms: list[str] = []
        matched: set[int] = set()  # competitor ids, never self

        for c in self._competitors:
            strong = _found(c.strong, haystack)
            ambiguous = _found(c.ambiguous, haystack) if has_context else []
            if not strong and not ambiguous:
                continue
            if not strong and c.negative is not None and c.negative.search(haystack):
                continue
            matched.add(c.id)
            terms.extend(strong + ambiguous)

        config = source.config or {}
        own_id = source.competitor_id
        auto_pass = own_id is not None and config.get("auto_pass", True)
        if auto_pass and own_id not in self._self_ids:
            matched.add(own_id)

        industry = _found(self._industry, haystack)
        terms.extend(industry)

        if matched:
            scope: Scope | None = Scope.competitor
        elif industry:
            scope = Scope.industry
        else:
            scope = None

        reason = None
        if scope is None and (
            (auto_pass and own_id in self._self_ids)
            or (self._self is not None and self._self.search(haystack))
        ):
            reason = JFROG_ONLY
        return PrefilterResult(
            passed=scope is not None,
            competitor_ids=sorted(matched),
            scope_hint=scope,
            matched_terms=list(dict.fromkeys(terms)),
            reason=reason,
        )


async def _prefilter_one(
    session: AsyncSession, doc_id: int, matcher: AliasMatcher, sources: dict[int, Source]
) -> bool | None:
    """Returns True if passed, False if rejected, None if skipped."""
    doc = await session.get(Document, doc_id)
    if doc is None or doc.status != DocStatus.new:
        return None
    source = sources.get(doc.source_id)
    if source is None:
        source = sources[doc.source_id] = await session.get(Source, doc.source_id)
    res = matcher.match(title=doc.title or "", text=doc.clean_text or "", source=source)
    async with session.begin_nested():
        if res.passed:
            doc.prefilter = {
                "matched_terms": res.matched_terms,
                "scope_hint": str(res.scope_hint),
            }
            doc.scope_hint = str(res.scope_hint)
            if res.competitor_ids:
                await session.execute(
                    pg_insert(DocumentCompetitor)
                    .values(
                        [{"document_id": doc.id, "competitor_id": i} for i in res.competitor_ids]
                    )
                    .on_conflict_do_nothing()
                )
        else:
            doc.prefilter = {
                "matched_terms": [],
                "scope_hint": None,
                **({"reason": res.reason} if res.reason else {}),
            }
            doc.scope_hint = None
            doc.status = DocStatus.irrelevant
        await session.flush()
    return res.passed


async def prefilter_documents(
    session: AsyncSession, doc_ids: list[int], matcher: AliasMatcher
) -> PrefilterStats:
    stats = PrefilterStats()
    sources: dict[int, Source] = {}
    for doc_id in doc_ids:
        try:
            outcome = await _prefilter_one(session, doc_id, matcher, sources)
        except Exception:  # noqa: BLE001 - one bad doc must not abort the batch
            logger.exception("prefilter failed for document %s", doc_id)
            sources.clear()  # savepoint rollback may have expired cached ORM objects
            continue
        if outcome is True:
            stats.passed += 1
        elif outcome is False:
            stats.rejected += 1
    return stats
