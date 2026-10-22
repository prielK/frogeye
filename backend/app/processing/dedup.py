"""Near-duplicate detection: 64-bit SimHash over word 3-shingles."""

import re
from datetime import timedelta
from hashlib import blake2b

from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import BIT
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document
from app.models.enums import DocStatus

MIN_WORDS = 50
SHINGLE_SIZE = 3
_TOKEN = re.compile(r"\w+")


def simhash64(text: str) -> int:
    """Signed 64-bit SimHash (fits BIGINT); 0 for texts under MIN_WORDS words."""
    tokens = _TOKEN.findall(text.lower())
    if len(tokens) < MIN_WORDS:
        return 0
    votes = [0] * 64
    for i in range(len(tokens) - SHINGLE_SIZE + 1):
        shingle = " ".join(tokens[i : i + SHINGLE_SIZE])
        h = int.from_bytes(blake2b(shingle.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            votes[bit] += 1 if (h >> bit) & 1 else -1
    fingerprint = sum(1 << bit for bit in range(64) if votes[bit] > 0)
    return fingerprint - 2**64 if fingerprint >= 2**63 else fingerprint


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & (2**64 - 1)).bit_count()


async def mark_duplicates(
    session: AsyncSession,
    doc_ids: list[int],
    *,
    window_days: int = 14,
    max_distance: int = 3,
) -> int:
    """Fingerprint ``doc_ids``, then mark 'new' ones that duplicate an earlier document.

    A document is a duplicate of an earlier (lower id), non-duplicate document whose
    reference time (published_at, else created_at) is within ``window_days`` and that
    has the same content_hash or a non-zero simhash within ``max_distance`` bits.
    ``duplicate_of`` points to the earliest such document. A document with neither title
    nor text has nothing to compare, so it is never an exact-hash duplicate.

    Postgres finds the earliest match per document (XOR + bit_count for the Hamming
    distance), so only ids cross the wire; banded LSH (bucketing simhash bands) is the
    path for much larger corpora.
    """
    if not doc_ids:
        return 0
    docs = (
        (
            await session.execute(
                select(Document).where(Document.id.in_(doc_ids)).order_by(Document.id)
            )
        )
        .scalars()
        .all()
    )
    for doc in docs:
        doc.simhash = simhash64(doc.clean_text or "")
    await session.flush()

    ref = func.coalesce(Document.published_at, Document.created_at)
    window = timedelta(days=window_days)
    marked = 0
    for doc in docs:  # ascending id, so earlier marks are visible to later candidates
        if doc.status != DocStatus.new:
            continue
        matches = []
        if (doc.title or "").strip() or (doc.clean_text or "").strip():
            matches.append(Document.content_hash == doc.content_hash)
        if doc.simhash:
            distance = func.bit_count(cast(Document.simhash.op("#")(doc.simhash), BIT(64)))
            matches.append(and_(Document.simhash != 0, distance <= max_distance))
        if not matches:
            continue
        center = doc.published_at or doc.created_at
        original = await session.scalar(
            select(Document.id)
            .where(
                Document.id < doc.id,
                Document.status != DocStatus.duplicate,
                ref.between(center - window, center + window),
                or_(*matches),
            )
            .order_by(Document.id)
            .limit(1)
        )
        if original is not None:
            doc.status = DocStatus.duplicate.value
            doc.duplicate_of = original
            marked += 1
            await session.flush()
    return marked
