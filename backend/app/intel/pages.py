"""Page-watch snapshots: stored only when a watched page's text changes."""

import difflib
import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PageSnapshot, Source
from app.models.enums import PageKind

DIFF_CAP_CHARS = 20_000  # the stored diff; the full text is kept beside it
DIFF_CONTEXT_LINES = 2
_PAGE_KINDS = {m.value for m in PageKind}


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unified_diff(old: str, new: str) -> str:
    """A unified diff of two page texts without the ---/+++ file headers, capped."""
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(), lineterm="", n=DIFF_CONTEXT_LINES
    )
    body = "\n".join(line for line in lines if not line.startswith(("--- ", "+++ ")))
    return body[:DIFF_CAP_CHARS]


def added_text(old: str, new: str) -> str:
    """Every line `new` adds to `old`, the rule `unified_diff` reads, never cut at its cap: what a
    later snapshot's facts are screened and read on."""
    a, b = old.splitlines(), new.splitlines()
    added = [
        line
        for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes()
        if tag in ("replace", "insert")
        for line in b[j1:j2]
    ]
    return "\n".join(added).strip()


async def store_page_snapshot(
    session: AsyncSession, source: Source, text: str, *, now: datetime
) -> PageSnapshot | None:
    """Store `text` as a new snapshot of the source's page when it differs from the last one.

    Returns the new snapshot, or None when the text is unchanged or empty (an empty fetch
    never replaces what we hold). The first snapshot is a baseline: no diff, never judged.
    """
    text = text.strip()
    if not text:
        return None
    digest = text_digest(text)
    last = (
        await session.execute(
            select(PageSnapshot)
            .where(PageSnapshot.source_id == source.id)
            .order_by(PageSnapshot.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is not None and last.text_hash == digest:
        return None
    page = str((source.config or {}).get("page") or "")
    snap = PageSnapshot(
        source_id=source.id,
        fetched_at=now,
        text_hash=digest,
        text=text,
        diff_from_prev=unified_diff(last.text, text) if last is not None else None,
        page_kind=page if page in _PAGE_KINDS else None,
    )
    session.add(snap)
    await session.flush()
    return snap
