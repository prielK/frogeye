"""Read APIs: briefings, signals, agent runs and costs."""

import base64
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.agents.runtime.context import RunCtx
from app.api import deps
from app.api.deps import get_optional_llm, get_session_factory
from app.api.v1 import costs as costs_api
from app.api.v1 import signals as signals_api
from app.core.config import get_settings
from app.main import app
from app.models import (
    AgentRun,
    AgentStep,
    Briefing,
    Capability,
    LlmCall,
    PipelineRun,
    RawItem,
    Signal,
    SignalCapability,
    Source,
)
from app.rag.indexing import index_signal

CTX = RunCtx(agent_run_id=None, node="index")
D1, D2, D3 = date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 17)
PUBLISHED_AT = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_state():
    signals_api.limiter.reset()
    yield
    signals_api.limiter.reset()
    app.dependency_overrides.pop(get_optional_llm, None)
    app.dependency_overrides.pop(get_session_factory, None)


def _content(headline: str, signal_ids: list[int] = ()) -> dict:
    return {
        "headline": headline,
        "lead": f"{headline} lead.",
        "top_takeaways": [
            {
                "title": "Takeaway",
                "so_what": "So what.",
                "now_what": "",
                "signal_ids": list(signal_ids)[:1],
                "confidence": "high",
            }
        ]
        if signal_ids
        else [],
        "sections": [
            {
                "theme": "Competitive moves",
                "items": [
                    {"signal_id": sid, "one_liner": f"One {sid}", "why_it_matters": "Why."}
                    for sid in signal_ids
                ],
            }
        ]
        if signal_ids
        else [],
        "quiet_competitors": [],
        "watchlist": [],
    }


@pytest.fixture
def make_briefing(session):
    async def _make(
        day: date,
        edition: str = "master",
        version: int = 1,
        status: str = "published",
        *,
        headline: str | None = None,
        signal_ids: list[int] = (),
        **kw,
    ) -> Briefing:
        live = status in ("published", "quiet")
        row = Briefing(
            date=day,
            edition=edition,
            version=version,
            status=status,
            content=_content(headline or f"{edition} {day} v{version}", list(signal_ids)),
            signal_ids=list(signal_ids),
            funnel=kw.pop("funnel", {"fetched": 40, "relevant": 6}),
            published_at=kw.pop("published_at", PUBLISHED_AT if live else None),
            **kw,
        )
        session.add(row)
        await session.flush()
        return row

    return _make


# ---- briefings -------------------------------------------------------------------------


async def test_briefing_endpoints_require_auth(client, seeded):
    for path in ("/briefings", "/briefings/latest", f"/briefings/{D1}"):
        assert (await client.get(f"/api/v1{path}")).status_code == 401


async def test_latest_briefing_defaults_to_role_edition(auth_client, make_briefing):
    exec_ = await auth_client("exec")
    assert (await exec_.get("/api/v1/briefings/latest")).status_code == 404  # no rows at all

    await make_briefing(D1, "master")
    master_d2 = await make_briefing(D2, "master")
    await make_briefing(D1, "sales")
    sales_d2 = await make_briefing(D2, "sales")
    pm_d2 = await make_briefing(D2, "pm")

    analyst = await auth_client("analyst")
    body = (await analyst.get("/api/v1/briefings/latest")).json()
    assert (body["id"], body["edition"], body["requested_edition"]) == (
        master_d2.id,
        "master",
        "master",
    )

    sales = await auth_client("sales")
    body = (await sales.get("/api/v1/briefings/latest")).json()
    assert (body["id"], body["edition"], body["date"]) == (sales_d2.id, "sales", str(D2))
    assert body["requested_edition"] == "sales"

    pm = await auth_client("pm")
    assert (await pm.get("/api/v1/briefings/latest")).json()["id"] == pm_d2.id

    # exec has no edition rows, so it is served the master, labelled as such.
    body = (await exec_.get("/api/v1/briefings/latest")).json()
    assert (body["id"], body["edition"], body["requested_edition"]) == (
        master_d2.id,
        "master",
        "exec",
    )


async def test_a_reader_cannot_obtain_another_edition(auth_client, make_briefing):
    """Each reader sees only their own edition: there is no `edition` parameter, so a sales
    reader asking for the master (or any edition) on any endpoint still gets the sales row."""
    await make_briefing(D1, "master")
    await make_briefing(D2, "master")
    s1 = await make_briefing(D1, "sales")
    s2 = await make_briefing(D2, "sales")
    await make_briefing(D2, "pm")

    sales = await auth_client("sales")
    for asked in ("master", "pm", "exec", "nope"):
        params = {"edition": asked}
        latest = (await sales.get("/api/v1/briefings/latest", params=params)).json()
        assert (latest["id"], latest["edition"], latest["requested_edition"]) == (
            s2.id,
            "sales",
            "sales",
        )
        dated = (await sales.get(f"/api/v1/briefings/{D2}", params=params)).json()
        assert (dated["id"], dated["edition"]) == (s2.id, "sales")
        listed = (await sales.get("/api/v1/briefings", params=params)).json()
        assert [(b["id"], b["edition"]) for b in listed] == [(s2.id, "sales"), (s1.id, "sales")]

    paths = app.openapi()["paths"]
    for path in ("/api/v1/briefings", "/api/v1/briefings/latest", "/api/v1/briefings/{date}"):
        names = {p["name"] for p in paths[path]["get"].get("parameters", [])}
        assert "edition" not in names, path


async def test_latest_falls_back_to_master_on_skipped_day(auth_client, make_briefing):
    """A skipped_budget day files a master row only; a sales reader gets that
    master row from /latest, not the previous day's sales briefing."""
    await make_briefing(D1, "master")
    await make_briefing(D1, "sales")
    skipped = await make_briefing(D2, "master", 1, "skipped_budget")

    sales = await auth_client("sales")
    body = (await sales.get("/api/v1/briefings/latest")).json()
    assert (body["id"], body["date"], body["status"]) == (skipped.id, str(D2), "skipped_budget")
    assert (body["edition"], body["requested_edition"]) == ("master", "sales")


async def test_briefing_by_date_falls_back_to_master(auth_client, make_briefing):
    """A published day whose sales edition is missing (role adapter error) serves the
    master; within the served edition the version rule still applies."""
    master_v1 = await make_briefing(D2, "master", 1, "published")
    await make_briefing(D2, "master", 2, "failed")
    pm = await make_briefing(D2, "pm", 1)

    c = await auth_client("sales")
    body = (await c.get(f"/api/v1/briefings/{D2}")).json()
    assert (body["id"], body["edition"], body["requested_edition"]) == (
        master_v1.id,
        "master",
        "sales",
    )
    pm_reader = await auth_client("pm")
    body = (await pm_reader.get(f"/api/v1/briefings/{D2}")).json()
    assert (body["id"], body["edition"], body["requested_edition"]) == (pm.id, "pm", "pm")
    assert (await c.get(f"/api/v1/briefings/{D1}")).status_code == 404  # no row that day


