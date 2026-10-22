from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.collectors import get_collector
from app.collectors.base import CollectorError
from app.collectors.gdelt import GdeltCollector, _gdelt_query, build_gdelt_url
from app.collectors.google_news import (
    GoogleNewsCollector,
    build_google_news_url,
    resolve_google_news_url,
)
from app.collectors.hn import HnCollector, build_hn_url
from app.collectors.queries import build_competitor_query

FIX = Path(__file__).parent.parent / "fixtures" / "news"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_query_for_socket(competitor_factory):
    c = competitor_factory(
        slug="socket",
        name="Socket",
        aliases=["Socket.dev", "Socket Security"],
        ambiguous_aliases=["Socket"],
        negative_keywords=["Socket.IO", "WebSocket"],
    )
    assert (
        build_competitor_query(c) == '("Socket.dev" OR "Socket Security") -"Socket.IO" -"WebSocket"'
    )


def test_query_limits_to_four_strong_aliases(competitor_factory):
    c = competitor_factory(aliases=["A", "B", "Amb", "C", "D", "E"], ambiguous_aliases=["Amb"])
    assert build_competitor_query(c) == '("A" OR "B" OR "C" OR "D")'


def test_query_falls_back_to_name(competitor_factory):
    c = competitor_factory(name="Wiz", aliases=["Wiz"], ambiguous_aliases=["Wiz"])
    assert build_competitor_query(c) == '("Wiz")'


def test_registered():
    assert isinstance(get_collector("google_news"), GoogleNewsCollector)
    assert isinstance(get_collector("gdelt"), GdeltCollector)
    assert isinstance(get_collector("hn"), HnCollector)


def test_google_news_url():
    assert build_google_news_url('("Cloudsmith")', 2) == (
        "https://news.google.com/rss/search?q=%28%22Cloudsmith%22%29+when%3A2d"
        "&hl=en-US&gl=US&ceid=US:en"
    )


def test_gdelt_url():
    u = build_gdelt_url('"Sonatype"', "1d")
    assert u.startswith(
        "https://api.gdeltproject.org/api/v2/doc/doc?query=%22Sonatype%22%20sourcelang%3Aenglish"
    )
    assert u.endswith("&mode=ArtList&format=json&sort=DateDesc&timespan=1d&maxrecords=75")


def test_gdelt_query_unwraps_single_term():
    assert _gdelt_query('("Snyk")') == "Snyk"
    assert _gdelt_query('("A" OR "B") -"x"') == "(A OR B) -x"
    assert _gdelt_query('("Snyk") -"x"') == "Snyk -x"
    assert _gdelt_query('("Docker Hub")') == '"Docker Hub"'


def test_gdelt_query_unquotes_single_words_only(competitor_factory):
    # GDELT: "The specified phrase is too short" for quoted single words (Snyk, Cloudsmith...).
    for name in ("Snyk", "Cloudsmith", "Checkmarx"):
        c = competitor_factory(name=name, aliases=[name])
        assert _gdelt_query(build_competitor_query(c)) == name
    socket = competitor_factory(
        name="Socket",
        aliases=["Socket.dev", "Socket Security"],
        negative_keywords=["Socket.IO", "WebSocket"],
    )
    assert (
        _gdelt_query(build_competitor_query(socket))
        == '("Socket.dev" OR "Socket Security") -"Socket.IO" -WebSocket'
    )
    sonatype = competitor_factory(name="Sonatype", aliases=["Sonatype", "Nexus Repository"])
    assert _gdelt_query(build_competitor_query(sonatype)) == '(Sonatype OR "Nexus Repository")'


def test_hn_url():
    assert build_hn_url("JFrog", 1760000000) == (
        "https://hn.algolia.com/api/v1/search_by_date?query=JFrog&tags=story"
        "&numericFilters=created_at_i>1760000000&hitsPerPage=50"
    )


GN_FEED = "https://news.google.com/rss/search"


@respx.mock
async def test_google_news_resolves_when_opted_in(make_source, competitor_factory, polite_client):
    comp = competitor_factory(name="Cloudsmith", aliases=["Cloudsmith"])
    src = await make_source(
        kind="google_news",
        url="generated://google_news/cloudsmith",
        config={"days": 2, "resolve_urls": True},
        competitor=comp,
    )
    respx.get("https://news.google.com/robots.txt").mock(return_value=httpx.Response(404))
    feed = respx.get(url__startswith=GN_FEED).mock(
        return_value=httpx.Response(200, text=_read("google_news.xml"))
    )
    respx.get("https://news.google.com/rss/articles/CBMiAAA?oc=5").mock(
        return_value=httpx.Response(302, headers={"location": "https://siliconangle.com/story"})
    )
    respx.get("https://siliconangle.com/story").mock(return_value=httpx.Response(200, text="x"))
    respx.get("https://news.google.com/rss/articles/CBMiBBB?oc=5").mock(
        return_value=httpx.Response(200, text="consent")
    )
    res = await GoogleNewsCollector().fetch(src, None, polite_client)
    assert "q=%28%22Cloudsmith%22%29+when%3A2d" in str(feed.calls[0].request.url)
    assert len(res.items) == 2
    first = res.items[0]
    assert first.external_id == "gn:CBMiAAA"
    assert first.payload["outlet"] == "SiliconANGLE"
    assert first.payload["outlet_url"] == "https://siliconangle.com"
    assert first.url == "https://siliconangle.com/story"
    assert first.published_at == datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    assert res.items[1].url == "https://news.google.com/rss/articles/CBMiBBB?oc=5"


