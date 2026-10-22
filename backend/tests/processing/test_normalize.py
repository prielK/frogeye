from pathlib import Path

import httpx
import respx
from sqlalchemy import select

from app.models import Document
from app.processing.normalize import (
    canonicalize_url,
    extract_text,
    normalize_new_raw_items,
    url_hash,
)

FIX = Path(__file__).parent.parent / "fixtures" / "articles"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_canonicalize_strips_tracking():
    a = "https://Example.com/post/?utm_source=x&b=2&a=1#frag"
    b = "https://example.com/post?a=1&b=2&fbclid=zzz"
    assert canonicalize_url(a) == canonicalize_url(b) == "https://example.com/post?a=1&b=2"
    assert url_hash(a) == url_hash(b)


def test_canonicalize_keeps_root_slash():
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_canonicalize_keeps_scheme():
    assert canonicalize_url("HTTP://Example.com/x") == "http://example.com/x"


def test_extract_text_drops_boilerplate():
    t = extract_text(_read("article.html"))
    assert "Cloudsmith raises" in t.text
    assert "Subscribe to our newsletter" not in t.text


def _mock_page(url: str, body: str, host: str = "https://news.test"):
    respx.get(f"{host}/robots.txt").mock(return_value=httpx.Response(404))
    return respx.get(url).mock(return_value=httpx.Response(200, text=body))


@respx.mock
async def test_short_summary_triggers_fulltext_fetch(
    session, make_source, raw_item_factory, polite_client
):
    src = await make_source()
    await raw_item_factory(
        source_id=src.id,
        url="https://news.test/story?utm_source=x",
        raw_text="Short teaser.",
        title="A story",
    )
    route = _mock_page("https://news.test/story?utm_source=x", _read("short_summary_page.html"))
    stats = await normalize_new_raw_items(session, polite_client)
    assert route.called
    assert stats.created == 1 and stats.fulltext_fetched == 1
    doc = (await session.execute(select(Document))).scalar_one()
    assert "registry for machine learning models" in doc.clean_text
    assert doc.summary == "Short teaser."
    assert doc.canonical_url == "https://news.test/story"
    assert stats.document_ids == [doc.id]


@respx.mock
async def test_fulltext_limit_respected(session, make_source, raw_item_factory, polite_client):
    src = await make_source()
    respx.get("https://news.test/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get(url__regex=r"https://news\.test/s\d").mock(
        return_value=httpx.Response(200, text=_read("short_summary_page.html"))
    )
    for i in range(3):
        await raw_item_factory(source_id=src.id, url=f"https://news.test/s{i}", raw_text="Tiny.")
    stats = await normalize_new_raw_items(session, polite_client, fulltext_limit=1)
    assert route.call_count == 1
    assert stats.created == 3 and stats.fulltext_fetched == 1


@respx.mock
async def test_existing_url_hash_skipped(session, make_source, raw_item_factory, polite_client):
    src = await make_source(config={"fetch_fulltext": False})
    await raw_item_factory(source_id=src.id, url="https://news.test/a?utm_source=x", raw_text="One")
    await raw_item_factory(source_id=src.id, url="https://news.test/a/", raw_text="Two")
    stats = await normalize_new_raw_items(session, polite_client)
    assert stats.created == 1 and stats.skipped_existing == 1
    stats2 = await normalize_new_raw_items(session, polite_client)
    assert stats2.created == 0 and stats2.skipped_existing == 0


@respx.mock
async def test_lang_detected(session, make_source, raw_item_factory, polite_client):
    src = await make_source()
    await raw_item_factory(
        source_id=src.id, url="https://news.test/en", raw_text=_read("article.html")
    )
    await normalize_new_raw_items(session, polite_client)
    doc = (await session.execute(select(Document))).scalar_one()
    assert doc.lang == "en"


@respx.mock
async def test_google_news_and_disabled_sources_not_fetched(
    session, make_source, raw_item_factory, polite_client
):
    gsrc = await make_source()
    await raw_item_factory(
        source_id=gsrc.id, url="https://news.google.com/rss/articles/abc", raw_text="Tiny."
    )
    off = await make_source(config={"fetch_fulltext": False})
    await raw_item_factory(source_id=off.id, url="https://news.test/off", raw_text="Tiny.")
    stats = await normalize_new_raw_items(session, polite_client)
    assert stats.created == 2 and stats.fulltext_fetched == 0