async def test_briefing_list_mixes_edition_and_master_rows(auth_client, make_briefing):
    """One entry per date -- the edition's row when the date has one, else the master's;
    the limit counts dates. An analyst reads the master's own rows."""
    m1 = await make_briefing(D1, "master")
    s1 = await make_briefing(D1, "sales")
    m2 = await make_briefing(D2, "master", 1, "skipped_budget")
    m3 = await make_briefing(D3, "master")
    s3 = await make_briefing(D3, "sales")
    await make_briefing(D3, "pm")

    sales = await auth_client("sales")
    rows = (await sales.get("/api/v1/briefings")).json()
    assert [(b["date"], b["id"], b["edition"]) for b in rows] == [
        (str(D3), s3.id, "sales"),
        (str(D2), m2.id, "master"),
        (str(D1), s1.id, "sales"),
    ]
    assert {b["requested_edition"] for b in rows} == {"sales"}
    limited = (await sales.get("/api/v1/briefings", params={"limit": 2})).json()
    assert [b["id"] for b in limited] == [s3.id, m2.id]

    analyst = await auth_client("analyst")
    masters = (await analyst.get("/api/v1/briefings")).json()
    assert [(b["id"], b["edition"]) for b in masters] == [
        (m3.id, "master"),
        (m2.id, "master"),
        (m1.id, "master"),
    ]


async def test_briefing_by_date_latest_version(auth_client, make_briefing, make_signal):
    sig = await make_signal(title="Briefed signal", reliability="B", credibility=2)
    await make_briefing(D2, "master", 1, headline="first")
    v2 = await make_briefing(D2, "master", 2, headline="second", signal_ids=[sig.id])
    await make_briefing(D2, "sales", 3, headline="sales edition")

    c = await auth_client("analyst")
    r = await c.get(f"/api/v1/briefings/{D2}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["id"], body["version"], body["edition"]) == (v2.id, 2, "master")
    assert body["status"] == "published"
    assert body["content"]["headline"] == "second"
    assert body["content"]["top_takeaways"][0]["now_what"] == ""
    assert body["funnel"] == {"fetched": 40, "relevant": 6}
    assert body["published_at"].startswith("2026-10-17T04:00:00")
    assert body["agent_run_id"] is None
    [card] = body["signals"]
    assert (card["id"], card["title"], card["grade"]) == (sig.id, "Briefed signal", "B2")

    sales = (await (await auth_client("sales")).get(f"/api/v1/briefings/{D2}")).json()
    assert sales["content"]["headline"] == "sales edition"
    assert (await c.get("/api/v1/briefings/2026-01-01")).status_code == 404
    assert (await c.get("/api/v1/briefings/not-a-date")).status_code == 422


async def test_briefing_prefers_published_or_quiet_version(auth_client, make_briefing):
    """Per (date, edition), the latest published|quiet version wins over a later
    skipped_budget/failed one; a day with no live version still shows its latest state."""
    v1 = await make_briefing(D1, "master", 1, "published")
    await make_briefing(D1, "master", 2, "skipped_budget")
    await make_briefing(D1, "master", 3, "failed")
    await make_briefing(D2, "master", 1, "published")
    quiet = await make_briefing(D2, "master", 2, "quiet")
    await make_briefing(D3, "master", 1, "failed")
    skipped = await make_briefing(D3, "master", 2, "skipped_budget")

    c = await auth_client("analyst")
    assert (await c.get(f"/api/v1/briefings/{D1}")).json()["id"] == v1.id
    assert (await c.get(f"/api/v1/briefings/{D2}")).json()["id"] == quiet.id
    latest = (await c.get("/api/v1/briefings/latest")).json()
    assert (latest["id"], latest["status"]) == (skipped.id, "skipped_budget")
    listed = (await c.get("/api/v1/briefings")).json()
    assert [(b["date"], b["id"]) for b in listed] == [
        (str(D3), skipped.id),
        (str(D2), quiet.id),
        (str(D1), v1.id),
    ]


async def test_briefing_list_newest_first_per_edition(auth_client, make_briefing):
    await make_briefing(D1, "pm", 1)
    d2 = await make_briefing(D2, "pm", 2, headline="pm two")
    await make_briefing(D2, "pm", 1)
    d3 = await make_briefing(D3, "pm", 1)
    await make_briefing(D3, "master", 1)

    pm = await auth_client("pm")
    rows = (await pm.get("/api/v1/briefings", params={"limit": 2})).json()
    assert [b["id"] for b in rows] == [d3.id, d2.id]
    assert rows[1] == {
        "id": d2.id,
        "date": str(D2),
        "edition": "pm",
        "requested_edition": "pm",
        "version": 2,
        "status": "published",
        "headline": "pm two",
        "published_at": rows[1]["published_at"],
    }
    assert len((await pm.get("/api/v1/briefings")).json()) == 3


async def test_briefing_cards_follow_current_visibility(auth_client, make_briefing, make_signal):
    """Cards show the briefing's signals this reader may
    see NOW, so one rejected after filing drops out."""
    ok = await make_signal(status="approved")
    later_rejected = await make_signal(status="rejected")
    await make_briefing(D2, "sales", signal_ids=[ok.id, later_rejected.id])

    sales = await auth_client("sales")
    body = (await sales.get(f"/api/v1/briefings/{D2}")).json()
    assert {s["id"] for s in body["signals"]} == {ok.id}
    assert [i["signal_id"] for i in body["content"]["sections"][0]["items"]] == [ok.id]
    assert (await sales.get(f"/api/v1/signals/{later_rejected.id}")).status_code == 404


async def test_filed_briefing_redacts_hidden_items(
    auth_client, make_briefing, make_signal, session
):
    """A filed briefing drops items citing now-invisible (jfrog-scope) signals, and
    any legacy "JFrog in the news" section; the other items stay."""
    ok = await make_signal(status="approved")
    other = await make_signal(status="approved")
    hidden = await make_signal(status="approved", scope="jfrog")
    row = await make_briefing(D2, "master", signal_ids=[ok.id, other.id, hidden.id])
    row.content = {
        "headline": "H",
        "lead": "L",
        "top_takeaways": [
            {
                "title": "Gone",
                "so_what": "s",
                "now_what": "",
                "signal_ids": [hidden.id],
                "confidence": "high",
            },
            {
                "title": "Kept",
                "so_what": "s",
                "now_what": "",
                "signal_ids": [hidden.id, ok.id],
                "confidence": "high",
            },
        ],
        "sections": [
            {
                "theme": "Competitive moves",
                "items": [
                    {"signal_id": ok.id, "one_liner": "ok", "why_it_matters": "w"},
                    {"signal_id": hidden.id, "one_liner": "hid", "why_it_matters": "w"},
                ],
            },
            {
                "theme": "Market & money",
                "items": [{"signal_id": hidden.id, "one_liner": "hid2", "why_it_matters": "w"}],
            },
            {
                "theme": "JFrog in the news",
                "items": [{"signal_id": other.id, "one_liner": "self", "why_it_matters": "w"}],
            },
        ],
        "quiet_competitors": [],
        "watchlist": [
            {"signal_id": hidden.id, "note": "n"},
            {"signal_id": other.id, "note": "n2"},
        ],
    }
    await session.flush()
    for role in ("sales", "analyst"):
        r = await (await auth_client(role)).get(f"/api/v1/briefings/{D2}")
        assert r.status_code == 200, r.text
        body = r.json()
        content = body["content"]
        assert [t["title"] for t in content["top_takeaways"]] == ["Kept"]
        assert [s["theme"] for s in content["sections"]] == ["Competitive moves"]
        assert [i["signal_id"] for i in content["sections"][0]["items"]] == [ok.id]
        assert [w["signal_id"] for w in content["watchlist"]] == [other.id]
        assert {s["id"] for s in body["signals"]} == {ok.id, other.id}


async def test_legacy_briefing_without_new_fields_renders(auth_client, make_briefing, make_signal):
    """Content with only so_what/now_what validates and serves."""
    sig = await make_signal(status="approved")
    await make_briefing(D2, "master", signal_ids=[sig.id])
    body = (await (await auth_client("pm")).get(f"/api/v1/briefings/{D2}")).json()
    t = body["content"]["top_takeaways"][0]
    assert t["so_what"] == "So what." and t["now_what"] == ""
    assert [s["id"] for s in body["signals"]] == [sig.id]


async def test_cards_never_list_the_self_competitor(
    auth_client, seeded, make_briefing, make_signal
):
    """Even a stray self link never shows as a company chip."""
    sig = await make_signal(status="approved", competitors=["jfrog", "sonatype"])
    await make_briefing(D2, "master", signal_ids=[sig.id])
    pm = await auth_client("pm")
    body = (await pm.get(f"/api/v1/briefings/{D2}")).json()
    assert body["signals"][0]["competitors"] == ["sonatype"]
    page = (await pm.get("/api/v1/signals")).json()
    assert page["items"][0]["competitors"] == ["sonatype"]


async def test_jfrog_scope_hidden_for_analyst_too(auth_client, make_signal, seeded):
    """No role, whatever the status, gets a scope=jfrog signal from the list, detail
    or related (q search and /search are covered where retrieval is wired)."""
    shown = await make_signal(title="Shown", centroid="registry news")
    hidden = [
        await make_signal(title="Self", scope="jfrog", status=st, centroid="registry news")
        for st in ("auto", "approved", "needs_review", "rejected", "suppressed")
    ]
    hidden_ids = {h.id for h in hidden}
    for role in ("analyst", "pm"):
        c = await auth_client(role)
        for params in (
            {},
            {"status": ["rejected", "suppressed", "auto", "approved", "needs_review"]},
            {"scope": "jfrog"},
        ):
            r = await c.get("/api/v1/signals", params=params)
            assert r.status_code == 200, r.text
            assert not hidden_ids & set(_ids(r.json())), (role, params)
        for h in hidden:
            assert (await c.get(f"/api/v1/signals/{h.id}")).status_code == 404
        detail = await c.get(f"/api/v1/signals/{shown.id}")
        assert detail.status_code == 200
        assert not hidden_ids & {s["id"] for s in detail.json()["related"]}


async def test_signals_q_hides_jfrog_scope_for_analyst(auth_client, indexed, wire):
    """The same rule on /signals?q (the retrieval path)."""
    wire()
    shown = await indexed(title="Registry news", fact="Registry thing", impact="x")
    hidden = await indexed(title="Registry news", fact="Registry thing", impact="x", scope="jfrog")
    r = await (await auth_client("analyst")).get("/api/v1/signals", params={"q": "registry"})
    assert r.status_code == 200, r.text
    assert set(_ids(r.json())) == {shown.id} and hidden.id not in _ids(r.json())


# ---- signals: list -----------------------------------------------------------------------


def _ids(page: dict) -> list[int]:
    return [s["id"] for s in page["items"]]


async def test_signal_endpoints_require_auth(client, seeded):
    assert (await client.get("/api/v1/signals")).status_code == 401
    assert (await client.get("/api/v1/signals/1")).status_code == 401


async def test_signals_list_keyset_and_filters(auth_client, make_signal):
    recent = datetime(2026, 10, 16, tzinfo=UTC)
    s1 = await make_signal(importance=90, competitors=["sonatype"], last_updated_at=recent)
    s2 = await make_signal(importance=80, competitors=["sonatype"], last_updated_at=recent)
    s3 = await make_signal(importance=80, status="auto", last_updated_at=recent)
    s4 = await make_signal(
        importance=70,
        competitors=["gitlab"],
        signal_type="pricing_change",
        scope="industry",
        last_updated_at=recent,
    )
    s5 = await make_signal(importance=10, last_updated_at=datetime(2026, 9, 1, tzinfo=UTC))
    expected = [s1.id, s3.id, s2.id, s4.id, s5.id]  # importance DESC, id DESC

    c = await auth_client("pm")
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await c.get("/api/v1/signals", params=params)).json()
        assert page["search_mode"] is None
        seen += _ids(page)
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == expected and pages == 3

    async def ids(**params) -> list[int]:
        r = await c.get("/api/v1/signals", params=params)
        assert r.status_code == 200, r.text
        return _ids(r.json())

    assert await ids(competitor="gitlab") == [s4.id]
    assert await ids(competitor=["gitlab", "sonatype"]) == [s1.id, s2.id, s4.id]
    assert await ids(type="pricing_change") == [s4.id]
    assert await ids(scope="industry") == [s4.id]
    assert await ids(min_importance=75) == [s1.id, s3.id, s2.id]
    assert await ids(since="2026-10-01T00:00:00Z") == [s1.id, s3.id, s2.id, s4.id]
    assert (await c.get("/api/v1/signals", params={"cursor": "%%%not-base64"})).status_code == 422
    assert (await c.get("/api/v1/signals", params={"cursor": "e30="})).status_code == 422
    assert (await c.get("/api/v1/signals", params={"type": "bogus"})).status_code == 422


