"""Read APIs for competitors: the overview, the deep-dive profile and paged postings.

Rules under test: the deep dive is identical for every persona -- aggregates and signal
cards alike read public signals, the cards also within the reader's visibility; JFrog itself is
never served; facts are grouped per (kind, label, as-of month), a named funding round across months,
with every source and never averaged; a competitor with no data renders every section
empty, honest and explained; the profile is a bounded number of
statements.
"""

import base64
import json
from datetime import date

import pytest
from sqlalchemy import delete, select

from app.api import intel_out
from app.intel.fact_checks import normalise_label
from app.main import app
from app.models import (
    CompanyFact,
    Competitor,
    CompetitorBrief,
    CompetitorProduct,
    DocumentCompetitor,
    JobPosting,
    MetricPoint,
    PageSnapshot,
    Source,
)
from tests.intel.test_profile import NOW, ago, analyse, at, hit, new_comp, signal

PROFILE_SECTIONS = {
    "competitor",
    "kpis",
    "brief",
    "products",
    "momentum",
    "timeline",
    "hiring",
    "headcount",
    "money",
    "market",
    "customers",
    "pricing",
    "community",
    "people_partners",
    "latest_signals",
    "collected_since",  # the day collection began (not a section)
    "sources_freshness",
}
SERIES = ("total", "department", "seniority", "location", "tag")
SECTIONS = {  # schemas.intel.ProfileSection: every one carries a note when empty
    "overview",
    "products",
    "momentum",
    "hiring",
    "headcount",
    "money",
    "market",
    "customers",
    "pricing",
    "community",
    "people_partners",
    "latest_signals",
    "sources",
}
PENDING = "Job board found — first snapshot pending"


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    monkeypatch.setattr(intel_out, "utcnow", lambda: NOW)


# ---- builders ------------------------------------------------------------------------------


async def board(
    make_source, comp, *, enabled: bool = True, fetched: bool = True, listed: int = 0, **kw
):
    """A job board; `fetched` gives it a complete listing (a first snapshot), `listed` days
    ago (the board's history starts at its first complete listing)."""
    return await make_source(
        kind="job_board",
        name=kw.pop("name", f"{comp.name} jobs"),
        competitor_id=comp.id,
        enabled=enabled,
        config={"ats": "lever"},
        last_fetched_at=NOW if fetched else None,
        first_complete_fetch_at=at(ago(listed)) if fetched else None,
        last_complete_fetch_at=NOW if fetched else None,  # listed in full today
        **kw,
    )


def posting(comp, src, n, *, seen: int = 10, closed: int | None = None, **kw) -> JobPosting:
    return JobPosting(
        source_id=src.id,
        competitor_id=comp.id,
        ats="lever",
        external_id=f"{comp.slug}-{n}",
        title=kw.pop("title", f"Role {n}"),
        department=kw.pop("department", "Engineering"),
        seniority=kw.pop("seniority", "Mid"),
        content_hash=f"{comp.slug}-h{n}",
        first_seen_at=at(ago(seen)),
        closed_at=at(ago(closed)) if closed is not None else None,
        url=f"https://jobs.example.com/{comp.slug}/{n}",
        tags=list(kw.pop("tags", [])),
        **kw,
    )


async def fact(session, comp, kind, label, *, source, value=None, as_of=None, **kw):
    """A verified fact as the extractor stores it, from a document or a page snapshot."""
    row = CompanyFact(
        competitor_id=comp.id,
        kind=kind,
        label=label,
        label_norm=normalise_label(kind, label),
        value_num=value,
        unit=kw.pop("unit", None),
        currency=kw.pop("currency", None),
        as_of=as_of,
        source_kind="page_snapshot" if isinstance(source, PageSnapshot) else "document",
        source_id=source.id,
        quote=kw.pop("quote", f"The source states the {label} in a full sentence."),
        verified=True,
        extractor_version="1",
    )
    session.add(row)
    await session.flush()
    return row


async def news(make_document, src, title: str, published: date):
    return await make_document(
        source_id=src.id, title=title, published_at=at(published), status="relevant"
    )


async def jfrog(session) -> Competitor:
    return (await session.execute(select(Competitor).where(Competitor.is_self))).scalar_one()


@pytest.fixture
async def acme(seeded, session, competitor_factory, make_signal, make_source, make_document):
    """A competitor with every kind of data."""
    comp = await new_comp(
        session,
        competitor_factory,
        slug="acme",
        name="Acme",
        tier=1,
        website="https://acme.example",
        ticker="ACME",
        description="Acme makes artifact registries.",
    )
    session.add_all(
        [
            CompetitorProduct(
                competitor_id=comp.id,
                name="Acme Repo",
                category="Artifact repository",
                url="https://acme.example/repo",
                jfrog_products=["Artifactory", "Container Registry"],
            ),
            CompetitorProduct(
                competitor_id=comp.id,
                name="Acme Scan",
                category="Software composition analysis",
                url=None,
                jfrog_products=["Xray"],
            ),
        ]
    )
    w: dict = {"comp": comp}
    blog = await make_source(name="Acme blog", competitor_id=comp.id)
    wire = await make_source(name="Tech wire")
    hn = await make_source(
        kind="hn", name="HN: Acme", url="generated://hn/acme", competitor_id=comp.id
    )
    await make_source(
        kind="github_repo_stats",
        name="acme/acme stats",
        url="https://api.github.com/repos/acme/acme",
        competitor_id=comp.id,
    )
    pricing = await make_source(
        kind="page_watch",
        name="Acme · pricing page",
        url="https://acme.example/pricing",
        competitor_id=comp.id,
        config={"page": "pricing", "url": "https://acme.example/pricing"},
    )
    about = await make_source(
        kind="page_watch",
        name="Acme · about page",
        url="https://acme.example/about",
        competitor_id=comp.id,
        config={"page": "about", "url": "https://acme.example/about"},
    )
    await make_source(name="Acme old feed", competitor_id=comp.id, enabled=False)
    jobs = await board(make_source, comp, listed=60)

    # signals: public ones of several lanes, and three that only some readers may see
    w["launch"] = await signal(
        make_signal, comp, ago(3), signal_type="product_launch", title="Acme Repo 2", importance=70
    )
    await analyse(session, w["launch"], hit("Artifactory"), hit("Xray", severity="medium"))
    w["win"] = await signal(
        make_signal, comp, ago(10), signal_type="customer_win_loss", title="Acme wins BigBank"
    )
    await analyse(session, w["win"], hit("Artifactory"))
    w["partner"] = await signal(
        make_signal, comp, ago(40), signal_type="partnership", title="Acme partners with CloudCo"
    )
    w["cto"] = await signal(
        make_signal, comp, ago(70), signal_type="leadership_change", title="Acme names a CTO"
    )
    w["price"] = await signal(
        make_signal, comp, ago(100), signal_type="pricing_change", title="Acme cuts prices"
    )
    await analyse(session, w["price"], hit("Artifactory", severity="medium"))
    w["review"] = await signal(
        make_signal, comp, ago(2), status="needs_review", title="Acme rumour under review"
    )
    w["rejected"] = await signal(
        make_signal, comp, ago(5), status="rejected", title="Acme story the desk rejected"
    )
    w["own"] = await signal(
        make_signal, comp, ago(1), scope="jfrog", title="JFrog-scope story naming Acme"
    )

    # facts: two sources disagree on September's headcount; a dated and an undated Series B
    a = await news(make_document, blog, "Acme raises its Series B", date(2026, 6, 10))
    b = await news(make_document, wire, "Acme Series B, now valued higher", date(2026, 6, 12))
    c = await news(make_document, wire, "Acme's year so far", date(2026, 3, 2))
    d = await news(make_document, wire, "Acme hires 1,200", date(2026, 9, 2))
    e = await news(make_document, blog, "Acme at 1,250 people", date(2026, 9, 20))
    f = await news(make_document, wire, "A look at Acme", date(2026, 8, 3))
    snap_about = PageSnapshot(
        source_id=about.id,
        fetched_at=at(ago(15)),
        text_hash="about-1",
        text="About Acme. Offices in Austin, United States.",
        page_kind="about",
    )
    session.add(snap_about)
    await session.flush()
    staff, usd, pct = {"unit": "employees"}, {"currency": "USD"}, {"unit": "%"}
    table = [
        # (kind, label, source, value, as_of, extra)
        ("headcount", "employees", d, 1200, date(2026, 9, 1), staff),
        ("headcount", "employees", e, 1250, date(2026, 9, 15), staff),
        ("headcount", "employees", c, 900, date(2026, 3, 1), staff),
        ("headcount", "employees", f, 1100, None, staff),  # undated: never on the axis
        ("layoff", "layoff", c, 10, date(2026, 5, 4), pct),
        ("funding_round", "Series A", c, 10e6, date(2026, 1, 15), usd),
        ("funding_round", "Series B", a, 40e6, date(2026, 6, 10), usd),
        ("funding_round", "Series B round", b, 45e6, date(2026, 6, 12), usd),
        ("funding_round", "Series B", f, 40e6, None, usd),  # the same round, undated
        ("funding_round", "Seed", f, 2e6, None, usd),
        ("funding_round", "Debt financing", f, None, date(2026, 8, 1), {}),
        ("valuation", "valuation", b, 500e6, date(2026, 6, 12), usd),
        ("revenue", "FY2025 revenue", c, 80e6, date(2026, 2, 1), usd),
        ("arr", "ARR", f, 100e6, date(2026, 7, 1), usd),
        ("market_share", "container security, IDC", c, 12, date(2026, 3, 1), pct),
        ("market_share", "artifact management, Gartner", f, 7, None, pct),
        ("customer", "BigBank", f, None, date(2026, 8, 1), {}),
        ("customer", "SmallCo", f, None, None, {}),
        ("customer_count", "customers", f, 3000, date(2026, 8, 1), {"unit": "customers"}),
        ("leadership_change", "CTO: Jane Roe", f, None, date(2026, 7, 20), {}),
        ("partnership", "CloudCo", f, None, date(2026, 9, 5), {}),
        ("acquisition", "Tiny Inc", f, None, None, {}),
        ("office", "Austin, United States", snap_about, None, None, {}),
    ]
    facts = [
        await fact(session, comp, kind, label, source=src, value=value, as_of=as_of, **extra)
        for kind, label, src, value, as_of, extra in table
    ]
    w["series_b"] = facts[6]

    # the job board: open, closed and tagged postings
    p3 = posting(comp, jobs, 3, seen=60, closed=5, tags=["sca"], title="Closed SCA role")
    session.add_all(
        [
            posting(comp, jobs, 1, seen=50, tags=["sca"], title="Staff SCA Engineer"),
            posting(comp, jobs, 2, seen=20, department="Sales", tags=["sbom"]),
            p3,
            posting(comp, jobs, 4, seen=5, department="Security", tags=["sca", "sast"]),
            posting(comp, jobs, 5, seen=45, closed=40, seniority="Senior"),
        ]
    )
    # page watch: a baseline and a meaningful pricing change; an about-page change
    session.add(
        PageSnapshot(
            source_id=pricing.id,
            fetched_at=at(ago(30)),
            text_hash="p-1",
            text="Team $10",
            page_kind="pricing",
        )
    )
    w["price_change"] = PageSnapshot(
        source_id=pricing.id,
        fetched_at=at(ago(12)),
        text_hash="p-2",
        text="Team $12",
        diff_from_prev="@@ -1 +1 @@\n-Team $10\n+Team $12",
        page_kind="pricing",
        meaningful=True,
        change_kind="pricing",
        change_summary="Raised the Team tier from $10 to $12.",
        judged_at=at(ago(12)),
    )
    session.add(w["price_change"])
    session.add(
        PageSnapshot(
            source_id=about.id,
            fetched_at=at(ago(8)),
            text_hash="about-2",
            text="About Acme. New CEO.",
            diff_from_prev="-Offices\n+New CEO",
            page_kind="about",
            meaningful=True,
            change_kind="company",
            change_summary="Names a new CEO.",
            judged_at=at(ago(8)),
        )
    )

    # community: repo stats and Hacker News mentions
    for metric, day, value in (
        ("github_stars:acme/acme", ago(200), 50.0),  # outside the window
        ("github_stars:acme/acme", ago(10), 100.0),
        ("github_stars:acme/acme", ago(3), 120.0),
        ("github_forks:acme/acme", ago(3), 7.0),
    ):
        session.add(MetricPoint(competitor_id=comp.id, metric=metric, date=day, value=value))
    for days, status in ((3, "relevant"), (20, "relevant"), (4, "irrelevant")):
        doc = await make_document(
            source_id=hn.id, title=f"HN {days}", published_at=at(ago(days)), status=status
        )
        session.add(DocumentCompetitor(document_id=doc.id, competitor_id=comp.id))
    await session.flush()

    # the brief: an older version, and the current one citing every ref kind
    session.add(
        CompetitorBrief(
            competitor_id=comp.id,
            version=1,
            content={"bottom_line": [{"text": "Old bottom line.", "cites": []}]},
            model="m",
            prompt_version="1+check1",
            created_at=at(ago(14)),
        )
    )
    r = w["brief_refs"] = {
        "launch": f"signal:{w['launch'].id}",
        "series_b": f"fact:{w['series_b'].id}",
        "posting": f"posting:{p3.id}",
        "page": f"page:{w['price_change'].id}",
        "rejected": f"signal:{w['rejected'].id}",
    }
    edge = {"text": "Cheaper per seat.", "cites": [r["page"], "fact:999999"]}
    session.add(
        CompetitorBrief(
            competitor_id=comp.id,
            version=2,
            content={
                "bottom_line": [
                    {"text": "Acme pushes into registries.", "cites": [r["launch"], r["series_b"]]},
                    {"text": "Acme is hiring for SCA.", "cites": [r["posting"], r["launch"]]},
                    {"text": "Acme raised its Team price.", "cites": [r["page"]]},
                ],
                "product_edges": [
                    {"product": "Acme Repo", "jfrog_product": "Artifactory", "edge": edge}
                ],
                "watch": [{"text": "Watch the reorganisation.", "cites": [r["rejected"]]}],
                "evidence": {"signals": 3, "facts": 1, "postings": 1, "pages": 1},
                "input_hash": "h",
            },
            model="m",
            prompt_version="1+check1",
            created_at=at(ago(2)),
        )
    )
    await session.flush()
    return w


