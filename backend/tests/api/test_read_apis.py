from datetime import UTC, datetime, timedelta

import httpx
import respx
from sqlalchemy import func, select

from app.collectors.base import CollectorError, FetchResult, PostingIn, RawItemIn
from app.models import RawItem, Source


async def test_competitors_jfrog_first(auth_client, seeded):
    r = await (await auth_client("pm")).get("/api/v1/competitors")
    body = r.json()
    assert body[0]["slug"] == "jfrog" and len(body) == 24
    assert body[0]["is_self"] is True


async def test_competitors_logo_url(auth_client, seeded):
    rows = (await (await auth_client("pm")).get("/api/v1/competitors")).json()
    for c in rows:
        assert c["logo_url"] in (None, f"/logos/{c['slug']}.svg")


async def test_read_endpoints_require_auth(client, seeded):
    for path in ("/competitors", "/sources", "/documents"):
        assert (await client.get(f"/api/v1{path}")).status_code == 401


async def test_sources_filter_by_kind(auth_client, seeded):
    r = await (await auth_client("pm")).get("/api/v1/sources", params={"kind": "sec_edgar"})
    assert {s["competitor_slug"] for s in r.json()} == {"jfrog", "gitlab"}


async def test_sources_filter_competitor_and_health(auth_client, seeded):
    c = await auth_client("pm")
    r = await c.get("/api/v1/sources", params={"competitor": "sonatype"})
    assert r.json() and {s["competitor_slug"] for s in r.json()} == {"sonatype"}
    r = await c.get("/api/v1/sources", params={"health": "down"})
    assert r.json() == []


async def test_sources_items_7d(auth_client, seeded, session, raw_item_factory):
    src = await _sonatype(session)
    now = datetime.now(UTC)
    await raw_item_factory(source_id=src.id, fetched_at=now - timedelta(days=1))
    await raw_item_factory(source_id=src.id, fetched_at=now - timedelta(days=2))
    await raw_item_factory(source_id=src.id, fetched_at=now - timedelta(days=9))
    rows = (await (await auth_client("pm")).get("/api/v1/sources")).json()
    by_id = {s["id"]: s for s in rows}
    assert by_id[src.id]["items_7d"] == 2
    assert all(s["items_7d"] == 0 for s in rows if s["id"] != src.id)