@respx.mock
async def test_google_news_default_does_not_resolve(make_source, competitor_factory, polite_client):
    comp = competitor_factory(name="Cloudsmith", aliases=["Cloudsmith"])
    src = await make_source(
        kind="google_news", url="generated://google_news/cs", config={}, competitor=comp
    )
    respx.get("https://news.google.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(url__startswith=GN_FEED).mock(
        return_value=httpx.Response(200, text=_read("google_news.xml"))
    )
    article = respx.get(url__startswith="https://news.google.com/rss/articles/").mock(
        return_value=httpx.Response(200, text="x")
    )
    res = await GoogleNewsCollector().fetch(src, None, polite_client)
    assert not article.called
    assert res.items[0].url == "https://news.google.com/rss/articles/CBMiAAA?oc=5"
    assert res.items[0].payload["outlet"] == "SiliconANGLE"


@respx.mock
async def test_google_news_needs_competitor(make_source, polite_client):
    src = await make_source(kind="google_news", url="generated://google_news/x")
    with pytest.raises(CollectorError, match="competitor"):
        await GoogleNewsCollector().fetch(src, None, polite_client)


@respx.mock
async def test_resolve_redirect_to_publisher(polite_client):
    respx.get("https://news.google.com/rss/articles/X").mock(
        return_value=httpx.Response(302, headers={"location": "https://siliconangle.com/a"})
    )
    respx.get("https://siliconangle.com/a").mock(return_value=httpx.Response(200, text="ok"))
    got = await resolve_google_news_url("https://news.google.com/rss/articles/X", polite_client)
    assert got == "https://siliconangle.com/a"


@respx.mock
async def test_resolve_stays_on_google_returns_none(polite_client):
    respx.get("https://news.google.com/rss/articles/X").mock(
        return_value=httpx.Response(200, text="consent")
    )
    got = await resolve_google_news_url("https://news.google.com/rss/articles/X", polite_client)
    assert got is None


@respx.mock
async def test_gdelt_parses_articles(make_source, competitor_factory, polite_client):
    comp = competitor_factory(name="Sonatype", aliases=["Sonatype"])
    src = await make_source(
        kind="gdelt", url="generated://gdelt/sonatype", config={"timespan": "1d"}, competitor=comp
    )
    route = respx.get(url__startswith="https://api.gdeltproject.org/api/v2/doc/doc").mock(
        return_value=httpx.Response(200, text=_read("gdelt.json"))
    )
    res = await GdeltCollector().fetch(src, None, polite_client)
    assert "timespan=1d" in str(route.calls[0].request.url)
    assert len(res.items) == 2
    a = res.items[0]
    assert a.external_id.startswith("gdelt:") and len(a.external_id) == len("gdelt:") + 40
    assert a.url == "https://a.example.com/one"
    assert a.title == "Sonatype ships thing"
    assert a.published_at == datetime(2026, 10, 14, 9, 15, 0, tzinfo=UTC)
    assert a.raw_text is None
    assert a.payload == {
        "domain": "a.example.com",
        "language": "English",
        "sourcecountry": "United States",
    }


@respx.mock
async def test_gdelt_plain_text_error_raises(make_source, competitor_factory, polite_client):
    comp = competitor_factory(name="Sonatype", aliases=["Sonatype"])
    src = await make_source(kind="gdelt", url="generated://gdelt/s", competitor=comp)
    respx.get(url__startswith="https://api.gdeltproject.org/").mock(
        return_value=httpx.Response(200, text="Please limit requests to one every 5 seconds")
    )
    with pytest.raises(CollectorError, match="limit requests"):
        await GdeltCollector().fetch(src, None, polite_client)


@respx.mock
async def test_gdelt_empty_object_no_items(make_source, competitor_factory, polite_client):
    comp = competitor_factory(name="Sonatype", aliases=["Sonatype"])
    src = await make_source(kind="gdelt", url="generated://gdelt/e", competitor=comp)
    respx.get(url__startswith="https://api.gdeltproject.org/").mock(
        return_value=httpx.Response(200, text="{}")
    )
    assert (await GdeltCollector().fetch(src, None, polite_client)).items == []


@respx.mock
async def test_hn_falls_back_to_item_url(make_source, competitor_factory, polite_client):
    comp = competitor_factory(name="JFrog", aliases=["JFrog"])
    src = await make_source(kind="hn", url="generated://hn/jfrog", competitor=comp)
    route = respx.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, text=_read("hn.json"))
    )
    since = datetime(2026, 10, 1, tzinfo=UTC)
    res = await HnCollector().fetch(src, since, polite_client)
    called = str(route.calls[0].request.url).replace("%3E", ">")
    assert f"created_at_i>{int(since.timestamp())}" in called
    assert [i.external_id for i in res.items] == ["hn:111", "hn:222"]
    assert res.items[0].url == "https://jfrog.com/blog/x"
    assert res.items[0].payload == {
        "points": 120,
        "num_comments": 45,
        "hn_url": "https://news.ycombinator.com/item?id=111",
    }
    assert res.items[0].published_at == datetime.fromtimestamp(1760400000, tz=UTC)
    assert res.items[1].url == "https://news.ycombinator.com/item?id=222"
    assert res.items[1].raw_text == "<p>Anyone use it?</p>"


@respx.mock
async def test_hn_http_error(make_source, competitor_factory, polite_client):
    comp = competitor_factory(name="JFrog", aliases=["JFrog"])
    src = await make_source(kind="hn", url="generated://hn/j", competitor=comp)
    respx.get(url__startswith="https://hn.algolia.com/").mock(return_value=httpx.Response(403))
    with pytest.raises(CollectorError, match="HTTP 403"):
        await HnCollector().fetch(src, None, polite_client)