@pytest.fixture
async def quiet(seeded, session, competitor_factory):
    """A competitor with no board, no facts and no signals."""
    return await new_comp(session, competitor_factory, slug="quiet", name="Quiet Co", tier=3)


# ---- the contract's own words ---------------------------------------------


def test_open_role_fields_say_when_they_are_null():
    """The generated TS types carry these descriptions, so they must state the rule
    the API applies: null until an enabled board has been listed in full (a found board is
    pending, a partial listing measures nothing), not only "without an enabled board"."""
    schemas = app.openapi()["components"]["schemas"]
    fields = {
        ("CompetitorSummary", "open_roles"),
        ("CompetitorSummary", "delta_30d"),
        ("CompetitorSummary", "board_since"),
        ("CompetitorKpisOut", "open_roles"),
        ("CompetitorKpisOut", "delta_30d"),
        ("HiringOut", "open_now"),
        ("HiringOut", "board_since"),
        ("AreaBoardOut", "board_since"),
    }
    for model, field in sorted(fields):
        text = schemas[model]["properties"][field].get("description", "")
        assert "listed in full" in text, (model, field, text)
        assert "Null without an enabled public job board." not in text, (model, field)
        assert "earliest first-seen day" not in text, (model, field)
    for model in ("ThreatOut", "HiringSeriesOut", "HiringAreaOut"):
        assert "completed fetch" not in json.dumps(schemas[model]), model
    # every board read has been listed in full, so none is "with no posting yet"
    boards = schemas["HiringAreaOut"]["properties"]["boards"]["description"]
    assert "no posting yet" not in boards and "listed in full" in boards, boards


# ---- auth, 404s and the competitor list ----------------------------------------------------


async def test_endpoints_require_auth(client, seeded):
    for path in (
        "/api/v1/competitors/overview",
        "/api/v1/competitors/sonatype/profile",
        "/api/v1/competitors/sonatype/postings",
    ):
        assert (await client.get(path)).status_code == 401, path


async def test_self_and_unknown_slugs_return_404(auth_client, seeded):
    """JFrog itself has no profile and no postings, and is never in the overview."""
    c = await auth_client("analyst")
    for slug in ("jfrog", "no-such-company"):
        for tail in ("profile", "postings"):
            r = await c.get(f"/api/v1/competitors/{slug}/{tail}")
            assert r.status_code == 404, (slug, tail)
    rows = (await c.get("/api/v1/competitors/overview")).json()
    assert len(rows) == 23 and all(r["slug"] != "jfrog" for r in rows)


async def test_competitor_list_serves_description_and_ticker(auth_client, seeded):
    rows = (await (await auth_client("pm")).get("/api/v1/competitors")).json()
    by = {r["slug"]: r for r in rows}
    assert by["gitlab"]["ticker"] == "GTLB"
    assert by["sonatype"]["description"].startswith("Sonatype is a software supply chain")
    assert {"description", "ticker"} <= set(rows[0])


# ---- overview ------------------------------------------------------------------------------


@pytest.fixture
async def ranked(seeded, session, competitor_factory, make_signal, make_source, make_document):
    """hot (high), warm (medium), cool (low) and quiet (insufficient), plus JFrog itself."""
    out = {}
    for slug, threats, others in (("hot", 5, 0), ("warm", 2, 1), ("cool", 0, 3)):
        comp = await new_comp(session, competitor_factory, slug=slug, name=slug.title(), tier=2)
        out[slug] = comp
        for n in range(threats):
            s = await signal(make_signal, comp, ago(2 + n), title=f"{slug} threat {n}")
            await analyse(session, s, hit("Xray"))
        for n in range(others):
            s = await signal(make_signal, comp, ago(8 + n), title=f"{slug} other {n}")
            await analyse(session, s, hit("Xray", effect="opportunity"))
    hot = out["hot"]
    session.add(
        CompetitorProduct(
            competitor_id=hot.id,
            name="Hot Scan",
            category="SCA",
            url=None,
            jfrog_products=["Advanced Security", "Xray"],
        )
    )
    src = await board(make_source, hot, listed=50)
    session.add_all([posting(hot, src, n, seen=seen) for n, seen in enumerate((50, 10, 5))])
    doc = await news(make_document, await make_source(name="Wire"), "Hot raises", date(2026, 5, 1))
    await fact(
        session,
        hot,
        "funding_round",
        "Series A",
        source=doc,
        value=5e6,
        currency="USD",
        as_of=date(2026, 5, 1),
    )
    out["quiet"] = await new_comp(session, competitor_factory, slug="quiet", name="Quiet", tier=2)
    await signal(make_signal, await jfrog(session), ago(1), title="JFrog itself")
    await session.flush()
    return out