async def test_patch_source_requires_analyst(auth_client, seeded):
    pm = await auth_client("pm")
    sid = (await pm.get("/api/v1/sources")).json()[0]["id"]
    assert (await pm.patch(f"/api/v1/sources/{sid}", json={"enabled": False})).status_code == 403
    analyst = await auth_client("analyst")
    r = await analyst.patch(f"/api/v1/sources/{sid}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["id"] == sid
    missing = await analyst.patch("/api/v1/sources/999999", json={"enabled": True})
    assert missing.status_code == 404


async def _sonatype(session) -> Source:
    return (
        await session.execute(select(Source).where(Source.name == "Sonatype Blog"))
    ).scalar_one()


def _items(n):
    return [
        RawItemIn(
            external_id=f"e{i}",
            url=f"https://x.test/{i}",
            title=f"T{i}",
            published_at=datetime(2026, 10, 1, tzinfo=UTC),
            raw_text="x",
            payload={},
        )
        for i in range(n)
    ]


async def test_source_test_does_not_persist(auth_client, seeded, session, fake_collectors):
    src = await _sonatype(session)
    before = (src.health, src.consecutive_failures, src.last_fetched_at)
    fake_collectors({"Sonatype Blog": FetchResult(items=_items(2))})

    async def count():
        return await session.scalar(select(func.count()).select_from(RawItem))

    n0 = await count()
    r = await (await auth_client("analyst")).post(f"/api/v1/sources/{src.id}/test")
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["item_count"] == 2
    assert len(body["sample"]) == 2 and body["sample"][0]["title"] == "T0"
    assert body["error"] is None
    assert await count() == n0
    await session.refresh(src)
    assert (src.health, src.consecutive_failures, src.last_fetched_at) == before


async def test_source_test_sample_capped_and_errors(auth_client, seeded, session, fake_collectors):
    src = await _sonatype(session)
    a = await auth_client("analyst")
    fc = fake_collectors({"Sonatype Blog": FetchResult(items=_items(8))})
    body = (await a.post(f"/api/v1/sources/{src.id}/test")).json()
    assert body["item_count"] == 8 and len(body["sample"]) == 5
    assert fc.seen == [("Sonatype Blog", None)]
    fake_collectors({"Sonatype Blog": CollectorError("boom")})
    body = (await a.post(f"/api/v1/sources/{src.id}/test")).json()
    assert body["ok"] is False and "boom" in body["error"] and body["sample"] == []
    await session.refresh(src)
    assert src.consecutive_failures == 0


async def test_source_test_counts_a_boards_postings_and_a_pages_text(
    auth_client, seeded, fake_collectors, make_source
):
    """A job board returns postings, not feed items, and a watched page its
    text, so a working one no longer reads "0 items": the count is in the source's own unit
    (postings; 1 for a page read), the sample is its first postings (or the page itself),
    and the kind's own fields say how complete the listing is and how much text was read."""
    board = await make_source(kind="job_board", name="Acme jobs", config={"ats": "lever"})
    url = "https://acme.test/pricing"
    page = await make_source(
        kind="page_watch", name="Acme pricing page", config={"page": "pricing", "url": url}
    )
    postings = [
        PostingIn(
            external_id=f"p{i}",
            title=f"Engineer {i}",
            url=f"https://jobs.test/{i}",
            posted_at=datetime(2026, 10, i + 1, tzinfo=UTC),
        )
        for i in range(7)
    ]
    fake_collectors(
        {
            "Acme jobs": FetchResult(postings=postings, postings_complete=False),
            "Acme pricing page": FetchResult(page_text="Team plan: 49 dollars"),
            "Sonatype Blog": FetchResult(items=_items(2)),
        }
    )
    a = await auth_client("analyst")
    body = (await a.post(f"/api/v1/sources/{board.id}/test")).json()
    assert (body["ok"], body["item_count"], body["postings_count"]) == (True, 7, 7)
    assert body["postings_complete"] is False and body["page_text_chars"] is None
    assert [s["title"] for s in body["sample"]] == [f"Engineer {i}" for i in range(5)]
    assert body["sample"][0]["url"] == "https://jobs.test/0"
    assert body["sample"][0]["published_at"].startswith("2026-10-01")

    body = (await a.post(f"/api/v1/sources/{page.id}/test")).json()
    assert (body["ok"], body["item_count"], body["page_text_chars"]) == (True, 1, 21)
    assert (body["postings_count"], body["postings_complete"]) == (None, None)
    assert body["sample"] == [{"title": "Acme pricing page", "url": url, "published_at": None}]

    feed = (await a.post(f"/api/v1/sources/{(await _sonatype_id(a))}/test")).json()
    assert (feed["item_count"], feed["postings_count"], feed["page_text_chars"]) == (2, None, None)
    assert feed["metrics_count"] is None


async def test_source_test_counts_a_repositorys_metric_readings(
    auth_client, seeded, fake_collectors, make_source
):
    """A GitHub repository-stats source returns stars and forks as metrics, no items, so
    its test reports the readings taken, never "0 items"."""
    from datetime import date

    from app.collectors.base import MetricPointIn

    stats = await make_source(kind="github_repo_stats", name="acme/acme stats", config={})
    today = date(2026, 10, 20)
    fake_collectors(
        {
            "acme/acme stats": FetchResult(
                metrics=[
                    MetricPointIn("github_stars:acme/acme", today, 120),
                    MetricPointIn("github_forks:acme/acme", today, 9),
                ]
            )
        }
    )
    body = (await (await auth_client("analyst")).post(f"/api/v1/sources/{stats.id}/test")).json()
    assert (body["ok"], body["item_count"], body["metrics_count"]) == (True, 2, 2)
    assert (body["postings_count"], body["page_text_chars"]) == (None, None)


async def _sonatype_id(client) -> int:
    rows = (await client.get("/api/v1/sources", params={"competitor": "sonatype"})).json()
    return next(s["id"] for s in rows if s["name"] == "Sonatype Blog")


async def test_source_test_guards_and_unknown_kind(auth_client, seeded, make_source):
    assert (await (await auth_client("pm")).post("/api/v1/sources/1/test")).status_code == 403
    a = await auth_client("analyst")
    assert (await a.post("/api/v1/sources/999999/test")).status_code == 404
    src = await make_source(kind="tavily")
    body = (await a.post(f"/api/v1/sources/{src.id}/test")).json()
    assert body["ok"] is False and body["error"]


async def test_documents_filter_and_pagination(auth_client, docs_fixture):
    c = await auth_client("pm")
    p1 = (await c.get("/api/v1/documents", params={"competitor": "sonatype", "limit": 2})).json()
    assert p1["next_cursor"]
    p2 = (
        await c.get(
            "/api/v1/documents",
            params={"competitor": "sonatype", "limit": 2, "cursor": p1["next_cursor"]},
        )
    ).json()
    ids = [d["id"] for d in p1["items"] + p2["items"]]
    # 3 dated + 1 undated; the duplicate is excluded by default
    assert len(ids) == len(set(ids)) == 4 and p2["next_cursor"] is None
    assert ids[-1] == docs_fixture["undated"].id


async def test_documents_pagination_exact_boundary(auth_client, docs_fixture):
    c = await auth_client("pm")
    p = (await c.get("/api/v1/documents", params={"competitor": "sonatype", "limit": 4})).json()
    assert len(p["items"]) == 4 and p["next_cursor"] is None


async def test_documents_cursor_through_null_tier(auth_client, docs_fixture, make_document):
    await make_document(title="undated 2", published_at=None)
    c = await auth_client("pm")
    seen, cursor = [], None
    while True:
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        p = (await c.get("/api/v1/documents", params=params)).json()
        seen += [d["id"] for d in p["items"]]
        cursor = p["next_cursor"]
        if not cursor:
            break
    full = (await c.get("/api/v1/documents")).json()["items"]
    assert seen == [d["id"] for d in full] and len(seen) == len(set(seen))


async def test_documents_order_nulls_last(auth_client, docs_fixture):
    items = (await (await auth_client("pm")).get("/api/v1/documents")).json()["items"]
    dated = [d for d in items if d["published_at"]]
    assert dated and len(dated) < len(items)
    assert items[: len(dated)] == dated  # undated documents come after all dated ones
    stamps = [d["published_at"] for d in dated]
    assert stamps == sorted(stamps, reverse=True)


async def test_documents_exclude_duplicates_by_default(auth_client, docs_fixture):
    c = await auth_client("pm")
    items = (await c.get("/api/v1/documents")).json()["items"]
    assert docs_fixture["dup"].id not in {d["id"] for d in items}
    dups = (await c.get("/api/v1/documents", params={"status": "duplicate"})).json()["items"]
    assert [d["id"] for d in dups] == [docs_fixture["dup"].id]


async def test_documents_scope_q_since_and_shape(auth_client, docs_fixture):
    c = await auth_client("pm")
    r = (await c.get("/api/v1/documents", params={"scope": "industry"})).json()["items"]
    assert [d["id"] for d in r] == [docs_fixture["g1"].id]
    r = (await c.get("/api/v1/documents", params={"q": "100%_"})).json()["items"]
    assert [d["id"] for d in r] == [docs_fixture["s2"].id]
    assert (await c.get("/api/v1/documents", params={"q": "100%x"})).json()["items"] == []
    since = datetime(2026, 10, 12, tzinfo=UTC).isoformat()
    r = (await c.get("/api/v1/documents", params={"since": since, "competitor": "sonatype"})).json()
    assert {d["id"] for d in r["items"]} == {docs_fixture["s3"].id, docs_fixture["undated"].id}
    sonatype = (await c.get("/api/v1/documents", params={"competitor": "sonatype"})).json()["items"]
    d = next(x for x in sonatype if x["id"] == docs_fixture["s1"].id)
    assert d["source_name"] == "Sonatype Blog" and d["competitors"] == ["sonatype"]
    assert d["matched_terms"] == ["nexus"] and d["scope_hint"] == "competitor"
    assert d["excerpt"].startswith("Hello world x") and len(d["excerpt"]) == 300
    assert d["url"].startswith("https://")
    everything = (await c.get("/api/v1/documents")).json()["items"]
    other = next(x for x in everything if x["id"] == docs_fixture["g1"].id)
    assert other["matched_terms"] == [] and other["excerpt"] == ""


async def test_feed_hides_jfrog_self_news_for_every_role(
    auth_client, seeded, make_source, make_document
):
    """The Feed never lists JFrog-self news ("drop it entirely"), by the
    predicate /search shares: prefilter scope jfrog, prefilter reason jfrog_only, triage
    scope jfrog, or a source tied to the self competitor."""
    from app.models import Competitor

    jfrog = await seeded.scalar(select(Competitor).where(Competitor.is_self))
    own_feed = await make_source(name="JFrog Blog", competitor_id=jfrog.id)
    shown = await make_document(title="Sonatype ships", scope_hint="competitor", status="new")
    hidden = [
        await make_document(title="JFrog shares fall", scope_hint="jfrog", status="relevant"),
        await make_document(
            title="JFrog sets its results date",
            status="irrelevant",
            prefilter={"matched_terms": [], "scope_hint": None, "reason": "jfrog_only"},
        ),
        await make_document(
            title="UBS targets JFrog",
            scope_hint="industry",
            status="relevant",
            triage={"relevant": True, "scope": "jfrog"},
        ),
        await make_document(title="jfrog-cli 2.127.0", source_id=own_feed.id, status="new"),
    ]
    for role in ("analyst", "pm"):
        c = await auth_client(role)
        items = (await c.get("/api/v1/documents", params={"limit": 200})).json()["items"]
        ids = {d["id"] for d in items}
        assert shown.id in ids and not ids & {d.id for d in hidden}, role
        assert (await c.get("/api/v1/documents", params={"scope": "jfrog"})).json()["items"] == []


async def test_documents_bad_cursor_422(auth_client, docs_fixture):
    r = await (await auth_client("pm")).get("/api/v1/documents", params={"cursor": "!!!"})
    assert r.status_code == 422


# -- Test ignores the stored conditional-GET validators -----------------


@respx.mock
async def test_source_test_returns_items_for_a_previously_fetched_feed(
    auth_client, seeded, session, make_polite_client
):
    from pathlib import Path

    from app.api.v1 import sources as sources_api
    from app.main import app

    src = await _sonatype(session)
    src.etag = '"v1"'
    src.last_modified = "Wed, 14 Oct 2026 09:00:00 GMT"
    await session.flush()
    feed = (Path(__file__).parent.parent / "fixtures" / "feeds" / "rss2.xml").read_text("utf-8")

    def respond(request: httpx.Request) -> httpx.Response:
        conditional = "if-none-match" in request.headers or "if-modified-since" in request.headers
        return httpx.Response(304) if conditional else httpx.Response(200, text=feed)

    respx.get("https://www.sonatype.com/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get(src.url).mock(side_effect=respond)

    async def http_override():
        async with make_polite_client() as c:
            yield c

    app.dependency_overrides[sources_api.get_http] = http_override
    try:
        r = await (await auth_client("analyst")).post(f"/api/v1/sources/{src.id}/test")
    finally:
        app.dependency_overrides.pop(sources_api.get_http, None)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True, body
    assert body["item_count"] == 3 and body["sample"][0]["title"] == "Post one"
    assert "If-None-Match" not in route.calls[0].request.headers
    # the stored validators are untouched: the next real run still sends them
    await session.refresh(src)
    assert src.etag == '"v1"' and src.last_modified == "Wed, 14 Oct 2026 09:00:00 GMT"


# -- feed links point at the original item URL ------------------------------


async def test_document_url_is_the_original_item_url(auth_client, seeded, make_document, session):
    doc = await make_document(
        title="Tracked link",
        canonical_url="https://news.example.com/story?id=7",
    )
    raw = await session.get(RawItem, doc.raw_item_id)
    raw.url = "https://news.example.com/story?utm_source=rss&id=7"
    await session.flush()
    items = (await (await auth_client("pm")).get("/api/v1/documents")).json()["items"]
    got = next(x for x in items if x["id"] == doc.id)
    assert got["url"] == "https://news.example.com/story?utm_source=rss&id=7"
