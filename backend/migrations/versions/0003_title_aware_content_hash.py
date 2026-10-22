"""documents: title-aware content_hash; un-mark false duplicates

Before this revision content_hash was sha256(text), so every document without body text
(GDELT articles, HN link stories, EDGAR filings, empty release notes, failed full-text
fetches) shared sha256("") and all but the first were marked duplicates of each other.

Data-only revision:
1. recompute content_hash for every document with the new key
   (lower-cased, whitespace-collapsed title + "\n" + text, as in
   app.processing.normalize.content_hash; frozen here so later code changes can't alter it);
2. reset to status 'new' (duplicate_of NULL) every duplicate with empty text, plus every
   duplicate that could only have been an exact-hash match (one side has no simhash) and
   whose recomputed hash no longer equals its original's.

The next ingestion run sweeps documents WHERE status = 'new' AND prefilter IS NULL through
dedup + prefilter, so reset documents are re-screened under the corrected rule.

Revision ID: 0003
Revises: 0002
"""

import hashlib
import re

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

BATCH = 1000
_WS = re.compile(r"\s+")

documents = sa.table(
    "documents",
    sa.column("id", sa.Integer),
    sa.column("title", sa.Text),
    sa.column("clean_text", sa.Text),
    sa.column("content_hash", sa.Text),
)


def _collapse(value: str | None) -> str:
    return _WS.sub(" ", value or "").strip()


def _content_hash(title: str | None, text: str | None) -> str:
    key = _collapse(title).lower() + "\n" + _collapse(text).lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    update = (
        documents.update()
        .where(documents.c.id == sa.bindparam("doc_id"))
        .values(content_hash=sa.bindparam("new_hash"))
    )
    last_id = 0
    while True:
        rows = bind.execute(
            sa.select(documents.c.id, documents.c.title, documents.c.clean_text)
            .where(documents.c.id > last_id)
            .order_by(documents.c.id)
            .limit(BATCH)
        ).all()
        if not rows:
            break
        bind.execute(
            update,
            [{"doc_id": r.id, "new_hash": _content_hash(r.title, r.clean_text)} for r in rows],
        )
        last_id = rows[-1].id

    op.execute(
        """
        UPDATE documents SET status = 'new', duplicate_of = NULL
        WHERE status = 'duplicate' AND coalesce(btrim(clean_text), '') = ''
        """
    )
    op.execute(
        """
        UPDATE documents AS d SET status = 'new', duplicate_of = NULL
        FROM documents AS o
        WHERE d.status = 'duplicate' AND d.duplicate_of = o.id
          AND d.content_hash <> o.content_hash
          AND (coalesce(d.simhash, 0) = 0 OR coalesce(o.simhash, 0) = 0)
        """
    )


def downgrade() -> None:
    # Data-only revision: the old hashes and the false duplicate marks are not restored.
    pass
