from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.collectors.base import CollectorError
from app.collectors.rss import RssCollector

FIX = Path(__file__).parent.parent / "fixtures" / "feeds"
URL = "https://f.test/rss"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _mock(text: str, status: int = 200, **kw):
    respx.get("https://f.test/robots.txt").mock(return_value=httpx.Response(404))
    return respx.get(URL).mock(return_value=httpx.Response(status, text=text, **kw))


@respx.mock
async def test_parses_rss2_items(make_source, polite_client):
    _mock(_read("rss2.xml"))
    res = await RssCollector().fetch(await make_source(kind="rss", url=URL), None, polite_client)
    assert len(res.items) == 3
    first = res.items[0]
    assert first.external_id == "https://f.test/posts/1"
    assert first.published_at == datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    assert first.raw_text == "<p>Full body one</p>"
    assert first.payload["feed_title"] == "Test Blog"
    assert first.payload["author"] == "Ann"
    assert first.payload["tags"] == ["security", "ai"]
    assert res.items[1].external_id == "post-2"
    assert res.items[1].raw_text == "Summary two"


@respx.mock
async def test_parses_atom(make_source, polite_client):
    _mock(_read("atom.xml"))
    res = await RssCollector().fetch(await make_source(kind="rss", url=URL), None, polite_client)
    assert [i.external_id for i in res.items] == [
        "tag:f.test,2026:release/1.2.0",
        "tag:f.test,2026:release/1.1.0",
    ]
    assert res.items[0].url == "https://f.test/releases/1.2.0"


@respx.mock
async def test_since_filters_old_entries(make_source, polite_client):
    _mock(_read("rss2.xml"))
    since = datetime(2026, 10, 13, tzinfo=UTC)
    res = await RssCollector().fetch(await make_source(kind="rss", url=URL), since, polite_client)
    assert len(res.items) == 2


@respx.mock
async def test_missing_and_future_dates(make_source, polite_client, freeze_now):
    _mock(_read("dates.xml"))
    res = await RssCollector().fetch(await make_source(kind="rss", url=URL), None, polite_client)
    assert [i.published_at for i in res.items] == [None, None]


@respx.mock
async def test_not_modified_returns_flag(make_source, polite_client):
    route = _mock("", 304)
    res = await RssCollector().fetch(
        await make_source(kind="rss", url=URL, etag='"e"'), None, polite_client
    )
    assert res.not_modified and res.items == []
    assert route.calls[0].request.headers["if-none-match"] == '"e"'


@respx.mock
async def test_captures_etag_and_last_modified(make_source, polite_client):
    _mock(
        _read("rss2.xml"),
        headers={"ETag": '"v2"', "Last-Modified": "Wed, 14 Oct 2026 09:00:00 GMT"},
    )
    res = await RssCollector().fetch(await make_source(kind="rss", url=URL), None, polite_client)
    assert res.etag == '"v2"'
    assert res.last_modified == "Wed, 14 Oct 2026 09:00:00 GMT"


@respx.mock
async def test_http_error_raises(make_source, polite_client):
    _mock("nope", 404)
    with pytest.raises(CollectorError, match="HTTP 404"):
        await RssCollector().fetch(await make_source(kind="rss", url=URL), None, polite_client)


@respx.mock
async def test_malformed_feed_raises_collector_error(make_source, polite_client):
    _mock(_read("error_page.html"))
    with pytest.raises(CollectorError, match="unparseable feed"):
        await RssCollector().fetch(await make_source(kind="rss", url=URL), None, polite_client)


@respx.mock
async def test_feed_ignores_robots(make_source, polite_client):
    # feeds are fetched as a feed reader would; robots.txt is neither fetched nor checked
    robots = respx.get("https://f.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: */feed")
    )
    respx.get("https://f.test/blog/rss/").mock(
        return_value=httpx.Response(301, headers={"Location": "https://f.test/blog/feed/"})
    )
    feed = respx.get("https://f.test/blog/feed/").mock(
        return_value=httpx.Response(200, text=_read("rss2.xml"))
    )
    src = await make_source(kind="rss", url="https://f.test/blog/rss/")
    res = await RssCollector().fetch(src, None, polite_client)
    assert len(res.items) == 3 and feed.call_count == 1
    assert robots.call_count == 0


def test_seed_low_footprint_feed_flags():
    from app.seed.loader import read_seed

    sources = {s.name: s for s in read_seed().sources}
    for name in ("Checkmarx Blog", "Aqua Blog", "Black Duck Blog"):
        assert sources[name].enabled and sources[name].config["browser_ua"] is True
    assert "JFrog Blog" not in sources  # news about JFrog itself is not collected
    assert sum(1 for s in sources.values() if s.config.get("browser_ua")) == 3
