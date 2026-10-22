"""Read APIs for the JFrog product deep dive: the 13 products with their pressure and
top competitors, and one product's profile (the description comes from the JFrog
context pack's product portfolio and never carries JFrog company figures:
kebab-case slugs, unknown slugs 404)."""

import json

import pytest
from sqlalchemy import select

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.api import intel_out
from app.models import Competitor, CompetitorProduct, JobPosting, ProductBrief
from tests.intel.test_profile import NOW, ago, analyse, at, hit, new_comp, signal

SLUGS = [
    "artifactory",
    "xray",
    "advanced-security",
    "curation",
    "distribution",
    "apptrust",
    "runtime",
    "jfrog-ml",
    "ai-catalog",
    "mcp-registry",
    "container-registry",
    "connect",
    "platform",
]
# JFrog company figures in seed/jfrog_context.md's financial snapshot (never served)
JFROG_FIGURES = ("163.8", "87.5", "121%", "97 customers", "29%", "53%")


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    monkeypatch.setattr(intel_out, "utcnow", lambda: NOW)


def add_product(session, comp, name, *jfrog):
    session.add(
        CompetitorProduct(
            competitor_id=comp.id, name=name, category="Cat", url=None, jfrog_products=list(jfrog)
        )
    )


def job(comp, src, n, *, tags, closed=None, seen=10) -> JobPosting:
    return JobPosting(
        source_id=src.id,
        competitor_id=comp.id,
        ats="lever",
        external_id=f"{comp.slug}-{n}",
        title=f"Role {n}",
        department="Engineering",
        seniority="Mid",
        content_hash=f"{comp.slug}-{n}",
        first_seen_at=at(ago(seen)),
        closed_at=at(ago(closed)) if closed is not None else None,
        tags=tags,
    )


@pytest.fixture
async def market(seeded, session, competitor_factory, make_signal, make_source):
    """Xray under rising pressure (threat only in the last four weeks), Artifactory steady
    (one threat a week), Curation easing (threat only before the last four weeks)."""
    alpha = await new_comp(session, competitor_factory, slug="alpha", name="Alpha", tier=1)
    beta = await new_comp(session, competitor_factory, slug="beta", name="Beta", tier=2)
    add_product(session, alpha, "Alpha Scan", "Xray")
    add_product(session, beta, "Beta Repo", "Artifactory")
    s = {"alpha": alpha, "beta": beta}
    s["x1"] = await signal(make_signal, alpha, ago(3), title="Alpha ships a scanner")
    await analyse(session, s["x1"], hit("Xray"))
    s["x2"] = await signal(
        make_signal, beta, ago(10), signal_type="pricing_change", title="Beta cuts scan prices"
    )
    await analyse(session, s["x2"], hit("Xray", severity="medium"))
    for week in range(13):
        sig = await signal(make_signal, beta, ago(week * 7), title=f"Beta repo week {week}")
        await analyse(session, sig, hit("Artifactory", severity="low"))
    for days in (40, 60, 80):
        sig = await signal(make_signal, alpha, ago(days), title=f"Alpha gate {days}")
        await analyse(session, sig, hit("Curation"))
    s["review"] = await signal(
        make_signal, alpha, ago(2), status="needs_review", title="Xray rumour under review"
    )
    await analyse(session, s["review"], hit("Xray"))
    s["rejected"] = await signal(
        make_signal, alpha, ago(4), status="rejected", title="Rejected Xray story"
    )
    await analyse(session, s["rejected"], hit("Xray"))

    # hiring in the Xray area (sca, sbom, ...): alpha's board counts (it was listed in full
    # 40 days ago, so it is measured), beta has none enabled
    jobs = await make_source(
        kind="job_board",
        competitor_id=alpha.id,
        config={"ats": "lever"},
        last_fetched_at=at(ago(0)),
        first_complete_fetch_at=at(ago(40)),
        last_complete_fetch_at=at(ago(0)),
    )
    off = await make_source(
        kind="job_board", competitor_id=beta.id, enabled=False, config={"ats": "lever"}
    )
    session.add_all(
        [
            job(alpha, jobs, 1, tags=["sca"]),
            job(alpha, jobs, 2, tags=["sbom", "sca"]),  # counted once
            job(alpha, jobs, 3, tags=["model-registry"]),  # another product's area
            job(alpha, jobs, 4, tags=["sca"], seen=40, closed=20),
            job(beta, off, 1, tags=["sca"]),  # no enabled board
        ]
    )
    session.add(
        ProductBrief(
            product="Xray",
            version=1,
            content={
                "bottom_line": [
                    {"text": "Xray faces new scanners.", "cites": [f"signal:{s['x1'].id}"]},
                    {
                        "text": "Prices are under pressure.",
                        "cites": [f"signal:{s['x2'].id}", f"signal:{s['x1'].id}"],
                    },
                ],
                "gaps_parity": [
                    {"text": "Parity on SBOM export.", "cites": [f"signal:{s['rejected'].id}"]}
                ],
                "actions": {
                    "sales": ["Lead with contextual analysis."],
                    "pm": ["Review the scan pricing."],
                    "exec": [],
                    "pmm_devrel": ["Publish a comparison."],
                },
                "evidence": {"signals": 2},
                "input_hash": "h",
            },
            model="m",
            prompt_version="1+check1",
            created_at=at(ago(1)),
        )
    )
    await session.flush()
    return s


