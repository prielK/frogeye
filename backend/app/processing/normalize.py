"""Bronze -> Silver: canonical URLs, text extraction, full-text enrichment."""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import trafilatura
from langdetect import DetectorFactory, LangDetectException, detect
from lxml import html as lxml_html
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.http import FetchError, PoliteClient
from app.models import Document, RawItem, Source

logger = logging.getLogger(__name__)

DetectorFactory.seed = 0

TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "_hsenc",
        "_hsmi",
    }
)
SHORT_TEXT_CHARS = 400
SUMMARY_CHARS = 500
TITLE_FALLBACK_CHARS = 120
LANG_MIN_CHARS = 50
LANG_SAMPLE_CHARS = 1000
GOOGLE_NEWS_HOST = "news.google.com"

_HTML_HINT = re.compile(r"<(?:p|div|br|a|/)[\s>/]|</", re.IGNORECASE)
_WS = re.compile(r"\s+")


def _is_tracking(name: str) -> bool:
    low = name.lower()
    return low.startswith("utm_") or low in TRACKING_PARAMS


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    )
    path = parts.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


@dataclass
class ExtractedText:
    text: str
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None


def _get(doc, name: str):
    if doc is None:
        return None
    if isinstance(doc, dict):
        return doc.get(name)
    return getattr(doc, name, None)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _strip_tags(html: str) -> str:
    try:
        return _WS.sub(" ", lxml_html.fromstring(html).text_content()).strip()
    except Exception:  # noqa: BLE001 - malformed markup: fall back to a regex
        return _WS.sub(" ", re.sub(r"<[^>]+>", " ", html)).strip()


def extract_text(html: str, url: str | None = None) -> ExtractedText:
    doc = trafilatura.bare_extraction(html, url=url, favor_precision=True, include_comments=False)
    text = (_get(doc, "text") or "").strip()
    if not text:
        text = _strip_tags(html)
    return ExtractedText(
        text=text,
        title=_get(doc, "title") or None,
        author=_get(doc, "author") or None,
        published_at=_parse_date(_get(doc, "date")),
    )


def looks_like_html(text: str) -> bool:
    return bool(_HTML_HINT.search(text))


def _collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()


def content_hash(title: str | None, text: str | None) -> str:
    """Exact-duplicate key over the lower-cased, whitespace-collapsed title and text.

    The title is part of the key so the many items without body text (GDELT articles, HN link
    stories, EDGAR filings, empty release notes, failed full-text fetches) don't all share
    sha256("") and get marked as duplicates of each other.
    """
    key = _collapse(title or "").lower() + "\n" + _collapse(text or "").lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _detect_lang(text: str) -> str | None:
    if len(text) < LANG_MIN_CHARS:
        return None
    try:
        return detect(text[:LANG_SAMPLE_CHARS])
    except LangDetectException:
        return None


@dataclass
class NormalizeStats:
    created: int = 0
    skipped_existing: int = 0
    fulltext_fetched: int = 0
    document_ids: list[int] = field(default_factory=list)


async def _fetch_fulltext(
    http: PoliteClient, source_config: dict, url: str
) -> ExtractedText | None:
    try:
        resp = await http.get(
            url,
            # Article pages keep robots.txt: honeypot/trap avoidance (ban protection),
            # not compliance.
            respect_robots=True,
            browser_ua=source_config.get("browser_ua", False),
        )
    except FetchError as exc:  # includes RobotsDisallowed / ResponseTooLarge
        logger.info("fulltext fetch failed for %s: %s", url, exc)
        return None
    except Exception:  # noqa: BLE001 - any fetch problem keeps the feed text
        logger.warning("fulltext fetch crashed for %s", url, exc_info=True)
        return None
    if resp.status != 200:
        logger.info("fulltext fetch for %s returned HTTP %s", url, resp.status)
        return None
    extracted = extract_text(resp.text, url)
    return extracted if extracted.text else None