async def test_overview_excludes_self_and_orders_by_threat(auth_client, ranked):
    rows = (await (await auth_client("sales")).get("/api/v1/competitors/overview")).json()
    slugs = [r["slug"] for r in rows]
    assert "jfrog" not in slugs
    assert slugs[:3] == ["hot", "warm", "cool"]
    levels = [r["threat"]["level"] for r in rows]
    assert levels[:3] == ["high", "medium", "low"]
    assert set(levels[3:]) == {"insufficient"}
    by = {r["slug"]: r for r in rows}

    hot = by["hot"]
    assert hot["jfrog_products"] == ["Xray", "Advanced Security"]  # JFROG_PRODUCTS order
    assert hot["has_board"] is True and (hot["open_roles"], hot["delta_30d"]) == (3, 2)
    assert hot["overlap"] == 15.0
    assert len(hot["momentum"]) == 13
    assert hot["momentum"][-1] == {"week_start": "2026-10-14", "value": 250.0}
    assert hot["latest_move"]["title"] == "hot threat 0"
    assert hot["latest_move"]["lane"] == "Launches"
    assert hot["funding_total"] == {
        "amount": 5e6,
        "currency": "USD",
        "rounds": 1,
        "undated_rounds": 0,
        "excluded_rounds": 0,
        "as_of": "2026-05-01",  # the newest dated round in the sum
    }
    assert hot["threat"]["score"] == 18.75 and "score 18.8" in hot["threat"]["explanation"]
    assert hot["open_roles_note"] is None  # measured

    quiet = by["quiet"]  # nothing reads as a measured 0
    assert quiet["threat"]["level"] == "insufficient"
    assert quiet["threat"]["explanation"].startswith("Insufficient data")
    for key in ("momentum", "overlap", "open_roles", "delta_30d", "funding_total", "latest_move"):
        assert quiet[key] is None, key
    assert quiet["has_board"] is False and quiet["jfrog_products"] == []
    assert quiet["open_roles_note"] == "No public Greenhouse, Lever or Ashby board found"
    assert quiet["board_checked_at"] is None  # discovery recorded no check for it
    assert quiet["board_state"] == "none"


async def test_overview_is_the_same_for_every_persona(auth_client, ranked, make_signal, session):
    """An analyst's needs_review signals never move an aggregate."""
    await signal(make_signal, ranked["cool"], ago(1), status="needs_review", importance=99)
    await session.flush()
    seen = {}
    for role in ("analyst", "pm"):
        seen[role] = (await (await auth_client(role)).get("/api/v1/competitors/overview")).json()
    assert seen["analyst"] == seen["pm"]


async def test_overview_statements_do_not_grow_with_competitors(auth_client, ranked, sql_counter):
    """Auth, competitors, overview_intel (4, its board activity and, the
    collected-since day included), products, funding facts -- eight, however many
    competitors."""
    c = await auth_client("pm")
    sql_counter.clear()
    assert (await c.get("/api/v1/competitors/overview")).status_code == 200
    assert len(sql_counter) == 8


async def test_overview_serves_each_competitors_product_categories(auth_client, ranked, session):
    """The list's category filter reads each competitor's seeded product
    categories, once each in seed order, from the overview's own products statement."""
    hot = ranked["hot"]
    session.add_all(
        [
            CompetitorProduct(
                competitor_id=hot.id,
                name="Hot Repo",
                category="Artifact repository",
                url=None,
                jfrog_products=["Artifactory"],
            ),
            CompetitorProduct(
                competitor_id=hot.id,
                name="Hot Scan Pro",
                category="SCA",
                url=None,
                jfrog_products=["Xray"],
            ),
        ]
    )
    await session.flush()
    rows = (await (await auth_client("pm")).get("/api/v1/competitors/overview")).json()
    by = {r["slug"]: r["product_categories"] for r in rows}
    assert by["hot"] == ["SCA", "Artifact repository"]
    assert by["quiet"] == []


# ---- a job board with no completed fetch yet ----------------------------------------


@pytest.fixture
async def pending(seeded, session, competitor_factory, make_source):
    """newbie: a board found and enabled, never fetched; steady: a fetched board with no
    open role, a measured zero."""
    newbie = await new_comp(session, competitor_factory, slug="newbie", name="Newbie", tier=2)
    await board(make_source, newbie, fetched=False)
    steady = await new_comp(session, competitor_factory, slug="steady", name="Steady", tier=2)
    src = await board(make_source, steady, listed=50)
    session.add(posting(steady, src, 1, seen=50, closed=10))
    await session.flush()
    return {"newbie": newbie, "steady": steady}


async def test_a_never_fetched_board_shows_no_numbers_only_a_note(auth_client, pending):
    c = await auth_client("pm")
    by = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}
    newbie = by["newbie"]
    assert newbie["has_board"] is True  # a board was found
    assert (newbie["open_roles"], newbie["delta_30d"]) == (None, None)  # never a 0
    assert (newbie["open_roles_note"], newbie["board_state"]) == (PENDING, "pending")
    # an unfetched board is no data, so no signals and that board read "insufficient",
    # never "low 0.0" with thirteen zero momentum weeks
    assert newbie["threat"]["level"] == "insufficient"
    assert (newbie["overlap"], newbie["momentum"], newbie["threat"]["score"]) == (None, None, None)
    steady = by["steady"]
    assert (steady["open_roles"], steady["delta_30d"], steady["open_roles_note"]) == (0, -1, None)

    p = (await c.get("/api/v1/competitors/newbie/profile")).json()
    assert (p["kpis"]["has_board"], p["kpis"]["open_roles"], p["kpis"]["delta_30d"]) == (
        True,
        None,
        None,
    )
    assert p["kpis"]["threat"]["level"] == "insufficient" and p["momentum"] is None
    hiring = p["hiring"]
    assert (hiring["has_board"], hiring["open_now"], hiring["delta_30d"]) == (True, None, None)
    assert hiring["series"] == {k: [] for k in SERIES}  # no 90 zero days
    notes = {n["section"]: n["text"] for n in p["sources_freshness"]["notes"]}
    assert notes["hiring"] == PENDING
    measured = (await c.get("/api/v1/competitors/steady/profile")).json()["hiring"]
    # A measured zero, its series from the board's first snapshot (50 days ago), not 90 days.
    assert measured["open_now"] == 0 and measured["board_since"] == "2026-08-31"
    assert len(measured["series"]["total"]) == 51
    assert measured["series"]["total"][0]["day"] == "2026-08-31"


# ---- a partial first listing and a replaced board ---------


@pytest.fixture
async def unlisted(seeded, session, competitor_factory, make_source, make_signal):
    """halfway: a board fetched without an error but only listed in part (a Greenhouse page
    short of its meta.total), two postings stored, one public signal; moved: a board replaced
    by a new one (the old one disabled, five postings still open in the table), the new one
    listed in full 5 days ago with one posting."""
    halfway = await new_comp(session, competitor_factory, slug="halfway", name="Halfway", tier=2)
    part = await board(make_source, halfway)
    part.first_complete_fetch_at = None  # fetched, but never listed in full
    session.add_all([posting(halfway, part, n, seen=3) for n in (1, 2)])
    await signal(make_signal, halfway, ago(4), title="Halfway ships a scanner")
    moved = await new_comp(session, competitor_factory, slug="moved", name="Moved", tier=2)
    old = await board(make_source, moved, enabled=False, listed=100, name="Moved old jobs")
    session.add_all([posting(moved, old, n, seen=100) for n in range(1, 6)])
    new = await board(make_source, moved, listed=5, name="Moved new jobs")
    session.add(posting(moved, new, 9, seen=5, title="Current role"))
    await session.flush()
    return {"halfway": halfway, "moved": moved}


async def test_a_partial_listing_measures_nothing_and_its_notes_say_pending(auth_client, unlisted):
    """Only a complete listing measures a board, so a partial one serves no numbers,
    no series and no postings, and the threat cannot count it; the momentum note of
    such a competitor names the pending board, as the hiring note and the threat do, never
    "no job board"."""
    c = await auth_client("pm")
    row = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}["halfway"]
    assert (row["has_board"], row["open_roles"], row["board_since"]) == (True, None, None)
    assert row["open_roles_note"] == PENDING
    assert row["threat"]["level"] == "insufficient"
    assert "has not been listed in full yet" in row["threat"]["explanation"]

    p = (await c.get("/api/v1/competitors/halfway/profile")).json()
    assert (p["hiring"]["open_now"], p["hiring"]["recent"]) == (None, [])
    assert p["hiring"]["series"] == {k: [] for k in SERIES}
    notes = {n["section"]: n["text"] for n in p["sources_freshness"]["notes"]}
    assert notes["hiring"] == PENDING
    assert notes["momentum"] == (
        "Too little evidence for a momentum series: 1 public signal in the last 180 days "
        "(3 needed) and its job board has not been listed in full yet"
    )
    page = (await c.get("/api/v1/competitors/halfway/postings")).json()
    assert page == {"items": [], "next_cursor": None}


async def test_a_partly_listed_second_board_adds_nothing(
    auth_client, seeded, session, competitor_factory, make_source
):
    """With one board listed in full and a second only ever listed in part, only the
    first board's postings are measured: the overview, the deep dive and /postings agree."""
    split = await new_comp(session, competitor_factory, slug="split", name="Split", tier=2)
    whole = await board(make_source, split, listed=40, name="Split jobs")
    part = await board(make_source, split, name="Split jobs (partial)")
    part.first_complete_fetch_at = None
    session.add_all(
        [
            posting(split, whole, 1, seen=40, title="Listed role"),
            *(posting(split, part, n, seen=3, title=f"Partial role {n}") for n in (2, 3, 4)),
        ]
    )
    await session.flush()
    c = await auth_client("pm")
    row = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}["split"]
    assert (row["open_roles"], row["board_since"]) == (1, "2026-09-10")
    hiring = (await c.get("/api/v1/competitors/split/profile")).json()["hiring"]
    assert hiring["open_now"] == 1 and [r["title"] for r in hiring["recent"]] == ["Listed role"]
    page = (await c.get("/api/v1/competitors/split/postings")).json()
    assert [r["title"] for r in page["items"]] == ["Listed role"]


