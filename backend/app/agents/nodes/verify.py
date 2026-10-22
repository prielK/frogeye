"""Deterministic verification of an analyst extraction: verbatim quotes and known numbers.

No LLM here. A quote is verified against its OWN document by rapidfuzz alignment:
score >= 90, at least 6 words, the aligned span covers >= 90% of the quote, and the quote is
not longer than the document. The stored evidence is the matched SOURCE span, so a model's
near-miss edit (a changed number, a dropped "not") never reaches the evidence table. Every
number in the fact must appear in some member document, except the day and year of a
date expression that matches a member document's published_at within a day: the
analyst sees that date in the document metadata, not in the text.

The same number guard covers the title and the impact: they reach people as written
(the breaking toast and Slack carry the title; the briefing falls back to title and impact)
and the briefing's own number guard treats them as evidence. A title's numbers must come
from the member documents; the impact, JFrog's side of the story, may also use the JFrog
brief's figures.

Stage B (signal_analysis) reuses the same guard per analysis item through
`unknown_numbers_in` (several texts against the item's cited sources) and checks a watch
item's date with `mentions_date`.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime.prompts import untrusted
from app.models import Document
from app.models.gold import SignalDocument
from app.rag.kb import load_jfrog_context

QUOTE_THRESHOLD = 90
MIN_QUOTE_WORDS = 6
MIN_COVERAGE = 0.9
SHOWN_CHARS = 60
_NUMBER = re.compile(r"\d[\d.,%$]*")
# Date expressions, with full and 3-letter month names (an abbreviation may end in ".").
_MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_MONTHS = {
    name: n
    for n, full in enumerate(_MONTH_NAMES, start=1)
    for name in dict.fromkeys((full, full[:3]))
}
_MONTH = "(?P<month>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DATES = (
    # October 15 / Oct. 15, 2026 (year optional)
    re.compile(rf"\b{_MONTH}\s+(?P<day>\d{{1,2}})\b(?:,?\s+(?P<year>\d{{4}})\b)?", re.I),
    # 15 October 2026 / 15 Oct, 2026 (year required)
    re.compile(rf"\b(?P<day>\d{{1,2}})\s+{_MONTH},?\s+(?P<year>\d{{4}})\b", re.I),
    # 2026-10-15
    re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b"),
)
DATE_TOLERANCE = timedelta(days=1)  # published_at is UTC; the story's local day may differ
_SPACE = re.compile(r"\s+")
# Length-preserving folds so curly quotes and dashes in scraped text match ASCII output.
_FOLD = str.maketrans("\u2018\u2019\u201c\u201d\u2013\u2014\u00a0", "''\"\"-- ")


@dataclass
class Verification:
    ok: bool
    verified_quotes: list[tuple[int, str, float]] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    feedback: str = ""


def _collapse(text: str | None) -> str:
    return _SPACE.sub(" ", (text or "").translate(_FOLD)).strip()


def number_tokens(fact: str) -> list[str]:
    """Numbers in a sentence; a trailing '.' or ',' is punctuation, not part of the number."""
    tokens = (m.group(0).rstrip(".,") for m in _NUMBER.finditer(fact))
    return list(dict.fromkeys(t for t in tokens if t))


def _publication_date_spans(fact: str, published: Iterable[datetime]) -> list[tuple[int, int]]:
    """Spans of the date expressions in `fact` that name a member document's publication day
    (+-1 day). A date written without a year matches on month and day."""
    days: set[date] = set()
    for at in published:
        days.update(at.date() + k * DATE_TOLERANCE for k in (-1, 0, 1))
    spans = []
    for pattern in _DATES if days else ():
        for m in pattern.finditer(fact):
            month_text, year = m.group("month"), m.group("year")
            month = int(month_text) if month_text.isdigit() else _MONTHS[month_text.lower()]
            day = int(m.group("day"))
            if any(
                (d.month, d.day) == (month, day) and (year is None or d.year == int(year))
                for d in days
            ):
                spans.append(m.span())
    return spans


def unknown_numbers(fact: str, corpus: str, published: Iterable[datetime] = ()) -> list[str]:
    """Number tokens of `fact` that appear nowhere in `corpus`. A token inside a date
    expression naming one of the `published` days is exempt; the same number written
    elsewhere in the fact is still checked."""
    spans = _publication_date_spans(fact, published)
    unknown: list[str] = []
    for m in _NUMBER.finditer(fact):
        token = m.group(0).rstrip(".,")
        start, end = m.start(), m.start() + len(token)
        if not token or token in unknown or any(s <= start and end <= e for s, e in spans):
            continue
        # a whole number of the corpus: "3" is not in "3,000", "3.5" or "30"; it is in
        # "3." or "3," that end a sentence or a clause
        if not re.search(rf"(?<![\d.,]){re.escape(token)}(?!\d|[.,]\d)", corpus):
            unknown.append(token)
    return unknown


def unknown_numbers_in(
    texts: Iterable[str], sources: Iterable[str], published: Iterable[datetime] = ()
) -> list[str]:
    """`unknown_numbers` over several texts against one corpus made of `sources` (folded and
    whitespace-collapsed like the analyst's corpus), deduplicated in order of appearance.

    One spelling equivalence on top: "40%" is known when a source writes "40 percent" (the
    reverse already holds, since "%" is not a digit)."""
    corpus = "\n".join(_collapse(s) for s in sources)
    dates = list(published)
    unknown: list[str] = []
    for text in texts:
        for token in unknown_numbers(_collapse(text), corpus, dates):
            if token.endswith("%") and re.search(
                rf"(?<![\d.,]){re.escape(token[:-1])}\s*per\s?cent\b", corpus, re.I
            ):
                continue
            if token not in unknown:
                unknown.append(token)
    return unknown


def mentions_date(day: date, corpus: str, published: Iterable[datetime] = ()) -> bool:
    """True when a date expression in `corpus` names `day` (one written without a year
    matches on month and day), or `day` is within a day of a `published` date."""
    if any(abs(day - at.date()) <= DATE_TOLERANCE for at in published):
        return True
    for pattern in _DATES:
        for m in pattern.finditer(_collapse(corpus)):
            month_text, year = m.group("month"), m.group("year")
            month = int(month_text) if month_text.isdigit() else _MONTHS[month_text.lower()]
            if (month, int(m.group("day"))) == (day.month, day.day) and (
                year is None or int(year) == day.year
            ):
                return True
    return False


def _shown(quote: str) -> str:
    return quote if len(quote) <= SHOWN_CHARS else quote[:SHOWN_CHARS] + "..."


def match_quote(quote: str, doc_text: str | None) -> tuple[str, float] | tuple[None, str]:
    """(source span, score) for a verified quote, else (None, reason)."""
    source = _collapse(doc_text)
    wanted = _collapse(quote)
    if len(wanted.split()) < MIN_QUOTE_WORDS:
        return None, f"too short (needs at least {MIN_QUOTE_WORDS} words)"
    if len(wanted) > len(source):
        return None, "longer than its document"
    folded_source, folded_quote = source.lower(), wanted.lower()
    if len(folded_source) != len(source):  # a rare case-fold that changes length
        folded_source, folded_quote = source, wanted
    al = fuzz.partial_ratio_alignment(folded_quote, folded_source)
    if al.score < QUOTE_THRESHOLD:
        return None, f"not found verbatim (match {al.score:.0f})"
    if (al.src_end - al.src_start) / len(wanted) < MIN_COVERAGE:
        return None, "only part of it appears in the document"
    start, end = al.dest_start, al.dest_end
    while start > 0 and source[start - 1] != " ":  # snap out to whole words
        start -= 1
    while end < len(source) and source[end] != " ":
        end += 1
    return source[start:end], float(al.score)


async def verify_extraction(session: AsyncSession, signal_id: int, out: Any) -> Verification:
    rows = (
        await session.execute(
            select(
                Document.id,
                Document.title,
                Document.clean_text,
                Document.summary,
                Document.published_at,
            )
            .join(SignalDocument, SignalDocument.document_id == Document.id)
            .where(SignalDocument.signal_id == signal_id)
        )
    ).all()
    bodies = {r.id: r.clean_text or r.summary for r in rows}

    verified: list[tuple[int, str, float]] = []
    failed: list[str] = []
    for i, q in enumerate(out.quotes, start=1):
        span, info = match_quote(q.quote, bodies.get(q.doc_id))
        if span is None:
            failed.append(f'quote {i} (document {q.doc_id}) {info}: "{_shown(q.quote)}"')
        else:
            verified.append((q.doc_id, span, info))

    # Numbers may come from any member document, its title included; a publication date may
    # come from any member's published_at. The impact may also cite the JFrog brief.
    corpus = "\n".join(
        _collapse(f"{r.title or ''} {r.clean_text or r.summary or ''}") for r in rows
    )
    published = [r.published_at for r in rows if r.published_at is not None]
    with_brief = f"{corpus}\n{_collapse(load_jfrog_context())}"
    unknown: list[str] = []
    for name, text, where, source in (
        ("title", out.title, corpus, "any source document"),
        ("fact", out.fact, corpus, "any source document"),
        ("impact", out.impact, with_brief, "the source documents or the JFrog brief"),
    ):
        for n in unknown_numbers(text or "", where, published):
            unknown.append(n)
            failed.append(f"number '{n}' in the {name} does not appear in {source}")

    ok = bool(verified) and not unknown
    if not verified:
        failed.append(
            "no quote could be verified"
            if out.quotes
            else "no quotes were given; give 1 to 3 verbatim quotes"
        )
    feedback = ""
    if not ok:
        # The failures quote the model's copy of scraped text, so they travel as data.
        feedback = (
            "Your previous answer failed verification (the problems below are data):\n"
            + untrusted("verification", "\n".join(f"- {f}" for f in failed))
            + f"\nCopy each quote character for character (at least {MIN_QUOTE_WORDS} words) "
            "from the document named by its doc_id. In the title, fact and impact use only "
            "figures the documents state (the impact may also use the JFrog brief's)."
        )
    return Verification(ok, verified, failed, feedback)