async def _normalize_one(
    session: AsyncSession,
    http: PoliteClient,
    raw: RawItem,
    source_config: dict,
    canonical: str,
    uhash: str,
    can_fetch: bool,
) -> tuple[Document, bool, bool]:
    """Build a Document; returns (document, fetch attempted, fetch produced text)."""
    raw_text = raw.raw_text or ""
    meta = ExtractedText(text="")
    if looks_like_html(raw_text):
        meta = extract_text(raw_text, raw.url)
        text = meta.text
    else:
        text = raw_text.strip()
    summary = _collapse(text)[:SUMMARY_CHARS] or None

    attempted = fetched = False
    host = urlsplit(canonical).netloc
    if (
        len(text) < SHORT_TEXT_CHARS
        and can_fetch
        and source_config.get("fetch_fulltext", True)
        and host != GOOGLE_NEWS_HOST
    ):
        attempted = True
        page = await _fetch_fulltext(http, source_config, raw.url)
        if page is not None:
            fetched = True
            if len(page.text) > len(text):
                text = page.text
            meta = ExtractedText(
                text=text,
                title=meta.title or page.title,
                author=meta.author or page.author,
                published_at=meta.published_at or page.published_at,
            )

    norm = _collapse(text)
    title = raw.title or meta.title or norm[:TITLE_FALLBACK_CHARS] or None
    doc = Document(
        raw_item_id=raw.id,
        source_id=raw.source_id,
        canonical_url=canonical,
        url_hash=uhash,
        title=title,
        published_at=raw.published_at or meta.published_at,
        author=meta.author,
        lang=_detect_lang(norm),
        summary=summary,
        clean_text=text,
        content_hash=content_hash(title, norm),
    )
    return doc, attempted, fetched


async def _url_hash_exists(session: AsyncSession, uhash: str) -> bool:
    return (
        await session.execute(select(Document.id).where(Document.url_hash == uhash))
    ).first() is not None


async def _mark_normalized(session: AsyncSession, raw_id: int) -> None:
    async with session.begin_nested():
        await session.execute(
            update(RawItem).where(RawItem.id == raw_id).values(normalized_at=datetime.now(UTC))
        )


async def normalize_new_raw_items(
    session: AsyncSession, http: PoliteClient, *, fulltext_limit: int = 100
) -> NormalizeStats:
    """Normalize raw items with ``normalized_at IS NULL`` (reset it to NULL to reprocess)."""
    stats = NormalizeStats()
    rows = (
        (
            await session.execute(
                select(RawItem.id).where(RawItem.normalized_at.is_(None)).order_by(RawItem.id)
            )
        )
        .scalars()
        .all()
    )
    # plain dicts: ORM objects are expired whenever a savepoint rolls back
    configs: dict[int, dict] = {}
    fetch_attempts = 0

    for raw_id in rows:
        # get() reloads the row if a savepoint rollback expired it
        raw = await session.get(RawItem, raw_id)
        try:
            canonical = canonicalize_url(raw.url)
            uhash = url_hash(raw.url)
            if await _url_hash_exists(session, uhash):
                stats.skipped_existing += 1
            else:
                config = configs.get(raw.source_id)
                if config is None:
                    source = await session.get(Source, raw.source_id)
                    config = configs[raw.source_id] = dict(source.config or {})
                doc, attempted, fetched = await _normalize_one(
                    session, http, raw, config, canonical, uhash, fetch_attempts < fulltext_limit
                )
                fetch_attempts += attempted
                async with session.begin_nested():
                    session.add(doc)
                    raw.normalized_at = datetime.now(UTC)
                    await session.flush()
                stats.created += 1
                stats.document_ids.append(doc.id)
                if fetched:
                    stats.fulltext_fetched += 1
                continue
        except Exception:  # noqa: BLE001 - one bad item must not abort the batch
            logger.exception("normalize failed for raw_item %s", raw_id)
        # duplicate or failed item: record it as processed so it is not retried
        await _mark_normalized(session, raw_id)
    return stats