async def test_a_replaced_board_is_never_counted(auth_client, unlisted):
    """A board replaced by a new one is no longer read, so its postings never close;
    only the current board counts, and the history starts at ITS first listing."""
    c = await auth_client("pm")
    row = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}["moved"]
    assert (row["open_roles"], row["delta_30d"], row["board_since"]) == (1, None, "2026-10-15")
    hiring = (await c.get("/api/v1/competitors/moved/profile")).json()["hiring"]
    assert (hiring["open_now"], hiring["board_since"]) == (1, "2026-10-15")
    assert [r["title"] for r in hiring["recent"]] == ["Current role"]
    assert hiring["series"]["total"][0] == {"day": "2026-10-15", "count": 1}
    page = (await c.get("/api/v1/competitors/moved/postings")).json()
    assert [r["title"] for r in page["items"]] == ["Current role"]


# ---- a board under 30 days old --------------------------------------


@pytest.fixture
async def young(seeded, session, competitor_factory, make_source):
    """fresh: a board first read 10 days ago, three open roles (none closed), tagged."""
    fresh = await new_comp(session, competitor_factory, slug="fresh", name="Fresh", tier=2)
    src = await board(make_source, fresh, listed=10)
    session.add_all(
        [
            posting(fresh, src, 1, seen=10, tags=["sca"]),
            posting(fresh, src, 2, seen=10, department="Sales", tags=["sca"]),
            posting(fresh, src, 3, seen=4, department="Security"),
        ]
    )
    await session.flush()
    return fresh


async def test_a_young_board_has_no_30_day_change_and_its_series_start_at_its_first_snapshot(
    auth_client, young
):
    c = await auth_client("pm")
    row = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}["fresh"]
    assert (row["open_roles"], row["delta_30d"], row["board_since"]) == (3, None, "2026-10-10")

    p = (await c.get("/api/v1/competitors/fresh/profile")).json()
    assert (p["kpis"]["open_roles"], p["kpis"]["delta_30d"]) == (3, None)  # never "+3 in 30 days"
    assert p["kpis"]["threat"]["hiring_trend"] is None
    hiring = p["hiring"]
    assert (hiring["open_now"], hiring["delta_30d"], hiring["board_since"]) == (
        3,
        None,
        "2026-10-10",
    )
    assert {t["slug"]: (t["open"], t["delta_30d"]) for t in hiring["tags"]} == {"sca": (2, None)}
    total = hiring["series"]["total"]
    assert [pt["day"] for pt in total][0] == "2026-10-10" and len(total) == 11
    assert all(pt["count"] > 0 for pt in total)  # no day before the board, never zero-filled
    for by in ("department", "seniority", "location", "tag"):
        assert min(pt["day"] for pt in hiring["series"][by]) == "2026-10-10", by


# ---- a board that stops being listed, the untagged state, discovery's check --

STALE = "Job board not readable since 15 Oct 2026"


@pytest.fixture
async def lapsed(seeded, session, competitor_factory, make_source, make_signal):
    """lapsed: a board listed in full 60 days ago and last on 15 Oct, failing every day since
    (an ATS switch: a 404 each run, so nothing ever closes), three postings open at its last
    listing, three public signals (scored without the board); lagging: a board last listed in
    full two days ago (still fresh), one posting first seen since by a partial listing."""
    lapsed = await new_comp(session, competitor_factory, slug="lapsed", name="Lapsed", tier=2)
    src = await board(make_source, lapsed, listed=60)
    src.last_complete_fetch_at = at(ago(5), 3)  # 06:00 on 15 Oct in Jerusalem
    src.last_fetched_at, src.consecutive_failures, src.health = at(ago(5), 3), 5, "degraded"
    session.add_all([posting(lapsed, src, n, seen=40) for n in (1, 2, 3)])
    for n in range(3):
        await signal(make_signal, lapsed, ago(10 + n), title=f"Lapsed news {n}")
    lagging = await new_comp(session, competitor_factory, slug="lagging", name="Lagging", tier=2)
    lag = await board(make_source, lagging, listed=60)
    lag.last_complete_fetch_at = at(ago(2), 3)
    session.add_all(
        [
            posting(lagging, lag, 1, seen=10, title="Listed role"),
            posting(lagging, lag, 2, seen=1, title="Seen by a partial listing"),
        ]
    )
    await session.flush()
    return {"lapsed": lapsed, "lagging": lagging}


async def test_a_board_failing_for_days_serves_no_current_numbers(auth_client, lapsed):
    """Five days after its last complete listing a board is stale: the overview and
    the deep dive serve no current open roles, only the note; the threat reads it as no
    board; its series end at its last complete listing; /postings serves none."""
    c = await auth_client("pm")
    row = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}["lapsed"]
    assert (row["has_board"], row["open_roles"], row["delta_30d"]) == (True, None, None)
    assert (row["open_roles_note"], row["open_roles_as_of"]) == (STALE, None)
    assert row["board_state"] == "stale"
    assert row["threat"]["hiring_trend"] is None
    assert "the job board has not been readable since 15 Oct 2026" in row["threat"]["explanation"]

    p = (await c.get("/api/v1/competitors/lapsed/profile")).json()
    assert (p["kpis"]["open_roles"], p["kpis"]["delta_30d"]) == (None, None)
    hiring = p["hiring"]
    assert (hiring["open_now"], hiring["delta_30d"], hiring["as_of"]) == (None, None, None)
    assert hiring["not_readable_since"] == "2026-10-15"
    assert (hiring["recent"], hiring["tags"], hiring["tag_coverage"]) == ([], [], None)
    total = hiring["series"]["total"]
    assert (total[0]["day"], total[-1]) == ("2026-08-21", {"day": "2026-10-15", "count": 3})
    assert all(pt["day"] <= "2026-10-15" for by in SERIES for pt in hiring["series"][by])
    notes = {n["section"]: n["text"] for n in p["sources_freshness"]["notes"]}
    assert notes["hiring"] == STALE
    page = (await c.get("/api/v1/competitors/lapsed/postings")).json()
    assert page == {"items": [], "next_cursor": None}


async def test_open_roles_are_served_as_of_the_last_complete_listing(auth_client, lapsed):
    """A fresh board's counts are its last complete listing's, with that time
    served beside them; a posting seen since by a partial listing is not in them."""
    c = await auth_client("pm")
    row = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}["lagging"]
    assert (row["open_roles"], row["open_roles_note"], row["board_state"]) == (1, None, "measured")
    assert row["open_roles_as_of"] == "2026-10-18T03:00:00Z"
    hiring = (await c.get("/api/v1/competitors/lagging/profile")).json()["hiring"]
    assert (hiring["open_now"], hiring["as_of"]) == (1, "2026-10-18T03:00:00Z")
    assert [r["title"] for r in hiring["recent"]] == ["Listed role"]
    assert hiring["series"]["total"][-1] == {"day": "2026-10-18", "count": 1}


async def test_open_postings_say_their_tagging_state_and_the_profile_counts_them(
    auth_client, seeded, session, competitor_factory, make_source
):
    """Every open posting is tagged, untagged (tagged with none),
    pending or given up, on /postings and in the profile, and the profile counts them."""
    comp = await new_comp(session, competitor_factory, slug="tagco", name="Tagco", tier=2)
    src = await board(make_source, comp, listed=40)
    session.add_all(
        [
            posting(comp, src, 1, title="Tagged", tags=["sca"], tagged_at=at(ago(3))),
            posting(comp, src, 2, title="Untagged", tagged_at=at(ago(3)), tag_version="1"),
            posting(comp, src, 3, title="Pending"),
            posting(comp, src, 4, title="Given up", tag_failures=3, tag_error="LLMError: x"),
        ]
    )
    await session.flush()
    c = await auth_client("pm")
    hiring = (await c.get("/api/v1/competitors/tagco/profile")).json()["hiring"]
    assert hiring["tag_coverage"] == {"tagged": 1, "pending": 1, "untagged": 1, "gave_up": 1}
    states = {"Tagged": "tagged", "Untagged": "untagged", "Pending": "pending"}
    states["Given up"] = "gave_up"
    assert {r["title"]: r["tag_state"] for r in hiring["recent"]} == states
    page = (await c.get("/api/v1/competitors/tagco/postings", params={"status": "open"})).json()
    assert {r["title"]: r["tag_state"] for r in page["items"]} == states


async def test_a_discovery_miss_says_when_it_was_checked(
    auth_client, seeded, session, competitor_factory, make_source
):
    """Discovery is an operator command and a miss is cached for 30 days, so the note
    says no board was found and, when discovery recorded one, the day it last checked."""
    seen = await new_comp(session, competitor_factory, slug="nojobs", name="Nojobs", tier=3)
    await make_source(
        kind="job_board",
        name="Nojobs · job board (none found)",
        url="discovery://job_board/nojobs",
        competitor_id=seen.id,
        enabled=False,
        config={
            "missing": True,
            "checked": {
                "greenhouse:nojobs": "2026-10-02T04:00:00+00:00",
                "lever:nojobs": "2026-10-12T04:00:00+00:00",
            },
        },
    )
    await new_comp(session, competitor_factory, slug="unseen", name="Unseen", tier=3)
    await session.flush()
    c = await auth_client("pm")
    by = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}
    checked = "No public Greenhouse, Lever or Ashby board found (checked 12 Oct 2026)"
    assert (by["nojobs"]["open_roles_note"], by["nojobs"]["board_checked_at"]) == (
        checked,
        "2026-10-12T04:00:00Z",
    )
    assert (by["unseen"]["open_roles_note"], by["unseen"]["board_checked_at"]) == (
        "No public Greenhouse, Lever or Ashby board found",
        None,
    )
    p = (await c.get("/api/v1/competitors/nojobs/profile")).json()
    assert p["hiring"]["board_checked_at"] == "2026-10-12T04:00:00Z"
    notes = {n["section"]: n["text"] for n in p["sources_freshness"]["notes"]}
    assert notes["hiring"] == checked