@respx.mock
async def test_fetch_failure_keeps_feed_text(session, make_source, raw_item_factory, polite_client):
    src = await make_source()
    await raw_item_factory(source_id=src.id, url="https://news.test/bad", raw_text="Tiny.")
    respx.get("https://news.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://news.test/bad").mock(return_value=httpx.Response(404))
    stats = await normalize_new_raw_items(session, polite_client)
    assert stats.created == 1 and stats.fulltext_fetched == 0
    doc = (await session.execute(select(Document))).scalar_one()
    assert doc.clean_text == "Tiny."
    assert doc.title == "Tiny."


@respx.mock
async def test_short_html_summary_falls_back_to_tag_strip(
    session, make_source, raw_item_factory, polite_client
):
    src = await make_source(config={"fetch_fulltext": False})
    await raw_item_factory(
        source_id=src.id, url="https://news.test/h", raw_text="<p>Hi <b>there</b></p>"
    )
    await normalize_new_raw_items(session, polite_client)
    doc = (await session.execute(select(Document))).scalar_one()
    assert doc.clean_text == "Hi there"


@respx.mock
async def test_second_run_processes_nothing(session, make_source, raw_item_factory, polite_client):
    src = await make_source(config={"fetch_fulltext": False})
    await raw_item_factory(source_id=src.id, url="https://news.test/a", raw_text="One")
    await raw_item_factory(source_id=src.id, url="https://news.test/a/", raw_text="Two")
    s1 = await normalize_new_raw_items(session, polite_client)
    assert (s1.created, s1.skipped_existing) == (1, 1)
    s2 = await normalize_new_raw_items(session, polite_client)
    assert (s2.created, s2.skipped_existing, s2.document_ids) == (0, 0, [])


@respx.mock
async def test_failed_item_not_retried(
    session, make_source, raw_item_factory, polite_client, monkeypatch
):
    from app.processing import normalize as mod

    src = await make_source(config={"fetch_fulltext": False})
    await raw_item_factory(source_id=src.id, url="https://news.test/ok1", raw_text="One")
    await raw_item_factory(source_id=src.id, url="https://news.test/boom", raw_text="Two")
    await raw_item_factory(source_id=src.id, url="https://news.test/ok2", raw_text="Three")
    real = mod._normalize_one

    async def flaky(session_, http, raw, *a, **kw):
        if raw.url.endswith("boom"):
            raise RuntimeError("boom")
        return await real(session_, http, raw, *a, **kw)

    monkeypatch.setattr(mod, "_normalize_one", flaky)
    s1 = await normalize_new_raw_items(session, polite_client)
    assert s1.created == 2
    monkeypatch.setattr(mod, "_normalize_one", real)
    s2 = await normalize_new_raw_items(session, polite_client)
    assert s2.created == 0


@respx.mock
async def test_insert_conflict_keeps_earlier_documents(
    session, make_source, raw_item_factory, polite_client, monkeypatch
):
    from app.processing import normalize as mod

    src = await make_source(config={"fetch_fulltext": False})
    await raw_item_factory(source_id=src.id, url="https://news.test/x", raw_text="One")
    await raw_item_factory(source_id=src.id, url="https://news.test/x/", raw_text="Dup")
    await raw_item_factory(source_id=src.id, url="https://news.test/y", raw_text="Three")

    async def never_exists(session_, uhash):  # simulate a concurrent insert after the check
        return False

    monkeypatch.setattr(mod, "_url_hash_exists", never_exists)
    stats = await normalize_new_raw_items(session, polite_client)
    rows = (await session.execute(select(Document.id))).scalars().all()
    assert stats.created == 2 and sorted(rows) == sorted(stats.document_ids)
    s2 = await normalize_new_raw_items(session, polite_client)
    assert s2.created == 0


@respx.mock
async def test_unexpected_fetch_exception_keeps_feed_text_and_charges_budget(
    session, make_source, raw_item_factory, polite_client, monkeypatch
):
    src = await make_source()
    for i in range(2):
        await raw_item_factory(source_id=src.id, url=f"https://news.test/e{i}", raw_text="Tiny.")
    calls = 0

    async def explode(*a, **kw):
        nonlocal calls
        calls += 1
        raise ValueError("kaboom")

    monkeypatch.setattr(polite_client, "get", explode)
    stats = await normalize_new_raw_items(session, polite_client, fulltext_limit=1)
    assert stats.created == 2 and stats.fulltext_fetched == 0
    assert calls == 1
    docs = (await session.execute(select(Document))).scalars().all()
    assert {d.clean_text for d in docs} == {"Tiny."}
