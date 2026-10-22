"""JFrog knowledge base: a polite crawl of jfrog.com product pages plus a static context pack.

Each page's text comes from the article extractor every collector uses, unless its
jfrog_kb.yaml entry says `extract: sections`: a product landing page whose text sits in many
short feature blocks. There the article extractor keeps only the densest paragraphs, which
on /runtime/ were the customer testimonials (about another product) and on
/advanced-security/ the closing call to action (the KB crawl note), so neither page could
ground a claim about its product. `_section_text` keeps every content block in page order
instead, less navigation, forms, footers and testimonials.
"""

import logging
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml
from lxml import etree
from lxml import html as lxml_html
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime.accounted import AccountedLLM, LLMError
from app.agents.runtime.context import RunCtx
from app.collectors.attribution import display_title, is_url
from app.core.http import FetchError, PoliteClient
from app.models.enums import RetrievalKind
from app.models.rag import JfrogKbDoc, RetrievalItem
from app.processing.normalize import ExtractedText, content_hash, extract_text
from app.rag.indexing import index_kb_doc

log = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
MIN_TEXT_CHARS = 200
PENDING_HASH = ""  # a new page's hash until its first successful index
_EXTRACT_MODES = ("article", "sections")

# `_section_text`: what starts a new line; what is never product text (with its subtree);
# class/id words that mark a testimonial or other page furniture; a quoted line of 8+ words
# (a testimonial in markup the words do not catch).
_BLOCKS = frozenset(
    "address article aside blockquote body caption dd details dialog div dl dt fieldset "
    "figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section "
    "summary table tbody td tfoot th thead tr ul".split()
)
_SKIPPED = frozenset(
    "aside blockquote button form iframe nav noscript script select style svg template "
    "textarea".split()
)
_FURNITURE = ("testimonial", "quote", "review", "cookie", "consent", "newsletter", "breadcrumb")
_QUOTED_LINE = re.compile(r'^[“"„«][^\n]*[”"»]$')
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class KbStats:
    fetched: int = 0  # pages fetched and extracted
    changed: int = 0  # new or content changed
    indexed: int = 0  # re-embedded (changed pages and pages that had no chunks)
    skipped: int = 0  # robots / non-200 / errors / empty pages / failed indexing


@cache
def load_jfrog_context() -> str:
    """The static JFrog brief used as a cache-friendly prompt prefix."""
    return (SEED_DIR / "jfrog_context.md").read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class _KbPage:
    url: str
    name: str
    extract: str = "article"  # or "sections" (the module docstring)


@cache
def _kb_pages() -> tuple[_KbPage, ...]:
    data = yaml.safe_load((SEED_DIR / "jfrog_kb.yaml").read_text(encoding="utf-8"))
    pages = []
    for entry in data["pages"]:
        mode = entry.get("extract", "article")
        if mode not in _EXTRACT_MODES:
            raise ValueError(f"jfrog_kb.yaml: unknown extract {mode!r} for {entry['url']}")
        pages.append(_KbPage(entry["url"], entry["name"], mode))
    return tuple(pages)


def load_kb_urls() -> list[str]:
    return [page.url for page in _kb_pages()]


def kb_title(title: str | None, url: str) -> str:
    """A KB page's title for readers and prompts: the crawl stored each page's URL as
    its title, so a URL (or no title) becomes the page's name in jfrog_kb.yaml ("Curation"),
    else "Untitled page on <host>". Never a scheme."""
    if title and title.strip() and not is_url(title):
        return title.strip()
    name = next((page.name for page in _kb_pages() if page.url == url), None)
    return name or display_title(None, url)


def _skipped(el: lxml_html.HtmlElement) -> bool:
    """Never product text: navigation, forms, scripts and the like, hidden elements, the
    site's own header and footer (one inside an article or section is a card's), and
    testimonials or other furniture by their class or id words."""
    if el.tag in _SKIPPED or el.get("hidden") is not None or el.get("aria-hidden") == "true":
        return True
    if el.tag in ("header", "footer"):
        return next(el.iterancestors("article", "section", "main"), None) is None
    words = _WORD.findall(f"{el.get('class', '')} {el.get('id', '')}".lower())
    return any(word.startswith(_FURNITURE) for word in words)