async def test_a_long_diff_is_served_capped_and_whole_on_demand(
    auth_client, seeded, session, competitor_factory, make_source
):
    """The profile serves each page change's diff capped at 4,000 characters of whole
    lines, saying so; GET .../page-changes/{id}/diff serves the whole stored diff, for the
    changes the profile shows only (JFrog itself, another competitor's, a 404)."""
    comp = await new_comp(session, competitor_factory, slug="longdiff", name="Longdiff", tier=2)
    await new_comp(session, competitor_factory, slug="otherco", name="Otherco", tier=2)
    page = await make_source(
        kind="page_watch",
        name="Longdiff · pricing page",
        url="https://longdiff.example/pricing",
        competitor_id=comp.id,
        config={"page": "pricing", "url": "https://longdiff.example/pricing"},
    )
    long_diff = "\n".join(
        f"{'+' if n % 2 else '-'} Plan {n}: {n * 7} dollars per seat per month, billed yearly"
        for n in range(200)
    )

    def snap(n: int, diff: str, **kw) -> PageSnapshot:
        return PageSnapshot(
            source_id=page.id,
            fetched_at=at(ago(n)),
            text_hash=f"h{n}",
            text=f"text {n}",
            diff_from_prev=diff,
            page_kind="pricing",
            meaningful=kw.pop("meaningful", True),
            change_kind="pricing",
            change_summary=f"Change {n}",
            judged_at=at(ago(n)),
        )

    big, small, trivial = snap(3, long_diff), snap(2, "+ Team 59\n- Team 49"), snap(1, "+ x")
    trivial.meaningful = False
    session.add_all([big, small, trivial])
    await session.flush()
    c = await auth_client("pm")
    pricing = (await c.get("/api/v1/competitors/longdiff/profile")).json()["pricing"]
    by = {p["snapshot_id"]: p for p in pricing}
    assert set(by) == {big.id, small.id}
    capped = by[big.id]
    assert capped["truncated"] is True and 3900 < len(capped["diff"]) <= 4000
    assert long_diff.startswith(capped["diff"] + "\n")  # whole lines only
    assert (by[small.id]["truncated"], by[small.id]["diff"]) == (False, "+ Team 59\n- Team 49")

    url = f"/api/v1/competitors/longdiff/page-changes/{big.id}/diff"
    whole = await c.get(url)
    assert whole.status_code == 200 and whole.json() == {"snapshot_id": big.id, "diff": long_diff}
    assert (await c.get(url.replace("longdiff", "otherco"))).status_code == 404
    me = await jfrog(session)
    assert (await c.get(url.replace("longdiff", me.slug))).status_code == 404
    hidden = f"/api/v1/competitors/longdiff/page-changes/{trivial.id}/diff"
    assert (await c.get(hidden)).status_code == 404


async def test_the_profile_serves_no_logo(auth_client, acme):
    """A third-party logo would make every reader's browser request another host."""
    p = (await (await auth_client("pm")).get("/api/v1/competitors/acme/profile")).json()
    assert "logo_url" not in p["competitor"]


# ---- profile -------------------------------------------------------------------------------


async def test_profile_has_every_section(auth_client, acme):
    w = acme
    p = (await (await auth_client("pm")).get("/api/v1/competitors/acme/profile")).json()
    assert set(p) == PROFILE_SECTIONS

    assert p["competitor"] == {
        "slug": "acme",
        "name": "Acme",
        "tier": 1,
        "relationship": "competitor",
        "description": "Acme makes artifact registries.",
        "website": "https://acme.example",
        "ticker": "ACME",
    }  # no logo is served
    kpis = p["kpis"]
    assert kpis["threat"]["level"] == "high"
    assert (kpis["has_board"], kpis["open_roles"], kpis["delta_30d"]) == (True, 3, 1)
    assert kpis["funding_total"] == {
        # A 10M + B 40M (one round over its three sources; the newest states 40M) + Seed 2M
        "amount": 52e6,
        "currency": "USD",
        "rounds": 3,
        "undated_rounds": 1,  # the seed round: "incl. 1 undated"
        "excluded_rounds": 1,  # the debt financing states no amount
        "as_of": "2026-06-10",  # Series B, the newest dated round in the sum
    }
    assert kpis["last_round"]["label"] == "Debt financing"
    assert kpis["last_round"]["amount"] is None
    assert kpis["latest_headcount"]["value"] == 1250  # September's newest source
    assert kpis["latest_headcount"]["sources_disagree"] is True

    # products: the overlap rows, with the brief's edges numbered into its sources
    repo, scan = p["products"]
    assert (repo["product"], repo["url"], repo["level"], repo["evidence"]) == (
        "Acme Repo",
        "https://acme.example/repo",
        "high",
        8,
    )
    assert repo["signal_ids"] == [w["launch"].id, w["win"].id, w["price"].id]
    assert repo["edges"] == [
        {
            "product": "Acme Repo",
            "jfrog_product": "Artifactory",
            "edge": {"text": "Cheaper per seat.", "cites": [4]},
        }
    ]
    assert (scan["level"], scan["jfrog_products"], scan["edges"]) == ("low", ["Xray"], [])

    # momentum and the swimlane: public signals only
    assert len(p["momentum"]) == 13
    lanes = {e["signal_id"]: e["lane"] for e in p["timeline"]}
    assert lanes == {
        w["launch"].id: "Launches",
        w["win"].id: "Partners",
        w["partner"].id: "Partners",
        w["cto"].id: "People",
        w["price"].id: "Pricing",
    }

    hiring = p["hiring"]
    assert hiring["has_board"] is True and hiring["open_now"] == 3
    assert hiring["by_department"] == [
        {"key": "Engineering", "count": 1},
        {"key": "Sales", "count": 1},
        {"key": "Security", "count": 1},
    ]
    assert {t["slug"]: (t["open"], t["delta_30d"]) for t in hiring["tags"]} == {
        "sca": (2, 0),
        "sast": (1, 1),
        "sbom": (1, 1),
    }
    assert hiring["recent"][0]["title"] == "Role 4"
    series = hiring["series"]
    assert set(series) == set(SERIES)
    # from the board's first snapshot (its earliest posting, 60 days ago), not 90 days
    assert hiring["board_since"] == "2026-08-21"
    assert len(series["total"]) == 61 and series["total"][0]["day"] == "2026-08-21"
    assert series["total"][-1] == {"day": "2026-10-20", "count": 3}
    assert {pt["key"] for pt in series["tag"]} == {"sca", "sast", "sbom"}

    # headcount: reported points only; the same month's two sources are two points
    points = [(pt["as_of"], pt["value"], pt["layoff"]) for pt in p["headcount"]["points"]]
    assert points == [
        ("2026-03-01", 900, False),
        ("2026-05-04", 10, True),
        ("2026-09-01", 1200, False),
        ("2026-09-15", 1250, False),
    ]
    sept = p["headcount"]["points"][2]
    assert sept["sources_disagree"] is True and len(sept["sources"]) == 1
    assert sept["sources"][0]["title"] == "Acme hires 1,200"
    assert [f["value"] for f in p["headcount"]["undated"]] == [1100]
    assert p["headcount"]["undated"][0]["as_of"] is None

    money = p["money"]
    assert [(r["label"], r["amount"]) for r in money["funding_rounds"]] == [
        ("Series A", 10e6),
        ("Series B", 40e6),
        ("Debt financing", None),
    ]
    series_b = money["funding_rounds"][1]  # one round: its two dated and one undated source
    assert series_b["as_of"] == "2026-06-10"  # its earliest dated source
    assert series_b["sources_disagree"] is True
    assert [s["value"] for s in series_b["sources"]] == [40e6, 45e6, 40e6]  # newest first
    assert [v["value"] for v in money["valuations"]] == [500e6]
    assert [(r["kind"], r["value"]) for r in money["revenue"]] == [
        ("revenue", 80e6),
        ("arr", 100e6),
    ]
    assert [(f["label"], f["as_of"]) for f in money["undated"]] == [("Seed", None)]
    dated_total = sum(r["amount"] or 0 for r in money["funding_rounds"])
    undated_total = sum(f["value"] for f in money["undated"] if f["kind"] == "funding_round")
    assert dated_total + undated_total == kpis["funding_total"]["amount"]  # one scope

    market = p["market"]
    assert [f["label"] for f in market["market_share"]] == ["container security, IDC"]
    assert [f["label"] for f in market["undated"]] == ["artifact management, Gartner"]
    assert len(market["peers"]) == 4 and not {"acme", "jfrog"} & set(market["peers"])
    voice = market["share_of_voice"]
    assert {v["competitor_slug"] for v in voice} == {"acme", *market["peers"]}
    assert len(voice) == 13 * 5

    customers = p["customers"]
    assert [f["label"] for f in customers["facts"]] == ["BigBank", "customers"]
    assert [f["label"] for f in customers["undated"]] == ["SmallCo"]
    assert [s["id"] for s in customers["signals"]] == [w["win"].id]

    assert p["pricing"] == [
        {
            "snapshot_id": w["price_change"].id,
            "day": ago(12).isoformat(),
            "page_kind": "pricing",
            "change_kind": "pricing",
            "summary": "Raised the Team tier from $10 to $12.",
            "diff": "@@ -1 +1 @@\n-Team $10\n+Team $12",
            "truncated": False,  # a short diff is served whole
            "url": "https://acme.example/pricing",
        }
    ]

    community = p["community"]
    [stats] = community["repos"]
    assert stats["repo"] == "acme/acme"
    assert [pt["value"] for pt in stats["stars"]] == [100.0, 120.0]
    assert [pt["value"] for pt in stats["forks"]] == [7.0]
    assert len(community["mentions"]) == 13
    assert sum(m["count"] for m in community["mentions"]) == 2  # relevant HN items only

    people = p["people_partners"]
    assert [f["label"] for f in people["facts"]] == ["CloudCo", "CTO: Jane Roe"]
    assert {f["label"] for f in people["undated"]} == {"Tiny Inc", "Austin, United States"}
    office = next(f for f in people["undated"] if f["kind"] == "office")
    assert office["sources"][0]["source_kind"] == "page_snapshot"
    assert office["sources"][0]["url"] == "https://acme.example/about"
    assert {s["id"] for s in people["signals"]} == {w["partner"].id, w["cto"].id}

    assert [s["id"] for s in p["latest_signals"]] == [
        w["launch"].id,
        w["win"].id,
        w["partner"].id,
        w["cto"].id,
        w["price"].id,
    ]

    fresh = p["sources_freshness"]
    kinds = sorted(s["kind"] for s in fresh["sources"])
    assert kinds == ["github_repo_stats", "hn", "job_board", "page_watch", "page_watch", "rss"]
    assert all(set(s) == {"kind", "name", "health", "last_fetched_at"} for s in fresh["sources"])
    assert fresh["notes"] == []