def _forge(payload) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        _forge({"imp": 99999, "id": 1}),  # beyond smallint
        _forge({"imp": -1, "id": 1}),
        _forge({"imp": 50, "id": 2**31}),  # beyond integer
        _forge({"imp": 50, "id": 0}),
        _forge({"imp": 10**30, "id": 1}),
        _forge({"imp": True, "id": 1}),
        _forge({"imp": 1.5, "id": 1}),
        _forge({"imp": "50", "id": 1}),
        _forge([50, 1]),
        _forge("imp"),
        _forge(None),
        base64.urlsafe_b64encode(("[" * 50 + "1" + "]" * 50).encode()).decode(),
        base64.urlsafe_b64encode(b"\xff\xfe not utf-8").decode(),
        "=" * 5,
        "x" * 1000,  # far longer than any real cursor
    ],
    ids=[
        "imp-over-smallint",
        "imp-negative",
        "id-over-integer",
        "id-zero",
        "imp-huge",
        "imp-bool",
        "imp-float",
        "imp-string",
        "json-list",
        "json-string",
        "json-null",
        "json-nested",
        "not-utf8",
        "padding-only",
        "oversized",
    ],
)
async def test_signals_forged_cursor_is_422(auth_client, make_signal, cursor):
    """Every malformed or out-of-range cursor is a 422, never a 500."""
    await make_signal(importance=50)
    r = await (await auth_client("pm")).get("/api/v1/signals", params={"cursor": cursor})
    assert r.status_code == 422, r.text
    assert r.json() == {"detail": "Invalid cursor"}


