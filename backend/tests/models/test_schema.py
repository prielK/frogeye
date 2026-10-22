import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.models import Document, RawItem, Source  # noqa: F401

EXPECTED = {
    "competitors",
    "capability_categories",
    "capabilities",
    "kits",
    "sources",
    "users",
    "raw_items",
    "page_snapshots",
    "documents",
    "document_competitors",
    "pipeline_runs",
    "metric_points",
    "job_postings",
    "company_facts",
    "competitor_products",
    "competitor_briefs",
    "product_briefs",
}


async def test_migration_creates_tables(connection):
    names = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert EXPECTED <= names


async def test_vector_extension_installed(session):
    rows = (await session.execute(text("select extname from pg_extension"))).scalars().all()
    assert "vector" in rows


async def test_raw_item_unique_per_source_external_id(session, make_source):
    src = await make_source()
    session.add_all(
        [
            RawItem(source_id=src.id, url="u", external_id="x", content_hash="h", payload={}),
            RawItem(source_id=src.id, url="u", external_id="x", content_hash="h", payload={}),
        ]
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_document_url_hash_unique(session, make_document):
    await make_document(url_hash="abc")
    with pytest.raises(IntegrityError):
        await make_document(url_hash="abc")


async def test_enum_check_constraint_rejects_bad_value(session, make_source):
    with pytest.raises(IntegrityError):
        await make_source(kind="myspace")