async def test_young_collection_serves_only_the_weeks_it_collected(
    auth_client, ranked, make_document
):
    """FrogEye began collecting 20 days ago. The overview sparkline, the deep
    dive's momentum and its share of voice start at the week holding that day, never quiet
    weeks before it; the momentum trend has no baseline yet, so it is null and the threat's
    momentum factor 1.00; the profile serves the day itself."""
    await make_document(created_at=at(ago(20)))
    weeks = ["2026-09-30", "2026-10-07", "2026-10-14"]
    c = await auth_client("pm")
    hot = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}["hot"]
    assert [w["week_start"] for w in hot["momentum"]] == weeks
    assert hot["threat"]["momentum_trend"] is None
    assert "no baseline yet" in hot["threat"]["explanation"]
    p = (await c.get("/api/v1/competitors/hot/profile")).json()
    assert p["collected_since"] == "2026-09-30"
    assert [w["week_start"] for w in p["momentum"]] == weeks
    assert sorted({v["week_start"] for v in p["market"]["share_of_voice"]}) == weeks


async def test_young_collection_serves_only_the_hn_weeks_it_collected(
    auth_client, acme, make_document
):
    """The Community section's Hacker News mentions are a weekly series too, so they
    start at the week holding collected_since, never 0-mention weeks before FrogEye
    collected anything; the mention 20 days back (30 Sep) is in the first served week."""
    await make_document(created_at=at(ago(20)))
    p = (await (await auth_client("pm")).get("/api/v1/competitors/acme/profile")).json()
    mentions = p["community"]["mentions"]
    assert [m["week_start"] for m in mentions] == ["2026-09-30", "2026-10-07", "2026-10-14"]
    assert sum(m["count"] for m in mentions) == 2
    described = app.openapi()["components"]["schemas"]["CommunityOut"]["properties"]["mentions"]
    assert "collected_since" in described["description"]


async def test_sparse_competitor_renders_every_section_honestly(auth_client, quiet):
    """No board, no facts, no signals -- every section is present and empty,
    the threat reads "Insufficient data", nothing shows 0 as if measured, and the coverage
    gaps are said plainly."""
    p = (await (await auth_client("exec")).get("/api/v1/competitors/quiet/profile")).json()
    assert set(p) == PROFILE_SECTIONS
    kpis = p["kpis"]
    assert kpis["threat"]["level"] == "insufficient"
    assert kpis["threat"]["explanation"].startswith("Insufficient data")
    assert kpis["threat"]["overlap"] is None and kpis["threat"]["score"] is None
    for key in ("open_roles", "delta_30d", "latest_headcount", "funding_total", "last_round"):
        assert kpis[key] is None, key
    assert p["brief"] is None and p["products"] == [] and p["timeline"] == []
    assert p["momentum"] is None  # never thirteen zeros
    hiring = p["hiring"]
    assert hiring["has_board"] is False
    assert (hiring["open_now"], hiring["delta_30d"]) == (None, None)
    assert hiring["series"] == {k: [] for k in SERIES}
    for key in ("by_department", "by_seniority", "by_location", "tags", "recent"):
        assert hiring[key] == [], key
    assert p["headcount"] == {"points": [], "undated": []}
    assert p["money"] == {"funding_rounds": [], "valuations": [], "revenue": [], "undated": []}
    assert p["market"]["market_share"] == [] and p["market"]["undated"] == []
    assert p["customers"] == {"facts": [], "undated": [], "signals": []}
    assert p["people_partners"] == {"facts": [], "undated": [], "signals": []}
    assert p["pricing"] == [] and p["latest_signals"] == []
    assert p["community"] == {"repos": [], "mentions": []}
    assert p["sources_freshness"]["sources"] == []
    notes: dict[str, list[str]] = {}
    for n in p["sources_freshness"]["notes"]:
        notes.setdefault(n["section"], []).append(n["text"])
    assert set(notes) == SECTIONS  # every empty section says why
    assert notes["hiring"] == ["No public Greenhouse, Lever or Ashby board found"]
    assert notes["overview"] == [
        "Not enough evidence for a brief yet: 0 public signals in the last 180 days (3 needed)"
    ]
    assert notes["headcount"] == [
        "No headcount or layoff figures have been reported in its sources yet"
    ]
    assert notes["money"] == [
        "No funding, valuation or revenue figures have been reported in its sources yet"
    ]
    assert notes["community"] == [
        "No GitHub repositories are tracked for this competitor",
        "No Hacker News search runs for this competitor",
    ]
    assert all(t and t[0].isupper() and not t.endswith(".") for ts in notes.values() for t in ts)


async def test_every_coverage_note_names_a_section_that_is_empty(auth_client, acme, session):
    """A section with data carries no note; emptying the pricing log adds just its note."""
    c = await auth_client("pm")
    assert (await c.get("/api/v1/competitors/acme/profile")).json()["sources_freshness"][
        "notes"
    ] == []
    await session.execute(delete(PageSnapshot).where(PageSnapshot.id == acme["price_change"].id))
    await session.flush()
    p = (await c.get("/api/v1/competitors/acme/profile")).json()
    assert p["sources_freshness"]["notes"] == [
        {"section": "pricing", "text": "No meaningful pricing change seen on its pages yet"}
    ]


# ---- the brief's numbered sources ----------------------------------------------------------


async def test_brief_sources_are_numbered_and_every_cite_resolves(auth_client, acme):
    w = acme
    profile = (await (await auth_client("sales")).get("/api/v1/competitors/acme/profile")).json()
    brief = profile["brief"]
    assert brief["version"] == 2
    assert brief["created_at"].startswith(ago(2).isoformat())
    assert brief["evidence"] == {"signals": 3, "facts": 1, "postings": 1, "pages": 1}
    sources = brief["sources"]
    r = w["brief_refs"]
    assert [(s["n"], s["ref"]) for s in sources] == [  # first-cited order; fact:999999 is gone
        (1, r["launch"]),
        (2, r["series_b"]),
        (3, r["posting"]),
        (4, r["page"]),
        (5, r["rejected"]),
    ]
    assert [i["cites"] for i in brief["bottom_line"]] == [[1, 2], [3, 1], [4]]
    assert brief["product_edges"][0]["edge"]["cites"] == [4]
    assert brief["watch"][0]["cites"] == [5]
    cited = [n for item in brief["bottom_line"] + brief["watch"] for n in item["cites"]]
    cited += [n for e in brief["product_edges"] for n in e["edge"]["cites"]]
    assert set(cited) <= {s["n"] for s in sources}  # every cite resolves

    launch, series_b, job, page, hidden = sources
    assert (launch["kind"], launch["title"], launch["url"]) == (
        "signal",
        "Acme Repo 2",
        f"/signals/{w['launch'].id}",
    )
    assert launch["day"] == ago(3).isoformat()
    assert series_b["kind"] == "fact" and series_b["title"] == "Funding round: Series B"
    assert series_b["excerpt"] == "The source states the Series B in a full sentence."
    assert series_b["day"] == "2026-06-10" and series_b["url"].startswith("https://example.com/")
    assert (job["kind"], job["title"], job["url"]) == (
        "posting",
        "Closed SCA role",
        "https://jobs.example.com/acme/3",
    )  # a closed posting still resolves
    assert (page["kind"], page["title"], page["url"], page["excerpt"]) == (
        "page",
        "Pricing page change",
        "https://acme.example/pricing",
        "Raised the Team tier from $10 to $12.",
    )
    # a cited signal the reader may not see keeps its number, never its title
    assert (hidden["kind"], hidden["title"], hidden["url"]) == (
        "signal",
        "Signal not available",
        None,
    )


# ---- visibility ----------------------------------------------------------------------------


async def test_hidden_signals_never_appear(auth_client, acme):
    """The cards are the public set within the reader's visibility, as on the
    product page, so the whole deep dive is identical for every persona."""
    w = acme
    seen = {
        role: (await (await auth_client(role)).get("/api/v1/competitors/acme/profile")).text
        for role in ("pm", "analyst")
    }
    for text in seen.values():
        for title in (
            "Acme story the desk rejected",
            "JFrog-scope story naming Acme",
            "Acme rumour under review",  # needs_review: not public, so not on the deep dive
        ):
            assert title not in text
    analyst = json.loads(seen["analyst"])
    assert w["review"].id not in [s["id"] for s in analyst["latest_signals"]]
    assert analyst == json.loads(seen["pm"])