async def test_products_require_auth(client, seeded):
    for path in ("/api/v1/products", "/api/v1/products/xray"):
        assert (await client.get(path)).status_code == 401, path


async def test_products_list_the_thirteen_with_pressure(auth_client, market, sql_counter):
    c = await auth_client("pm")
    sql_counter.clear()
    rows = (await c.get("/api/v1/products")).json()
    assert len(sql_counter) == 4  # auth, then one signal scan for all thirteen (3)
    assert [r["slug"] for r in rows] == SLUGS
    assert [r["name"] for r in rows] == list(JFROG_PRODUCTS)
    by = {r["slug"]: r for r in rows}
    assert by["xray"]["pressure"] == "rising"  # threat only in the last four weeks
    assert by["artifactory"]["pressure"] == "steady"
    assert by["curation"]["pressure"] == "easing"
    assert by["connect"]["pressure"] == "none"
    assert by["xray"]["top_competitors"][:2] == ["alpha", "beta"]  # by evidence
    assert len(by["xray"]["top_competitors"]) == 3
    assert all("jfrog" not in r["top_competitors"] for r in rows)
    assert set(rows[0]) == {
        "slug",
        "name",
        "description",
        "pressure",
        "pressure_baseline",
        "top_competitors",
    }
    # no document filed before NOW, so every baseline was collected; Xray's 9 baseline
    # weeks name no threat, so it has no baseline yet (as a momentum trend), never "rising"
    assert {r["slug"] for r in rows if not r["pressure_baseline"]} == {"xray"}
    # each product says what it is, in the context pack's words (capabilities only)
    assert by["xray"]["description"].startswith("Software composition analysis; continuously")
    assert all(r["description"] for r in rows)
    for figure in JFROG_FIGURES:
        assert figure not in json.dumps(rows), figure


async def test_young_collection_serves_no_pressure_before_it_and_no_trend_yet(
    auth_client, market, make_document
):
    """FrogEye began collecting 20 days ago (its first document). The pressure
    weeks start at the week holding that day, never quiet weeks before it, and no product's
    pressure is a trend yet: `pressure_baseline` is false (the pages say "no baseline yet"),
    and the day itself is served."""
    await make_document(created_at=at(ago(20)))
    c = await auth_client("pm")
    rows = {r["slug"]: r for r in (await c.get("/api/v1/products")).json()}
    assert {r["pressure_baseline"] for r in rows.values()} == {False}
    p = (await c.get("/api/v1/products/xray")).json()
    assert (p["pressure_baseline"], p["collected_since"]) == (False, "2026-09-30")
    assert [w["week_start"] for w in p["pressure_series"]] == [
        "2026-09-30",
        "2026-10-07",
        "2026-10-14",
    ]