def _section_text(html: str) -> ExtractedText:
    """A landing page's text: every content block in page order, one line each (inline
    markup joined), less what `_skipped` drops and quoted testimonial lines; a repeated
    line (a carousel's clones) once."""
    try:
        root = lxml_html.fromstring(html)
    except (ValueError, etree.ParserError):  # an encoding declaration, an empty document
        return ExtractedText(text="")
    lines: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        line = " ".join("".join(pending).split())
        pending.clear()
        if line and not (_QUOTED_LINE.match(line) and len(line.split()) >= 8):
            lines.append(line)

    def walk(el: lxml_html.HtmlElement) -> None:
        if not isinstance(el.tag, str) or _skipped(el):  # comments too; tails stay
            return
        block = el.tag in _BLOCKS
        if block:
            flush()
        pending.append(" " if el.tag == "br" else el.text or "")
        for child in el:
            walk(child)
            pending.append(child.tail or "")
        if block:
            flush()

    body = root.find(".//body")
    walk(body if body is not None else root)
    flush()
    title = (root.findtext(".//title") or "").strip() or None
    return ExtractedText(text="\n".join(dict.fromkeys(lines)), title=title)


def _extract(html: str, url: str, mode: str) -> ExtractedText:
    if mode == "sections":
        extracted = _section_text(html)
        if len(extracted.text) >= MIN_TEXT_CHARS:
            return extracted
    return extract_text(html, url)


async def _fetch(http: PoliteClient, page: _KbPage) -> tuple[str, str] | None:
    """(title, text) of a readable page, or None when it must be skipped."""
    url = page.url
    try:
        resp = await http.get(url, respect_robots=True)
    except FetchError as e:  # robots, paused host, oversize, blocked address, network
        log.warning("kb skip %s: %s", url, e)
        return None
    if resp.status != 200:
        log.warning("kb skip %s: HTTP %s", url, resp.status)
        return None
    extracted = _extract(resp.text, url, page.extract)
    text = extracted.text.strip()
    if len(text) < MIN_TEXT_CHARS:
        log.warning("kb skip %s: no readable text", url)
        return None
    return (extracted.title or url).strip(), text


async def _upsert(
    session_factory: async_sessionmaker[AsyncSession], url: str, title: str, text: str
) -> tuple[int, str, bool] | None:
    """Store the page's title and text; return (id, digest, changed) when it needs indexing.

    The new content_hash is NOT written here. It is committed by `_mark_indexed` only
    after `index_kb_doc` succeeds, so a page whose indexing failed still looks changed on the
    next crawl. A page whose hash matches but that has no jfrog_kb retrieval items (left by a
    failure before this rule) is returned too, with changed=False."""
    digest = content_hash(title, text)
    async with session_factory() as session:
        kb = await session.scalar(select(JfrogKbDoc).where(JfrogKbDoc.url == url))
        if kb is None:
            kb = JfrogKbDoc(url=url, title=title, text=text, content_hash=PENDING_HASH)
            session.add(kb)
        else:  # a no-op unless an earlier failed index left newer text behind
            kb.title, kb.text = title, text
        await session.flush()
        kb_id, unchanged = kb.id, kb.content_hash == digest
        has_chunks = unchanged and await session.scalar(
            select(
                exists().where(
                    RetrievalItem.kind == RetrievalKind.jfrog_kb, RetrievalItem.ref_id == kb_id
                )
            )
        )
        await session.commit()
    if has_chunks:
        return None
    return kb_id, digest, not unchanged


async def _mark_indexed(
    session_factory: async_sessionmaker[AsyncSession], kb_id: int, digest: str
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(JfrogKbDoc).where(JfrogKbDoc.id == kb_id).values(content_hash=digest)
        )
        await session.commit()


async def build_kb(
    session_factory: async_sessionmaker[AsyncSession],
    llm: AccountedLLM,
    http: PoliteClient,
    *,
    ctx: RunCtx,
    urls: list[str] | None = None,
) -> KbStats:
    """Fetch each KB page (robots respected), upsert by URL, re-index changed pages and
    pages that have no chunks yet.

    Failures are skipped and counted: an LLMError while indexing one page is a skip and the
    crawl goes on, and that page is indexed again on the next crawl. BudgetExceeded
    stops the crawl. Fetches are sequential so PoliteClient's per-host interval keeps the
    crawl at <= 1 request/second."""
    fetched = changed = indexed = skipped = 0
    known = {page.url: page for page in _kb_pages()}
    for url in urls if urls is not None else load_kb_urls():
        page = await _fetch(http, known.get(url) or _KbPage(url, url))
        if page is None:
            skipped += 1
            continue
        fetched += 1
        todo = await _upsert(session_factory, url, *page)
        if todo is None:
            continue
        kb_id, digest, is_change = todo
        if is_change:
            changed += 1
        try:
            written = await index_kb_doc(session_factory, llm, kb_id, ctx=ctx)
        except LLMError as exc:
            log.warning("kb skip %s: indexing failed (%s); retried on the next crawl", url, exc)
            skipped += 1
            continue
        if written:
            await _mark_indexed(session_factory, kb_id, digest)
            indexed += 1
    return KbStats(fetched, changed, indexed, skipped)