# ---- facts ----------------------------------------------------------------


async def test_two_headcount_figures_for_one_month_are_two_points_never_an_average(
    auth_client, quiet, session, make_source, make_document
):
    a = await news(make_document, await make_source(name="Paper one"), "Has 300", date(2026, 7, 5))
    b = await news(make_document, await make_source(name="Paper two"), "Has 340", date(2026, 7, 9))
    for src, label, value, as_of in ((a, "employees", 300, 5), (b, "Employees", 340, 9)):
        await fact(
            session,
            quiet,
            "headcount",
            label,
            source=src,
            value=value,
            unit="employees",
            as_of=date(2026, 7, as_of),
        )
    p = (await (await auth_client("pm")).get("/api/v1/competitors/quiet/profile")).json()
    points = p["headcount"]["points"]
    assert [(pt["value"], pt["sources"][0]["outlet"]) for pt in points] == [
        (300, "Paper one"),
        (340, "Paper two"),
    ]
    assert all(pt["sources_disagree"] for pt in points)
    latest = p["kpis"]["latest_headcount"]
    assert latest["value"] == 340 and latest["as_of"] == "2026-07-09"  # the newest source's
    assert [s["value"] for s in latest["sources"]] == [340, 300]  # every source, newest first


async def test_a_named_round_dated_in_two_months_is_one_round(
    auth_client, quiet, session, make_source, make_document
):
    """A $40M Series B announced on 30 May and syndicated on 2 June is one round,
    dated by its earliest source, counted once."""
    wire, blog = await make_source(name="Wire"), await make_source(name="Blog")
    a = await news(make_document, wire, "Quiet raises a $40M Series B", date(2026, 5, 30))
    b = await news(make_document, blog, "Quiet's $40M Series B", date(2026, 6, 2))
    for src, day in ((a, date(2026, 5, 30)), (b, date(2026, 6, 2))):
        await fact(
            session, quiet, "funding_round", "Series B", source=src, value=40e6,
            currency="USD", as_of=day,
        )  # fmt: skip
    p = (await (await auth_client("pm")).get("/api/v1/competitors/quiet/profile")).json()
    [round_b] = p["money"]["funding_rounds"]
    assert (round_b["as_of"], round_b["amount"], round_b["sources_disagree"]) == (
        "2026-05-30",
        40e6,
        False,
    )
    assert [s["outlet"] for s in round_b["sources"]] == ["Blog", "Wire"]  # newest first
    assert p["kpis"]["funding_total"] == {
        "amount": 40e6,
        "currency": "USD",
        "rounds": 1,
        "undated_rounds": 0,
        "excluded_rounds": 0,
        "as_of": "2026-05-30",
    }
    assert p["kpis"]["last_round"]["as_of"] == "2026-05-30"


async def test_same_label_rounds_far_apart_are_two_rounds(
    auth_client, quiet, session, make_source, make_document
):
    """A named round merges its sources only within 120 days of its earliest one, so
    two "Seed" rounds two years apart are two rounds, both summed and both drawn. 120 days
    still joins the syndication of one round; an undated source of the label cannot be
    placed between two rounds and stays undated (never a third round in the total)."""
    wire = await make_source(name="Wire")
    rounds = (
        (date(2024, 3, 1), 2e6),
        (date(2024, 6, 29), 2e6),  # day 120: the same seed round
        (date(2026, 4, 1), 3e6),  # a second seed round, two years later
        (None, 3e6),
    )
    for n, (day, value) in enumerate(rounds):
        doc = await news(make_document, wire, f"Quiet seed {n}", day or date(2026, 5, 1))
        await fact(
            session, quiet, "funding_round", "Seed", source=doc, value=value,
            currency="USD", as_of=day,
        )  # fmt: skip
    p = (await (await auth_client("pm")).get("/api/v1/competitors/quiet/profile")).json()
    drawn = [(r["as_of"], r["amount"], len(r["sources"])) for r in p["money"]["funding_rounds"]]
    assert drawn == [("2024-03-01", 2e6, 2), ("2026-04-01", 3e6, 1)]
    assert [f["label"] for f in p["money"]["undated"]] == ["Seed"]
    assert p["kpis"]["funding_total"] == {
        "amount": 5e6,
        "currency": "USD",
        "rounds": 2,
        "undated_rounds": 0,
        "excluded_rounds": 0,
        "as_of": "2026-04-01",  # the newer of the two
    }


async def test_unnamed_rounds_in_different_months_stay_apart(
    auth_client, quiet, session, make_source, make_document
):
    """Only a named round (seed, Series X) is one event whatever its dates: two debt
    financings months apart are two rounds."""
    wire = await make_source(name="Wire")
    for day, value in ((date(2026, 2, 3), 5e6), (date(2026, 8, 3), 7e6)):
        doc = await news(make_document, wire, f"Debt {day}", day)
        await fact(
            session, quiet, "funding_round", "Debt financing", source=doc, value=value,
            currency="USD", as_of=day,
        )  # fmt: skip
    p = (await (await auth_client("pm")).get("/api/v1/competitors/quiet/profile")).json()
    assert [r["amount"] for r in p["money"]["funding_rounds"]] == [5e6, 7e6]
    assert p["kpis"]["funding_total"]["amount"] == 12e6


async def test_funding_total_with_mixed_currencies(
    auth_client, quiet, session, make_source, make_document
):
    """The total is in the currency most rounds state, never a sum across currencies;
    the rest are excluded and counted."""
    wire = await make_source(name="Wire")
    rounds = (
        ("Seed", 2e6, "EUR", date(2025, 3, 1)),
        ("Series A", 10e6, "USD", date(2025, 9, 1)),
        ("Series B", 30e6, "USD", date(2026, 4, 1)),
        ("Series C", None, None, date(2026, 9, 1)),  # undisclosed
        ("Series D", 50e6, "USD", None),  # undated, still raised
    )
    for label, value, currency, as_of in rounds:
        doc = await news(make_document, wire, label, as_of or date(2026, 10, 1))
        await fact(
            session, quiet, "funding_round", label, source=doc, value=value, currency=currency,
            as_of=as_of,
        )  # fmt: skip
    p = (await (await auth_client("pm")).get("/api/v1/competitors/quiet/profile")).json()
    assert p["kpis"]["funding_total"] == {
        "amount": 90e6,
        "currency": "USD",
        "rounds": 3,
        "undated_rounds": 1,
        "excluded_rounds": 2,  # the EUR seed and the undisclosed Series C
        "as_of": "2026-04-01",  # Series B; the undated Series D has no date to give
    }
    by_label = {r["label"]: r["currency"] for r in p["money"]["funding_rounds"]}
    assert by_label == {"Seed": "EUR", "Series A": "USD", "Series B": "USD", "Series C": None}


async def test_brief_refs_to_another_competitors_rows_or_deleted_ones_are_dropped(
    auth_client, acme, session, competitor_factory, make_source
):
    """A cited posting or page change of another competitor, and a deleted posting,
    resolve to nothing: no source, no number."""
    other = await new_comp(session, competitor_factory, slug="other-co", name="Other", tier=2)
    jobs = await board(make_source, other, listed=10)
    theirs = posting(other, jobs, 1, title="Other's role")
    page_src = await make_source(
        kind="page_watch", name="Other pricing", competitor_id=other.id,
        config={"page": "pricing", "url": "https://other.example/pricing"},
    )  # fmt: skip
    their_page = PageSnapshot(
        source_id=page_src.id, fetched_at=at(ago(3)), text_hash="o", text="x",
        page_kind="pricing", meaningful=True, change_kind="pricing", change_summary="Other's",
    )  # fmt: skip
    second = await board(make_source, acme["comp"], listed=60, name="Acme second board")
    gone = posting(acme["comp"], second, 99, title="Gone role")
    session.add_all([theirs, their_page, gone])
    await session.flush()
    gone_ref = f"posting:{gone.id}"
    await session.execute(delete(JobPosting).where(JobPosting.id == gone.id))
    launch = acme["brief_refs"]["launch"]
    refs = [f"posting:{theirs.id}", f"page:{their_page.id}", gone_ref, launch]
    session.add(
        CompetitorBrief(
            competitor_id=acme["comp"].id,
            version=3,
            content={"bottom_line": [{"text": "Refs.", "cites": refs}]},
            model="m",
            prompt_version="1+check1",
        )
    )
    await session.flush()
    p = (await (await auth_client("pm")).get("/api/v1/competitors/acme/profile")).json()
    brief = p["brief"]
    assert brief["version"] == 3
    assert [s["ref"] for s in brief["sources"]] == [launch]
    assert brief["bottom_line"][0]["cites"] == [1]
    text = json.dumps(p)
    assert "Other's role" not in text and "Other's" not in text and "Gone role" not in text


# ---- postings ------------------------------------------------------------------------------


@pytest.fixture
async def big_board(seeded, session, competitor_factory, make_source):
    comp = await new_comp(session, competitor_factory, slug="bigco", name="BigCo", tier=1)
    src = await board(make_source, comp, listed=90)
    session.add_all(
        posting(
            comp,
            src,
            n,
            seen=n % 90 + 1,
            closed=0 if n % 4 == 0 else None,
            department="Security" if n % 3 == 0 else "Engineering",
            tags=["sca"] if n % 2 == 0 else [],
        )
        for n in range(120)
    )
    await session.flush()
    return {"comp": comp, "board": src}