async def test_product_profile_sections(auth_client, market):
    s = market
    p = (await (await auth_client("sales")).get("/api/v1/products/xray")).json()
    assert set(p) == {
        "slug",
        "name",
        "description",
        "pressure",
        "pressure_baseline",
        "pressure_series",
        "collected_since",
        "competitors",
        "brief",
        "hiring_in_area",
        "timeline",
        "signals",
    }
    assert (p["slug"], p["name"], p["pressure"]) == ("xray", "Xray", "rising")
    assert p["description"].startswith("Software composition analysis; continuously scans")
    weeks = p["pressure_series"]
    assert len(weeks) == 13
    assert weeks[-1] == {"week_start": "2026-10-14", "threat": 3.0, "opportunity": 0.0}
    assert sum(w["threat"] for w in weeks) == 5.0  # x1 high 3 + x2 medium 2

    alpha, beta, *rest = p["competitors"]
    assert (alpha["competitor_slug"], alpha["their_products"], alpha["evidence"]) == (
        "alpha",
        ["Alpha Scan"],
        3,
    )
    assert alpha["last_move"]["signal_id"] == s["x1"].id
    assert (beta["competitor_slug"], beta["their_products"], beta["threat_level"]) == (
        "beta",
        [],
        "low",
    )
    assert all(c["competitor_slug"] != "jfrog" for c in rest)

    brief = p["brief"]
    assert brief["version"] == 1 and brief["created_at"].startswith(ago(1).isoformat())
    assert [x["cites"] for x in brief["bottom_line"]] == [[1], [2, 1]]
    assert brief["gaps_parity"][0]["cites"] == [3]
    assert brief["actions"] == {
        "sales": ["Lead with contextual analysis."],
        "pm": ["Review the scan pricing."],
        "exec": [],
        "pmm_devrel": ["Publish a comparison."],
    }
    assert brief["evidence"] == {"signals": 2}
    first, second, hidden = brief["sources"]
    assert (first["ref"], first["title"], first["url"]) == (
        f"signal:{s['x1'].id}",
        "Alpha ships a scanner",
        f"/signals/{s['x1'].id}",
    )
    assert second["ref"] == f"signal:{s['x2'].id}"
    assert (hidden["title"], hidden["url"]) == ("Signal not available", None)

    area = p["hiring_in_area"]
    assert {"sca", "sbom", "license-compliance", "container-scanning"} <= set(area["capabilities"])
    assert "model-registry" not in area["capabilities"]
    # alpha's board was first read 40 days ago (its earliest posting): the area's
    # series start there, never zero-filled over the 90-day window before it
    assert area["boards"] == [
        {
            "competitor_slug": "alpha",
            "board_since": "2026-09-10",
            "last_listed": "2026-10-20",
            "stale": False,
        }
    ]
    assert len(area["total"]) == 41
    assert area["total"][0] == {"day": "2026-09-10", "count": 1}  # only 4 was open then
    assert area["total"][-1] == {"day": "2026-10-20", "count": 2}  # 1 and 2 (once); 4 closed
    assert area["total"][-30] == {"day": "2026-09-21", "count": 1}
    assert {pt["key"] for pt in area["by_competitor"]} == {"alpha"}  # beta has no enabled board
    assert area["by_competitor"][0]["day"] == "2026-09-10"

    events = [(e["competitor_slug"], e["signal_id"]) for e in p["timeline"]]
    assert events == [("beta", s["x2"].id), ("alpha", s["x1"].id)]  # oldest first
    assert p["timeline"][0]["lane"] == "Pricing"
    assert [c["id"] for c in p["signals"]] == [s["x1"].id, s["x2"].id]  # newest first

    text = json.dumps(p)
    assert "Rejected Xray story" not in text and "Xray rumour under review" not in text
    for figure in JFROG_FIGURES:
        assert figure not in text, figure


async def test_product_without_competitive_signals_is_honestly_empty(auth_client, seeded):
    p = (await (await auth_client("pm")).get("/api/v1/products/connect")).json()
    assert p["pressure"] == "none"
    assert p["pressure_series"] == []  # no evidence at all: never thirteen zero weeks
    assert p["brief"] is None and p["timeline"] == [] and p["signals"] == []
    assert p["description"].startswith("IoT device management")
    assert p["hiring_in_area"]["capabilities"] == ["edge-distribution"]
    assert p["hiring_in_area"]["total"] == [] and p["hiring_in_area"]["by_competitor"] == []
    assert p["hiring_in_area"]["boards"] == []


async def test_every_product_has_a_description_without_jfrog_figures(auth_client, seeded):
    c = await auth_client("exec")
    for slug in SLUGS:
        p = (await c.get(f"/api/v1/products/{slug}")).json()
        assert p["description"], slug
        for figure in JFROG_FIGURES:
            assert figure not in p["description"], (slug, figure)


async def test_unknown_product_slugs_404(auth_client, seeded):
    c = await auth_client("pm")
    for slug in ("nexus", "JFrog ML", "jfrog_ml", "Xray", "pipelines"):
        assert (await c.get(f"/api/v1/products/{slug}")).status_code == 404, slug
    assert (await c.get("/api/v1/products/jfrog-ml")).status_code == 200
    assert (await c.get("/api/v1/products/ai-catalog")).status_code == 200


async def test_product_profile_statements_are_bounded(auth_client, market, sql_counter):
    c = await auth_client("pm")
    sql_counter.clear()
    assert (await c.get("/api/v1/products/xray")).status_code == 200
    assert len(sql_counter) <= 12


