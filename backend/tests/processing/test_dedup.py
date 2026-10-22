from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.processing.dedup import hamming, mark_duplicates, simhash64

FIX = Path(__file__).parent.parent / "fixtures" / "articles"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_syndicated_copy_is_near_duplicate():
    a = simhash64(_read("press_release_a.txt"))
    b = simhash64(_read("press_release_b_syndicated.txt"))
    assert hamming(a, b) <= 3


def test_unrelated_articles_are_far_apart():
    a, u = simhash64(_read("press_release_a.txt")), simhash64(_read("unrelated.txt"))
    assert hamming(a, u) > 10


def test_short_text_gets_zero():
    assert simhash64("too short to fingerprint") == 0


def test_simhash_fits_signed_bigint():
    v = simhash64(_read("press_release_a.txt"))
    assert -(2**63) <= v < 2**63


async def test_mark_duplicates_points_to_earliest(session, make_document):
    t = datetime(2026, 10, 1, tzinfo=UTC)
    d1 = await make_document(clean_text=_read("press_release_a.txt"), published_at=t)
    d2 = await make_document(
        clean_text=_read("press_release_b_syndicated.txt"), published_at=t + timedelta(days=1)
    )
    assert await mark_duplicates(session, [d1.id, d2.id]) == 1
    assert d1.simhash is not None and d2.simhash is not None
    assert d1.status == "new"
    assert d2.status == "duplicate"
    assert d2.duplicate_of == d1.id


async def test_outside_window_not_marked(session, make_document):
    t = datetime(2026, 10, 1, tzinfo=UTC)
    d1 = await make_document(clean_text=_read("press_release_a.txt"), published_at=t)
    d2 = await make_document(
        clean_text=_read("press_release_b_syndicated.txt"), published_at=t + timedelta(days=20)
    )
    assert await mark_duplicates(session, [d1.id, d2.id]) == 0
    assert d2.status == "new"


async def test_exact_content_hash_marked(session, make_document):
    t = datetime(2026, 10, 1, tzinfo=UTC)
    d1 = await make_document(clean_text="short", content_hash="same", published_at=t)
    d2 = await make_document(clean_text="short", content_hash="same", published_at=t)
    assert await mark_duplicates(session, [d1.id, d2.id]) == 1
    assert d2.status == "duplicate" and d2.duplicate_of == d1.id
    assert d1.simhash == 0


async def test_duplicate_chain_points_to_original(session, make_document):
    t = datetime(2026, 10, 1, tzinfo=UTC)
    text = _read("press_release_a.txt")
    d1 = await make_document(clean_text=text, published_at=t)
    d2 = await make_document(clean_text=text + " Extra.", published_at=t)
    d3 = await make_document(clean_text=text, published_at=t)
    assert await mark_duplicates(session, [d1.id, d2.id, d3.id]) == 2
    assert d2.duplicate_of == d1.id and d3.duplicate_of == d1.id


async def test_non_new_documents_untouched(session, make_document):
    t = datetime(2026, 10, 1, tzinfo=UTC)
    d1 = await make_document(clean_text=_read("press_release_a.txt"), published_at=t)
    d2 = await make_document(
        clean_text=_read("press_release_a.txt"), published_at=t, status="irrelevant"
    )
    assert await mark_duplicates(session, [d1.id, d2.id]) == 0
    assert d2.status == "irrelevant"


async def test_created_at_used_when_published_missing(session, make_document):
    d1 = await make_document(clean_text=_read("press_release_a.txt"))
    d2 = await make_document(clean_text=_read("press_release_b_syndicated.txt"))
    assert await mark_duplicates(session, [d1.id, d2.id]) == 1


# -- the exact-duplicate key includes the title -----------------------


def test_content_hash_includes_title_and_ignores_case_and_spacing():
    from app.processing.normalize import content_hash

    assert content_hash("Snyk raises", "") != content_hash("Cloudsmith ships", "")
    assert content_hash(None, "") != content_hash("A title", "")
    assert content_hash("Same  Title", "Short   body.") == content_hash("same title", "short body.")
    assert content_hash("Same title", "one") != content_hash("Same title", "two")


async def test_empty_text_docs_with_different_titles_not_marked(session, make_document):
    from app.processing.normalize import content_hash

    t = datetime(2026, 10, 1, tzinfo=UTC)
    docs = [
        await make_document(
            title=title, clean_text="", content_hash=content_hash(title, ""), published_at=t
        )
        for title in ("Snyk raises $100M", "Cloudsmith adds Cargo", "JFrog 8-K (2026-10-01)")
    ]
    assert await mark_duplicates(session, [d.id for d in docs]) == 0
    assert all(d.status == "new" and d.duplicate_of is None for d in docs)


async def test_same_title_and_short_text_marked(session, make_document):
    from app.processing.normalize import content_hash

    t = datetime(2026, 10, 1, tzinfo=UTC)
    h = content_hash("Sonatype ships Nexus 4", "New release.")
    d1 = await make_document(
        title="Sonatype ships Nexus 4", clean_text="New release.", content_hash=h, published_at=t
    )
    d2 = await make_document(
        title="Sonatype ships Nexus 4", clean_text="New release.", content_hash=h, published_at=t
    )
    assert await mark_duplicates(session, [d1.id, d2.id]) == 1
    assert d2.status == "duplicate" and d2.duplicate_of == d1.id


async def test_docs_without_title_or_text_never_exact_duplicates(session, make_document):
    t = datetime(2026, 10, 1, tzinfo=UTC)
    d1 = await make_document(title=None, clean_text="", content_hash="blank", published_at=t)
    d2 = await make_document(title=" ", clean_text=None, content_hash="blank", published_at=t)
    assert await mark_duplicates(session, [d1.id, d2.id]) == 0
    assert d2.status == "new"


async def test_normalized_empty_items_with_distinct_titles_are_not_duplicates(
    session, make_source, raw_item_factory, polite_client
):
    # End to end over normalize: GDELT-style items carry a title but no body text.
    from app.processing.normalize import normalize_new_raw_items

    src = await make_source(config={"fetch_fulltext": False})
    for title in ("Snyk raises $100M", "Checkmarx acquires a startup"):
        await raw_item_factory(source_id=src.id, title=title, raw_text=None)
    stats = await normalize_new_raw_items(session, polite_client)
    assert stats.created == 2
    assert await mark_duplicates(session, stats.document_ids) == 0
