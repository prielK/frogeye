"""Data migration 0003: title-aware content_hash, false duplicates reset to 'new'."""

import hashlib
import importlib.util
from pathlib import Path

from app.processing.normalize import content_hash

MIGRATION = (
    Path(__file__).parents[2] / "migrations" / "versions" / "0003_title_aware_content_hash.py"
)


def _upgrade(sync_conn) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    spec = importlib.util.spec_from_file_location("migration_0003", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(sync_conn)):
        module.upgrade()


async def test_0003_rehashes_and_resets_false_duplicates(connection, session, make_document):
    empty = hashlib.sha256(b"").hexdigest()  # the old key of every text-less document
    first = await make_document(title="Snyk raises $100M", clean_text="", content_hash=empty)
    false_dup = await make_document(
        title="Cloudsmith ships Cargo",
        clean_text=None,
        content_hash=empty,
        status="duplicate",
        duplicate_of=first.id,
    )
    short = await make_document(title="v1.0 notes", clean_text="Bug fixes.", content_hash="x")
    short_dup = await make_document(
        title="v2.0 notes",
        clean_text="Bug fixes.",
        content_hash="x",
        status="duplicate",
        duplicate_of=short.id,
        simhash=0,
    )
    near = await make_document(title="A", clean_text="long text", simhash=12345)
    near_dup = await make_document(
        title="B",
        clean_text="long text, syndicated",
        simhash=12347,
        status="duplicate",
        duplicate_of=near.id,
    )
    await session.flush()

    await connection.run_sync(_upgrade)
    for d in (first, false_dup, short, short_dup, near, near_dup):
        await session.refresh(d)

    assert first.content_hash == content_hash("Snyk raises $100M", "")
    assert false_dup.content_hash == content_hash("Cloudsmith ships Cargo", None)
    # text-less "duplicates" are reset for the next run's sweep (status new, prefilter NULL)
    assert (false_dup.status, false_dup.duplicate_of, false_dup.prefilter) == ("new", None, None)
    # short exact-hash duplicate whose titles differ: no longer a match, reset too
    assert (short_dup.status, short_dup.duplicate_of) == ("new", None)
    # SimHash near-duplicates (both fingerprints non-zero) keep their mark
    assert (near_dup.status, near_dup.duplicate_of) == ("duplicate", near.id)