async def test_hiring_in_area_starts_each_board_at_its_first_snapshot(
    auth_client, market, session, competitor_factory, make_source, sql_counter
):
    """Each competitor's area line starts at its board's first
    snapshot (board_since), never zero-filled over the days before the board was read, and the
    area total at the earliest board's. Only a board listed in full is read (a
    partial listing is not); every competitor is served (the chart folds its Other on the
    client), JFrog never."""

    async def board(slug, *, listed: int | None = 0, fetched: bool = True):
        comp = await new_comp(session, competitor_factory, slug=slug, name=slug.title(), tier=3)
        src = await make_source(
            kind="job_board",
            competitor_id=comp.id,
            config={"ats": "ashby"},
            last_fetched_at=at(ago(0)) if fetched else None,
            first_complete_fetch_at=at(ago(listed)) if listed is not None else None,
            last_complete_fetch_at=at(ago(0)) if listed is not None else None,
        )
        return comp, src

    gamma, young = await board("gamma", listed=5)  # a young board: first read 5 days ago
    session.add(job(gamma, young, 1, tags=["sbom"], seen=5))
    delta, pending = await board("delta", listed=None, fetched=False)  # never fetched: not read
    session.add(job(delta, pending, 1, tags=["sca"], seen=20))
    # fetched, but only ever listed in part (a short Greenhouse page): not read either
    zeta, partial = await board("zeta", listed=None)
    session.add(job(zeta, partial, 1, tags=["sca"], seen=2))
    await board("epsilon")  # listed in full today, no posting listed yet
    for n in range(9):  # nine more boards with an area role: still no server-side "Other"
        comp, src = await board(f"crowd-{n}", listed=3)
        session.add(job(comp, src, 1, tags=["sca"], seen=3))
    me = await session.scalar(select(Competitor).where(Competitor.is_self.is_(True)))
    mine = await make_source(
        kind="job_board",
        competitor_id=me.id,
        config={"ats": "lever"},
        last_fetched_at=at(ago(0)),
        first_complete_fetch_at=at(ago(60)),
        last_complete_fetch_at=at(ago(0)),
    )
    session.add(job(me, mine, 1, tags=["sca"], seen=60))  # never counted, never a board
    await session.flush()

    c = await auth_client("pm")
    sql_counter.clear()
    area = (await c.get("/api/v1/products/xray")).json()["hiring_in_area"]
    assert len(sql_counter) <= 12

    crowd = [f"crowd-{n}" for n in range(9)]
    today = {"last_listed": "2026-10-20", "stale": False}  # all listed in full today
    assert area["boards"] == [
        {"competitor_slug": "alpha", "board_since": "2026-09-10", **today},
        {"competitor_slug": "gamma", "board_since": "2026-10-15", **today},
        *({"competitor_slug": s, "board_since": "2026-10-17", **today} for s in crowd),
        # a board listed in full with no posting is a measured zero from its first listing
        {"competitor_slug": "epsilon", "board_since": "2026-10-20", **today},
    ]
    firsts: dict[str, str] = {}
    for pt in area["by_competitor"]:
        firsts.setdefault(pt["key"], pt["day"])
    assert set(firsts) == {"alpha", "gamma", *crowd}  # no "Other", no delta/zeta, no JFrog
    assert firsts["alpha"] == "2026-09-10"
    assert firsts["gamma"] == "2026-10-15"  # its board's first snapshot, not the window's start
    assert all(firsts[s] == "2026-10-17" for s in crowd)
    gamma_days = [pt for pt in area["by_competitor"] if pt["key"] == "gamma"]
    assert [pt["count"] for pt in gamma_days] == [1] * 6  # 15-20 Oct, never a zero before
    assert area["total"][0]["day"] == "2026-09-10"
    assert area["total"][-1] == {"day": "2026-10-20", "count": 12}  # alpha 2, gamma 1, crowd 9


async def test_a_stale_board_is_listed_but_not_counted_in_the_area(
    auth_client, market, session, competitor_factory, make_source
):
    """A board not listed in full for board_stale_days is listed with its last
    complete listing and counted nowhere; the area ends on the last day every board counted
    was listed in full (here two days ago), so no board is carried flat past its listing."""

    async def board(slug, *, last: int):
        comp = await new_comp(session, competitor_factory, slug=slug, name=slug.title(), tier=3)
        src = await make_source(
            kind="job_board",
            competitor_id=comp.id,
            config={"ats": "ashby"},
            last_fetched_at=at(ago(last)),
            first_complete_fetch_at=at(ago(30)),
            last_complete_fetch_at=at(ago(last)),
        )
        session.add(job(comp, src, 1, tags=["sca"], seen=20))
        return comp

    await board("gone", last=6)
    await board("lag", last=2)
    await session.flush()
    area = (await (await auth_client("pm")).get("/api/v1/products/xray")).json()["hiring_in_area"]
    by = {b["competitor_slug"]: b for b in area["boards"]}
    assert (by["gone"]["stale"], by["gone"]["last_listed"]) == (True, "2026-10-14")
    assert (by["lag"]["stale"], by["lag"]["last_listed"]) == (False, "2026-10-18")
    assert "gone" not in {pt["key"] for pt in area["by_competitor"]}
    assert area["total"][-1]["day"] == "2026-10-18"
    assert max(pt["day"] for pt in area["by_competitor"]) == "2026-10-18"