async def test_signals_cursor_at_range_edges_is_accepted(auth_client, make_signal):
    s = await make_signal(importance=0)
    c = await auth_client("pm")
    top = _forge({"imp": 32767, "id": 2**31 - 1})
    assert _ids((await c.get("/api/v1/signals", params={"cursor": top})).json()) == [s.id]
    bottom = _forge({"imp": 0, "id": 1})
    assert _ids((await c.get("/api/v1/signals", params={"cursor": bottom})).json()) == []


async def test_signals_capability_kit_stance_filters(auth_client, session, make_signal):
    """Capability (repeatable), kit (repeatable) and stance facets."""
    cap = {
        c.slug: c.id
        for c in await session.scalars(
            select(Capability).where(Capability.slug.in_(["container-registry", "model-registry"]))
        )
    }
    a = await make_signal(importance=60, stance="threat", kits=["ai-supply-chain"])
    b = await make_signal(importance=50, stance="opportunity", kits=["pricing-pressure"])
    c_ = await make_signal(importance=40, stance="threat")
    session.add_all(
        [
            SignalCapability(signal_id=a.id, capability_id=cap["model-registry"]),
            SignalCapability(signal_id=b.id, capability_id=cap["container-registry"]),
        ]
    )
    await session.flush()

    c = await auth_client("pm")

    async def ids(**params) -> list[int]:
        return _ids((await c.get("/api/v1/signals", params=params)).json())

    assert await ids(capability="model-registry") == [a.id]
    assert await ids(capability=["model-registry", "container-registry"]) == [a.id, b.id]
    assert await ids(kit="pricing-pressure") == [b.id]
    assert await ids(kit=["pricing-pressure", "ai-supply-chain"]) == [a.id, b.id]
    assert await ids(stance="threat") == [a.id, c_.id]
    assert await ids(stance="threat", kit="ai-supply-chain") == [a.id]
    assert await ids(capability="no-such-capability") == []


async def test_signal_visibility_by_role(auth_client, make_signal):
    """Non-analysts see auto|approved only (list, status filter, detail); analysts
    default to auto|approved|needs_review and may ask for any status."""
    by = {}
    for i, st in enumerate(
        ("auto", "approved", "needs_review", "rejected", "suppressed", "pending")
    ):
        by[st] = (await make_signal(status=st, importance=90 - i)).id

    pm = await auth_client("pm")

    async def ids(client, **params) -> set[int]:
        r = await client.get("/api/v1/signals", params=params)
        assert r.status_code == 200, r.text
        return set(_ids(r.json()))

    assert await ids(pm) == {by["auto"], by["approved"]}
    assert await ids(pm, status="needs_review") == set()
    assert await ids(pm, status=["approved", "rejected"]) == {by["approved"]}
    assert (await pm.get(f"/api/v1/signals/{by['needs_review']}")).status_code == 404
    assert (await pm.get(f"/api/v1/signals/{by['approved']}")).status_code == 200

    analyst = await auth_client("analyst")
    assert await ids(analyst) == {by["auto"], by["approved"], by["needs_review"]}
    assert await ids(analyst, status="rejected") == {by["rejected"]}
    assert await ids(analyst, status=["suppressed", "pending"]) == {
        by["suppressed"],
        by["pending"],
    }
    detail = await analyst.get(f"/api/v1/signals/{by['needs_review']}")
    assert detail.status_code == 200 and detail.json()["status"] == "needs_review"
    assert (await analyst.get("/api/v1/signals/999999")).status_code == 404


# ---- signals: detail ---------------------------------------------------------------------