async def test_postings_paging_and_filters(auth_client, big_board):
    c = await auth_client("pm")
    base = "/api/v1/competitors/bigco/postings"
    seen, cursor, pages = [], None, 0
    while True:
        r = await c.get(base, params={"limit": 50, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200
        page = r.json()
        seen += page["items"]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and len(seen) == 120 and len({p["id"] for p in seen}) == 120
    keys = [(p["first_seen_at"], p["id"]) for p in seen]
    assert keys == sorted(keys, reverse=True)  # newest first, stable across pages
    assert set(seen[0]) == {
        "id",
        "title",
        "department",
        "seniority",
        "location",
        "country",
        "remote",
        "url",
        "first_seen_at",
        "closed_at",
        "tags",
        "tag_state",
    }

    async def count(**params) -> int:
        page = (await c.get(base, params={"limit": 200, **params})).json()
        assert page["next_cursor"] is None
        return len(page["items"])

    assert await count(status="closed") == 30
    assert await count(status="open") == 90
    assert await count(department="Security") == 40
    assert await count(tag="sca") == 60
    assert await count(status="open", department="Security", tag="sca") == 10
    assert (await c.get(base, params={"department": "Astrology"})).status_code == 422
    assert (await c.get(base, params={"status": "maybe"})).status_code == 422
    # a tag outside the capability taxonomy is a 422, like an unknown department
    r = await c.get(base, params={"tag": "astrology"})
    assert r.status_code == 422 and "capability" in r.json()["detail"]
    assert await count(tag="model-registry") == 0  # a real capability nobody is hiring for
    # the page size is 1..200
    for limit in (0, 201):
        assert (await c.get(base, params={"limit": limit})).status_code == 422, limit
    assert len((await c.get(base, params={"limit": 200})).json()["items"]) == 120


def _b64(payload) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!!",
        _b64([]),
        _b64({"at": "x", "id": 1}),
        _b64({"at": "2026-10-01T00:00:00+00:00"}),
        _b64({"at": "2026-10-01T00:00:00+00:00", "id": True}),
        _b64({"at": "2026-10-01T00:00:00", "id": 1}),  # no time zone
        "x" * 300,
    ],
)
async def test_postings_forged_cursor_is_422(auth_client, big_board, cursor):
    c = await auth_client("pm")
    r = await c.get("/api/v1/competitors/bigco/postings", params={"cursor": cursor})
    assert r.status_code == 422


async def test_postings_without_an_enabled_board_are_not_served(auth_client, big_board, session):
    """The profile's "No public ... board found" holds for the list too."""
    big_board["board"].enabled = False
    await session.flush()
    page = (await (await auth_client("pm")).get("/api/v1/competitors/bigco/postings")).json()
    assert page == {"items": [], "next_cursor": None}


# ---- performance ----------------------------------------------------------


async def test_profile_statement_budget_with_600_postings_and_180_days(
    auth_client, seeded, session, competitor_factory, make_signal, make_source, make_document,
    sql_counter,
):  # fmt: skip
    comp = await new_comp(session, competitor_factory, slug="heavy", name="Heavy", tier=1)
    session.add(
        CompetitorProduct(
            competitor_id=comp.id,
            name="Heavy Repo",
            category="Repo",
            url=None,
            jfrog_products=["Artifactory", "Xray"],
        )
    )
    types = (
        "product_launch",
        "pricing_change",
        "partnership",
        "funding",
        "leadership_change",
        "security_incident",
        "customer_win_loss",
    )
    signals = []
    for days in range(180):  # a visible, analysed signal every day, of every lane
        s = await signal(
            make_signal, comp, ago(days), signal_type=types[days % 7], importance=days % 60
        )
        await analyse(session, s, hit("Artifactory"), hit("Xray", effect="opportunity"))
        signals.append(s)
    src = await board(make_source, comp, listed=180)
    departments = ("Engineering", "Security", "Sales", "Product", "Marketing")
    postings = [
        posting(
            comp,
            src,
            n,
            seen=n % 180 + 1,
            closed=(n % 30) if n % 5 == 0 else None,
            department=departments[n % 5],
            tags=[("sca", "sbom", "sast")[n % 3]],
        )
        for n in range(600)
    ]
    session.add_all(postings)
    wire = await make_source(name="Wire", competitor_id=comp.id)
    hn = await make_source(
        kind="hn", name="HN heavy", url="generated://hn/heavy", competitor_id=comp.id
    )
    pricing = await make_source(
        kind="page_watch",
        name="Heavy pricing",
        url="https://heavy.example/pricing",
        competitor_id=comp.id,
        config={"page": "pricing", "url": "https://heavy.example/pricing"},
    )
    kinds = ("headcount", "funding_round", "customer", "partnership")
    facts = []
    for n in range(40):
        doc = await news(make_document, wire, f"Heavy item {n}", ago(n * 4 + 1))
        facts.append(
            await fact(
                session,
                comp,
                kinds[n % 4],
                f"label {n % 8}",
                source=doc,
                value=100 + n,
                as_of=ago(n * 4 + 1),
            )
        )
        hn_doc = await make_document(
            source_id=hn.id, title=f"HN {n}", published_at=at(ago(n)), status="relevant"
        )
        session.add(DocumentCompetitor(document_id=hn_doc.id, competitor_id=comp.id))
    snaps = [
        PageSnapshot(
            source_id=pricing.id,
            fetched_at=at(ago(n * 10)),
            text_hash=f"h{n}",
            text=f"v{n}",
            diff_from_prev=f"-v{n + 1}\n+v{n}",
            page_kind="pricing",
            meaningful=True,
            change_kind="pricing",
            change_summary=f"Change {n}",
        )
        for n in range(10)
    ]
    session.add_all(snaps)
    for day in range(180):
        for metric in ("github_stars:heavy/repo", "github_forks:heavy/repo"):
            session.add(MetricPoint(competitor_id=comp.id, metric=metric, date=ago(day), value=day))
    await session.flush()
    closed = next(p for p in postings if p.closed_at is not None)
    refs = [
        f"signal:{signals[0].id}",
        f"fact:{facts[0].id}",
        f"posting:{closed.id}",
        f"page:{snaps[3].id}",
    ]
    edge = {"text": "Edge.", "cites": [f"fact:{facts[1].id}"]}
    session.add(
        CompetitorBrief(
            competitor_id=comp.id,
            version=1,
            model="m",
            prompt_version="1+check1",
            content={
                "bottom_line": [{"text": "Heavy is busy.", "cites": refs}],
                "product_edges": [
                    {"product": "Heavy Repo", "jfrog_product": "Artifactory", "edge": edge}
                ],
                "watch": [],
            },
        )
    )
    await session.flush()

    c = await auth_client("pm")
    sql_counter.clear()
    r = await c.get("/api/v1/competitors/heavy/profile")
    assert r.status_code == 200
    # the budget is 25; the accounting in app/api/competitor_profile.py gives at most 24 (no second
    # competitor load; the collected-since day, read once for momentum and share of voice), 23 here
    # (every card's signal is analysed: no thin check)
    assert len(sql_counter) == 23, len(sql_counter)
    body = r.json()
    assert len(body["timeline"]) == 180 and body["hiring"]["has_board"] is True
    assert len(body["brief"]["sources"]) == 5
    assert len(body["latest_signals"]) == 20
    assert len(body["community"]["repos"][0]["stars"]) == 180


# ---- schema --------------------------------------------------------------------------------


def test_openapi_names_the_new_funnel_counts_and_requires_every_field():
    schemas = app.openapi()["components"]["schemas"]
    funnel = schemas["BriefingOut"]["properties"]["funnel"]["description"]
    for key in ("postings_tagged", "page_changes", "facts_extracted"):
        assert key in funnel, key
    assert set(schemas["CompetitorProfile"]["required"]) == PROFILE_SECTIONS
    for name in (
        "CompetitorSummary",
        "PostingOut",
        "IntelSourceOut",
        "FactOut",
        "ThreatOut",
        "FundingTotalOut",
    ):
        props = schemas[name]["properties"]
        assert set(schemas[name]["required"]) == set(props), name  # always sent
    assert set(schemas["CompetitorOut"]["required"]) >= {"description", "ticker"}
    assert "undated_rounds" in schemas["FundingTotalOut"]["required"]
    assert "open_roles_note" in schemas["CompetitorSummary"]["required"]
    # both deep dives say which signals their cards show
    for schema, field in (("CompetitorProfile", "latest_signals"), ("ProductProfile", "signals")):
        assert "public" in schemas[schema]["properties"][field]["description"], schema


async def test_jfrog_rows_stay_unreachable_even_when_cited(auth_client, acme, session, seeded):
    """A brief citing a JFrog posting or page (impossible by construction) resolves to
    nothing, as a row of any other competitor does."""
    me = (await session.execute(select(Competitor).where(Competitor.is_self))).scalar_one()
    src = await session.scalar(select(Source).where(Source.competitor_id == me.id).limit(1))
    assert src is not None
    mine = JobPosting(
        source_id=src.id, competitor_id=me.id, ats="lever", external_id="jf-1",
        title="JFrog internal role", department="Engineering", seniority="Mid",
        content_hash="jf", first_seen_at=at(ago(3)),
    )  # fmt: skip
    session.add(mine)
    await session.flush()
    session.add(
        CompetitorBrief(
            competitor_id=acme["comp"].id,
            version=3,
            content={"bottom_line": [{"text": "x", "cites": [f"posting:{mine.id}"]}]},
            model="m",
            prompt_version="1+check1",
        )
    )
    await session.flush()
    p = (await (await auth_client("pm")).get("/api/v1/competitors/acme/profile")).text
    assert "JFrog internal role" not in p