async def test_signal_detail_includes_evidence_and_breakdown(
    auth_client, session, make_document, make_signal
):
    src = (await session.execute(select(Source).where(Source.name == "Sonatype Blog"))).scalar_one()
    primary = await make_document(
        source_id=src.id, title="Primary story", published_at=datetime(2026, 10, 14, tzinfo=UTC)
    )
    other = await make_document(title="Follow-up")
    breakdown = {"components": {"impact": {"value": 0.8}}, "decay": 0.9, "importance": 77}
    sig = await make_signal(
        title="Sonatype ships a model registry",
        importance=77,
        reliability="B",
        credibility=2,
        stance="threat",
        competitors=["sonatype"],
        kits=["ai-supply-chain"],
        documents=[primary, other],
        evidence=[(primary, "verified words here", True), (other, "unverified words", False)],
        score_breakdown=breakdown,
        role_actions={"sales": "Ask about scale.", "pm": "", "exec": None, "pmm_devrel": "Angle."},
        centroid="#cluster:reg",
    )
    cap = await session.scalar(select(Capability).where(Capability.slug == "model-registry"))
    session.add(SignalCapability(signal_id=sig.id, capability_id=cap.id))
    near = await make_signal(title="Near", centroid="#cluster:reg near")
    far = await make_signal(title="Far", centroid="#cluster:other")
    hidden = await make_signal(title="Hidden", status="rejected", centroid="#cluster:reg hidden")
    await session.flush()
    primary_url = await session.scalar(select(RawItem.url).where(RawItem.id == primary.raw_item_id))

    r = await (await auth_client("pm")).get(f"/api/v1/signals/{sig.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Sonatype ships a model registry"
    assert body["grade"] == "B2" and body["reliability"] == "B" and body["credibility"] == 2
    assert body["competitors"] == ["sonatype"]
    assert body["score_breakdown"] == breakdown
    assert body["role_actions"] == {
        "sales": "Ask about scale.",
        "pm": "",
        "exec": None,
        "pmm_devrel": "Angle.",
    }
    assert (body["primary_url"], body["primary_source"]) == (primary_url, "Sonatype Blog")
    assert body["evidence"][0] == {
        "quote": "verified words here",
        "verified": True,
        "document_id": primary.id,
        "url": primary_url,
        "source_name": "Sonatype Blog",
    }
    assert [e["verified"] for e in body["evidence"]] == [True, False]
    docs = body["documents"]
    assert [(d["id"], d["is_primary"]) for d in docs] == [(primary.id, True), (other.id, False)]
    assert docs[0]["title"] == "Primary story" and docs[0]["source_name"] == "Sonatype Blog"
    assert docs[0]["published_at"].startswith("2026-10-14")
    assert body["capabilities"] == [{"slug": "model-registry", "name": cap.name}]
    assert [k["slug"] for k in body["kits"]] == ["ai-supply-chain"]
    related = [s["id"] for s in body["related"]]
    assert related[:2] == [near.id, far.id]
    assert sig.id not in related and hidden.id not in related


# ---- signals: q (hybrid search) ---------------------------------------------------------


@pytest.fixture
def wire(session_factory, accounted_llm):
    def _wire(script=None, **kw):
        llm = accounted_llm(script, **kw)
        app.dependency_overrides[get_optional_llm] = lambda: llm
        app.dependency_overrides[get_session_factory] = lambda: session_factory
        return llm

    return _wire


@pytest.fixture
def indexed(session_factory, accounted_llm, make_signal):
    """Factory: a signal indexed into retrieval_items with fake embeddings."""
    llm = accounted_llm()

    async def _make(**kw) -> Signal:
        sig = await make_signal(**kw)
        await index_signal(session_factory, llm, sig.id, ctx=CTX)
        return sig

    return _make


async def test_signals_q_uses_hybrid(auth_client, indexed, wire):
    vector_only = await indexed(title="Model hub", fact="Registry #cluster:reg", impact="x")
    other = await indexed(title="Quarterly earnings", fact="Revenue grew", impact="y")
    rejected = await indexed(
        title="Model hub copy", fact="Registry #cluster:reg", impact="x", status="rejected"
    )
    gitlab = await indexed(
        title="GitLab registry", fact="Registry #cluster:reg", impact="z", competitors=["gitlab"]
    )
    llm = wire()

    c = await auth_client("pm")
    r = await c.get("/api/v1/signals", params={"q": "zzqq #cluster:reg", "limit": 2})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["search_mode"] == "hybrid" and page["next_cursor"] is None
    ids = _ids(page)
    assert ids[0] in (vector_only.id, gitlab.id) and rejected.id not in ids
    assert ("search_embed", "embed") in llm.backend.calls

    full = (await c.get("/api/v1/signals", params={"q": "zzqq #cluster:reg"})).json()
    # the unrelated vector-only tail (cosine ~0 here) is dropped
    assert set(_ids(full)) == {vector_only.id, gitlab.id} and other.id not in _ids(full)
    filtered = (
        await c.get("/api/v1/signals", params={"q": "zzqq #cluster:reg", "competitor": "gitlab"})
    ).json()
    assert _ids(filtered) == [gitlab.id]


async def test_signals_q_vector_floor_keeps_keyword_hits(auth_client, indexed, wire):
    """/signals?q keeps every keyword-arm hit, however far its vector, and drops
    vector-only hits below max(0.30, best - 0.12)."""
    # every query word, but no "#cluster:nx" tag: a keyword hit with an unrelated vector
    nexus = await indexed(title="Nexus repository update", fact="Proxy cluster nx", impact="x")
    near = await indexed(title="Model hub", fact="Registry #cluster:nx", impact="x")
    await indexed(title="Quarterly earnings", fact="Revenue grew", impact="y")
    wire()
    c = await auth_client("pm")
    page = (await c.get("/api/v1/signals", params={"q": "nexus #cluster:nx"})).json()
    assert page["search_mode"] == "hybrid"
    assert set(_ids(page)) == {nexus.id, near.id}


def _axis() -> list[float]:
    v = [0.0] * 512
    v[0] = 1.0
    return v


def _at(cos: float) -> list[float]:
    """A unit vector whose cosine similarity with `_axis()` is `cos`."""
    v = [0.0] * 512
    v[0], v[1] = cos, (1 - cos * cos) ** 0.5
    return v


async def test_signals_q_floor_is_taken_within_the_facets_and_visibility(
    auth_client, session, make_signal, wire
):
    """The vector floor (best - 0.12) is computed over the signals the reader asked for
    and may see, as /search does, so a closer match outside them never drops theirs."""
    from app.models.rag import RetrievalItem

    async def vec(cos: float, **kw) -> Signal:
        sig = await make_signal(**kw)
        item = RetrievalItem(
            kind="signal", ref_id=sig.id, chunk_no=0, text="no words", embedding=_at(cos)
        )
        session.add(item)
        await session.flush()
        return sig

    outside = await vec(0.8, competitors=["sonatype"])  # a better match, outside the facet
    hidden = await vec(0.95, competitors=["gitlab"], status="rejected")  # no one sees it
    best = await vec(0.6, competitors=["gitlab"])
    near = await vec(0.5, competitors=["gitlab"])  # within 0.12 of the facet's best
    await vec(0.4, competitors=["gitlab"])  # too far even within the facet
    wire({"search_embed": lambda texts, **_: [_axis() for _ in texts]})
    c = await auth_client("pm")

    page = (await c.get("/api/v1/signals", params={"q": "zzqq", "competitor": "gitlab"})).json()
    assert page["search_mode"] == "hybrid"
    assert _ids(page) == [best.id, near.id]
    everyone = (await c.get("/api/v1/signals", params={"q": "zzqq"})).json()
    assert _ids(everyone) == [outside.id]  # 0.8 sets the bar once sonatype is in scope
    assert hidden.id not in _ids(everyone)


async def test_signals_q_keyword_only_without_key(
    auth_client, indexed, session_factory, monkeypatch
):
    """No OpenAI key -> keyword-only search (never a 503), labelled search_mode."""
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    monkeypatch.setattr(deps, "_llm_cache", None)
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    nexus = await indexed(title="Nexus repository update", fact="Proxy repos", impact="x")
    await indexed(title="Quarterly earnings", fact="Revenue grew", impact="y")

    r = await (await auth_client("pm")).get("/api/v1/signals", params={"q": "nexus"})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["search_mode"] == "keyword"
    assert _ids(page) == [nexus.id]


@pytest.mark.parametrize("failure", ["llm_error", "budget"])
async def test_signals_q_keyword_fallback_on_embed_failure(
    auth_client, indexed, wire, test_settings_override, failure
):
    nexus = await indexed(title="Nexus repository update", fact="Proxy repos", impact="x")
    await indexed(title="Quarterly earnings", fact="Revenue grew", impact="y")
    if failure == "llm_error":
        wire({"search_embed": [RuntimeError("embeddings down")]})
    else:
        wire(settings=test_settings_override(llm_daily_budget_usd=0.0))

    r = await (await auth_client("pm")).get("/api/v1/signals", params={"q": "nexus"})
    assert r.status_code == 200, r.text
    assert r.json()["search_mode"] == "keyword" and _ids(r.json()) == [nexus.id]


async def test_signals_q_rate_limited(auth_client, seeded, wire, monkeypatch):
    wire()
    monkeypatch.setattr(signals_api.limiter, "limit", 2)
    c = await auth_client("pm")
    codes = [(await c.get("/api/v1/signals", params={"q": "nexus"})).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert (await c.get("/api/v1/signals")).status_code == 200  # plain listing is not limited


async def test_search_and_signals_q_have_independent_buckets(
    auth_client, seeded, wire, session_factory, accounted_llm, monkeypatch
):
    """/search and /signals?q are limited per user by separate buckets (one limiter
    implementation, two instances)."""
    from app.api.deps import get_llm
    from app.api.v1 import search as search_api

    plan = {
        "rewritten": "nexus",
        "variants": [],
        "competitor_slugs": [],
        "date_from": None,
        "date_to": None,
        "kinds": [],
    }
    wire()
    search_llm = accounted_llm({"query_understanding": lambda **_: plan})
    app.dependency_overrides[get_llm] = lambda: search_llm
    monkeypatch.setattr(search_api.limiter, "limit", 1)
    monkeypatch.setattr(signals_api.limiter, "limit", 1)
    search_api.limiter.reset()
    try:
        c = await auth_client("pm")
        assert (await c.get("/api/v1/search", params={"q": "nexus"})).status_code == 200
        assert (await c.get("/api/v1/signals", params={"q": "nexus"})).status_code == 200
        assert (await c.get("/api/v1/search", params={"q": "nexus"})).status_code == 429
        assert (await c.get("/api/v1/signals", params={"q": "nexus"})).status_code == 429
        assert search_api.limiter is not signals_api.limiter
    finally:
        search_api.limiter.reset()
        app.dependency_overrides.pop(get_llm, None)


async def test_signals_q_validates_length(auth_client, seeded, wire):
    wire()
    c = await auth_client("pm")
    assert (await c.get("/api/v1/signals", params={"q": "a"})).status_code == 422
    assert (await c.get("/api/v1/signals", params={"q": "a" * 301})).status_code == 422


async def test_signals_q_holds_no_transaction_across_embed(
    auth_client, seeded, session_factory, wire
):
    """The auth session is rolled back before the embedding await."""
    from app.core.db import get_session
    from tests.agents.fakes import fake_embedding

    sessions, seen = [], []

    async def _override():
        async with session_factory() as s:
            sessions.append(s)
            yield s

    def _embed(texts, dims, **_):
        seen.append([s.in_transaction() for s in sessions])
        return [fake_embedding(t, dims) for t in texts]

    wire({"search_embed": _embed})
    app.dependency_overrides[get_session] = _override
    c = await auth_client("pm")
    sessions.clear()
    r = await c.get("/api/v1/signals", params={"q": "nexus"})
    assert r.status_code == 200, r.text
    assert r.json()["search_mode"] == "hybrid"
    assert seen and sessions and seen == [[False] * len(sessions)]


# ---- agent runs --------------------------------------------------------------------------


async def _agent_run(session, n: int, **kw) -> AgentRun:
    run = AgentRun(
        graph="daily_intel",
        thread_id=f"thread-{n}",
        trigger=kw.pop("trigger", "manual"),
        status=kw.pop("status", "succeeded"),
        started_at=kw.pop("started_at", datetime(2026, 10, 17, 3, n, tzinfo=UTC)),
        **kw,
    )
    session.add(run)
    await session.flush()
    return run


async def test_agent_runs_list_newest_first_with_funnel(auth_client, seeded, session):
    stats = {"fetched": 40, "relevant": 6, "briefing_status": "published", "sources_failed": 1}
    pipeline = PipelineRun(kind="daily", trigger="manual", status="succeeded", stats=stats)
    session.add(pipeline)
    await session.flush()
    older = await _agent_run(
        session,
        1,
        pipeline_run_id=pipeline.id,
        tokens_in=1200,
        tokens_out=300,
        cost_usd=Decimal("0.012345"),
        notes="llm_disabled",
        finished_at=datetime(2026, 10, 17, 3, 30, tzinfo=UTC),
    )
    newer = await _agent_run(session, 2, status="running")

    c = await auth_client("sales")
    rows = (await c.get("/api/v1/agent-runs")).json()
    assert [r["id"] for r in rows] == [newer.id, older.id]
    assert rows[0]["funnel"] is None and rows[0]["finished_at"] is None
    o = rows[1]
    assert (o["graph"], o["trigger"], o["status"]) == ("daily_intel", "manual", "succeeded")
    assert (o["tokens_in"], o["tokens_out"], o["notes"]) == (1200, 300, "llm_disabled")
    assert o["cost_usd"] == pytest.approx(0.012345)
    assert o["pipeline_run_id"] == pipeline.id
    assert o["funnel"] == {"fetched": 40, "relevant": 6, "briefing_status": "published"}
    assert len((await c.get("/api/v1/agent-runs", params={"limit": 1})).json()) == 1
    one = (await c.get(f"/api/v1/agent-runs/{older.id}")).json()
    assert one == o
    assert (await c.get("/api/v1/agent-runs/999999")).status_code == 404


async def test_agent_run_steps_paged(auth_client, seeded, session):
    run = await _agent_run(session, 3)
    for n in range(1, 6):
        session.add(
            AgentStep(
                agent_run_id=run.id,
                step_no=n,
                node="triage",
                agent="triage" if n % 2 else None,
                kind="llm" if n % 2 else "tool",
                tool_name=None if n % 2 else "search_intel",
                args={"n": n},
                result_summary=f"step {n}",
                model="gpt-5-nano" if n % 2 else None,
                tokens_in=10 * n,
                cached_tokens=n,
                tokens_out=n,
                cost_usd=Decimal("0.000100"),
                latency_ms=5,
            )
        )
    await session.flush()

    c = await auth_client("analyst")  # args are analyst-only (test_agent_runs_api.py)
    url = f"/api/v1/agent-runs/{run.id}/steps"
    page = (await c.get(url, params={"after_step": 2, "limit": 2})).json()
    assert [s["step_no"] for s in page] == [3, 4]
    step4 = page[1]
    assert (step4["kind"], step4["tool_name"], step4["args"]) == ("tool", "search_intel", {"n": 4})
    assert step4["result_summary"] == "step 4" and step4["cost_usd"] == pytest.approx(0.0001)
    assert [s["step_no"] for s in (await c.get(url)).json()] == [1, 2, 3, 4, 5]
    assert (await c.get(url, params={"after_step": 5})).json() == []
    assert (await c.get("/api/v1/agent-runs/999999/steps")).status_code == 404


# ---- costs -------------------------------------------------------------------------------


async def test_costs_aggregates_local_day(auth_client, seeded, session, settings, monkeypatch):
    # 21:30 UTC on Oct 20 is 00:30 on Oct 21 in Asia/Jerusalem (UTC+3 until Oct 25, 2026).
    now = datetime(2026, 10, 20, 21, 30, tzinfo=UTC)
    monkeypatch.setattr(costs_api, "utcnow", lambda: now)

    def call(at, cost, task="triage", model="gpt-5-nano", status="ok"):
        return LlmCall(
            task=task,
            model=model,
            request_hash=f"h-{at.isoformat()}-{task}",
            cost_usd=Decimal(cost),
            latency_ms=10,
            status=status,
            created_at=at,
        )

    session.add_all(
        [
            call(datetime(2026, 10, 20, 21, 10, tzinfo=UTC), "0.10"),  # local Oct 21 00:10
            call(datetime(2026, 10, 20, 20, 50, tzinfo=UTC), "0.20", "analyst", "gpt-6-luna"),
            call(datetime(2026, 10, 15, 10, 0, tzinfo=UTC), "0.05"),  # local Oct 15
            call(datetime(2026, 10, 15, 11, 0, tzinfo=UTC), "0", status="cached"),
            call(datetime(2026, 10, 10, 10, 0, tzinfo=UTC), "1.00", "analyst", "gpt-6-luna"),
        ]
    )
    await session.flush()

    r = await (await auth_client("exec")).get("/api/v1/costs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["today_usd"] == pytest.approx(0.10)  # local day, not the UTC day (0.30)
    assert body["total_usd"] == pytest.approx(1.35)
    assert body["daily_budget_usd"] == settings.llm_daily_budget_usd
    assert body["total_budget_usd"] == settings.llm_total_budget_usd
    days = body["last_7_days"]
    assert [d["date"] for d in days] == [str(date(2026, 10, 15) + timedelta(i)) for i in range(7)]
    assert [round(d["cost_usd"], 6) for d in days] == [0.05, 0, 0, 0, 0, 0.20, 0.10]
    by_task = {t["task"]: t for t in body["by_task"]}
    assert by_task["analyst"]["calls"] == 2
    assert by_task["analyst"]["cost_usd"] == pytest.approx(1.20)
    assert by_task["triage"]["calls"] == 3
    assert by_task["triage"]["cost_usd"] == pytest.approx(0.15)
    assert [t["task"] for t in body["by_task"]] == ["analyst", "triage"]  # costliest first
    by_model = {m["model"]: m for m in body["by_model"]}
    assert by_model["gpt-5-nano"] == {
        "model": "gpt-5-nano",
        "calls": 3,
        "cost_usd": pytest.approx(0.15),
        "cached_calls": 1,
    }
    assert by_model["gpt-6-luna"]["cached_calls"] == 0


async def test_costs_requires_auth(client, seeded):
    assert (await client.get("/api/v1/costs")).status_code == 401
    assert (await client.get("/api/v1/agent-runs")).status_code == 401


async def test_aggregator_items_are_attributed_to_their_outlet(
    auth_client, session, make_signal, make_source, make_document
):
    """A Google News item names the outlet behind it (the payload keeps it),
    never the generated source "<Competitor> · Google News", which reads as if the
    competitor published it. A GDELT item names its domain the same way."""
    gnews = await make_source(kind="google_news", name="JFrog · Google News", reliability="C")
    gdelt = await make_source(kind="gdelt", name="Sonatype · GDELT", reliability="B")
    via_gnews = await make_document(source_id=gnews.id, title="JFrog and Hugging Face")
    via_gdelt = await make_document(source_id=gdelt.id, title="Same story")
    unnamed = await make_document(source_id=gnews.id, title="An outlet the feed omits")
    for doc, payload in (
        (via_gnews, {"outlet": "ET Now", "outlet_url": "https://www.etnownews.com"}),
        (via_gdelt, {"domain": "devops.com"}),
        (unnamed, {"outlet": None, "outlet_url": None}),
    ):
        (await session.get(RawItem, doc.raw_item_id)).payload = payload
    sig = await make_signal(
        documents=[via_gnews, via_gdelt, unnamed],
        evidence=[(via_gnews, "a quote from the outlet")],
        status="auto",
    )
    await session.flush()

    c = await auth_client("pm")
    body = (await c.get(f"/api/v1/signals/{sig.id}")).json()
    assert body["primary_source"] == "ET Now via Google News"
    names = {d["id"]: d["source_name"] for d in body["documents"]}
    assert names == {
        via_gnews.id: "ET Now via Google News",
        via_gdelt.id: "devops.com via GDELT",
        unnamed.id: "Google News",
    }
    assert body["evidence"][0]["source_name"] == "ET Now via Google News"
    listed = (await c.get("/api/v1/signals")).json()["items"]
    assert (
        next(s for s in listed if s["id"] == sig.id)["primary_source"] == "ET Now via Google News"
    )


# ---- signals: analysis, numbered sources ---------------------------------------


def _analysis(summary="Short summary.", cites=(), **extra):
    from app.agents.analysis_schema import CitedText, SignalAnalysisContent

    return SignalAnalysisContent(
        summary=CitedText(text=summary, cites=list(cites)),
        what_happened=[CitedText(text="It happened.", cites=list(cites)[::-1])],
        **extra,
    )


async def _store(session, signal_id, content):
    from app.agents.analysis_store import persist_analysis

    return await persist_analysis(
        session, signal_id, content, model="m", prompt_version="1", cost_usd=0
    )


async def test_detail_includes_analysis_with_numbered_sources(
    auth_client, session, make_document, make_signal
):
    from app.models import JfrogKbDoc

    primary = await make_document(
        title="Primary story", published_at=datetime(2026, 10, 14, tzinfo=UTC)
    )
    other = await make_document(title="Follow-up", published_at=datetime(2026, 10, 15, tzinfo=UTC))
    kb = JfrogKbDoc(url="https://jfrog.com/help/x", title="Xray docs", text="t", content_hash="h")
    session.add(kb)
    prior = await make_signal(title="Earlier launch")
    sig = await make_signal(documents=[primary, other])
    await session.flush()
    await _store(
        session,
        sig.id,
        _analysis(cites=[f"signal:{prior.id}", f"kb:{kb.id}", f"doc:{other.id}", "kb:999999"]),
    )

    body = (await (await auth_client("pm")).get(f"/api/v1/signals/{sig.id}")).json()
    assert body["analysis_state"] == "current"
    sources = body["sources"]
    assert [s["n"] for s in sources] == [1, 2, 3, 4]
    assert sources[0]["is_primary"] and sources[0]["ref"] == f"doc:{primary.id}"
    assert [s["kind"] for s in sources] == ["document", "document", "kb", "signal"]
    assert sources[2]["url"] == "https://jfrog.com/help/x" and sources[2]["title"] == "Xray docs"
    assert sources[3]["url"] == f"/signals/{prior.id}" and sources[3]["title"] == "Earlier launch"
    analysis = body["analysis"]
    assert analysis["summary"]["cites"] == [4, 3, 2]  # the unknown kb:999999 is dropped
    numbers = {s["n"] for s in sources}
    for cite in analysis["summary"]["cites"] + analysis["what_happened"][0]["cites"]:
        assert cite in numbers
    assert body["summary"] == "Short summary."


def test_openapi_lists_every_always_sent_field_as_required():
    """The API always sends every action list and the analysis state, so the
    generated client types must not make them optional (as the briefing schemas do)."""
    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["Actions"]["required"]) == {"sales", "pm", "exec", "pmm_devrel"}
    assert set(schemas["SalesActions"]["required"]) == {
        "talk_track",
        "discovery_questions",
        "objections",
        "landmines",
    }
    assert "risk_level" in schemas["ExecActions"]["required"]
    assert "analysis_state" in schemas["SignalCard"]["required"]
    assert schemas["SignalCard"]["properties"]["analysis_state"]["enum"] == [
        "current",
        "stale",
        "pending",
        "thin",
        "below_threshold",
    ]


async def test_source_titles_never_show_a_url(auth_client, session, make_document, make_signal):
    """A KB page whose stored title is its URL (every crawled page today)
    shows its product name from jfrog_kb.yaml; a document without a title, or whose title is
    a URL, reads "Untitled page on <host>" (the Feed's wording). No scheme ever shows."""
    from app.models import JfrogKbDoc

    untitled = await make_document(title=None, clean_text="x" * 2000)
    url_titled = await make_document(title="https://example.com/a/b", clean_text="x" * 2000)
    curation = JfrogKbDoc(
        url="https://jfrog.com/curation/",
        title="https://jfrog.com/curation/",
        text="t",
        content_hash="h",
    )
    unlisted = JfrogKbDoc(
        url="https://jfrog.com/help/x", title="https://jfrog.com/help/x", text="t", content_hash="i"
    )
    session.add_all([curation, unlisted])
    sig = await make_signal(documents=[untitled, url_titled])
    await session.flush()
    await _store(session, sig.id, _analysis(cites=[f"kb:{curation.id}", f"kb:{unlisted.id}"]))

    body = (await (await auth_client("pm")).get(f"/api/v1/signals/{sig.id}")).json()
    titles = [s["title"] for s in body["sources"]]
    assert titles == [
        "Untitled page on example.com",
        "Untitled page on example.com",
        "Curation",
        "Untitled page on jfrog.com",
    ]
    assert not any("://" in t for t in titles)


async def test_detail_analysis_states(auth_client, session, make_document, make_signal):
    c = await auth_client("pm")
    doc = await make_document(title="Doc", clean_text="x" * 2000)
    current = await make_signal(documents=[doc])
    await _store(session, current.id, _analysis())
    stale = await make_signal(documents=[doc])
    await _store(session, stale.id, _analysis("Old summary."))
    stale.analyzed_at = datetime.now(UTC) + timedelta(minutes=5)
    none = await make_signal(documents=[doc])
    thin_doc = await make_document(title="Headline", clean_text="")
    thin = await make_signal(documents=[thin_doc])
    await session.flush()

    async def get(sig):
        return (await c.get(f"/api/v1/signals/{sig.id}")).json()

    assert (await get(current))["analysis_state"] == "current"
    body = await get(stale)
    assert body["analysis_state"] == "stale"
    assert body["analysis"]["summary"]["text"] == "Old summary."
    assert body["summary"] == "Old summary."
    body = await get(none)
    assert (body["analysis_state"], body["analysis"], body["summary"]) == ("pending", None, None)
    assert len(body["sources"]) == 1  # member documents always listed
    body = await get(thin)
    assert (body["analysis_state"], body["analysis"]) == ("thin", None)


async def test_analysis_state_says_why_there_is_no_analysis(
    auth_client, session, make_source, make_document, make_signal, settings
):
    """One state with a reason, from the rule stage-B selection uses (the thin-evidence
    rule, the importance floor); cards carry it too, so a filed briefing marks the live state."""
    c = await auth_client("pm")
    long_doc = await make_document(title="Doc", clean_text="x" * 2000)
    pending = await make_signal(documents=[long_doc], importance=60)
    low = await make_signal(documents=[long_doc], importance=settings.analysis_min_importance - 1)
    own = await make_source(name="Vendor blog", reliability="A")  # first party
    terse = await make_document(source_id=own.id, title="Pro+", clean_text="y" * 153)
    short_first_party = await make_signal(documents=[terse], importance=60)
    gutted = await make_signal(documents=[long_doc], importance=60)
    from app.agents.analysis_schema import CitedText, SignalAnalysisContent

    await _store(  # what an incomplete fact-check left behind on a live run
        session, gutted.id, SignalAnalysisContent(summary=CitedText(text="Only.", cites=[]))
    )
    await session.flush()

    async def state(sig):
        body = (await c.get(f"/api/v1/signals/{sig.id}")).json()
        return body["analysis_state"], body["analysis"], body["summary"]

    assert await state(pending) == ("pending", None, None)
    assert await state(low) == ("below_threshold", None, None)
    assert await state(short_first_party) == ("thin", None, None)
    assert await state(gutted) == ("pending", None, None)  # re-analysed by the next run
    cards = {s["id"]: s for s in (await c.get("/api/v1/signals?limit=200")).json()["items"]}
    assert cards[pending.id]["analysis_state"] == "pending"
    assert cards[low.id]["analysis_state"] == "below_threshold"
    assert cards[short_first_party.id]["analysis_state"] == "thin"
    assert (cards[gutted.id]["analysis_state"], cards[gutted.id]["summary"]) == ("pending", None)


@pytest.mark.parametrize(
    "stored",
    [
        {"jfrog_impact": [{"product": "Xray", "effect": "sideways"}]},  # unknown enum
        {"what_to_watch": [{"date": "soon"}]},  # bad date
        {"summary": None},  # explicit null
    ],
)
async def test_unparseable_analysis_is_no_analysis(
    auth_client, session, make_document, make_signal, stored
):
    """A stored analysis that fails to parse is no analysis, never a 500."""
    from app.models.gold import SignalAnalysis

    doc = await make_document(title="Doc", clean_text="x" * 2000)
    sig = await make_signal(documents=[doc])
    session.add(
        SignalAnalysis(signal_id=sig.id, version=1, content=stored, model="m", prompt_version="9")
    )
    await session.flush()
    c = await auth_client("pm")
    r = await c.get(f"/api/v1/signals/{sig.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["analysis"], body["analysis_state"], body["summary"]) == (None, "pending", None)
    listed = (await c.get("/api/v1/signals")).json()["items"]
    assert next(s for s in listed if s["id"] == sig.id)["summary"] is None


async def test_cited_hidden_signal_not_linked(auth_client, session, make_document, make_signal):
    doc = await make_document(title="Doc", clean_text="x" * 2000)
    prior = await make_signal(title="Secret prior story")
    sig = await make_signal(documents=[doc])
    await _store(session, sig.id, _analysis(cites=[f"signal:{prior.id}"]))
    prior.status = "rejected"
    await session.flush()

    r = await (await auth_client("pm")).get(f"/api/v1/signals/{sig.id}")
    assert "Secret prior story" not in r.text
    source = r.json()["sources"][-1]
    assert source["kind"] == "signal" and source["url"] is None
    assert r.json()["analysis"]["summary"]["cites"] == [source["n"]]


async def test_card_summaries_are_batch_loaded(auth_client, session, make_signal):
    a = await make_signal(importance=90)
    b = await make_signal(importance=80)
    await _store(session, a.id, _analysis("Summary A."))
    await session.flush()
    items = (await (await auth_client("pm")).get("/api/v1/signals")).json()["items"]
    by = {s["id"]: (s["summary"], s["analysis_state"]) for s in items}
    assert by[a.id] == ("Summary A.", "current") and by[b.id] == (None, "pending")


async def test_many_products_and_actions_serialize(
    auth_client, session, make_document, make_signal
):
    from app.agents.analysis_schema import (
        JFROG_PRODUCTS,
        Actions,
        PmActions,
        ProductImpact,
        SalesActions,
    )

    doc = await make_document(title="Doc", clean_text="x" * 2000)
    sig = await make_signal(documents=[doc])
    content = _analysis(
        jfrog_impact=[
            ProductImpact(product=p, effect="threat", severity="high", rationale=f"R {p}")
            for p in JFROG_PRODUCTS
        ],
        recommended_actions=Actions(
            sales=SalesActions(talk_track=[f"t{i}" for i in range(10)]),
            pm=PmActions(
                assessment=[f"a{i}" for i in range(10)], follow_ups=[f"f{i}" for i in range(10)]
            ),
        ),
    )
    await _store(session, sig.id, content)
    body = (await (await auth_client("pm")).get(f"/api/v1/signals/{sig.id}")).json()
    assert len(body["analysis"]["jfrog_impact"]) == 13
    actions = body["analysis"]["recommended_actions"]
    total = (
        len(actions["sales"]["talk_track"])
        + len(actions["pm"]["assessment"])
        + len(actions["pm"]["follow_ups"])
    )
    assert total == 30
