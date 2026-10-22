"""The comparison matrix (GET /compare): JFrog's products against the competitors with
seeded products, each value the one the product and competitor pages already serve."""

import pytest

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.api import intel_out
from tests.api import test_products_api
from tests.api.test_products_api import SLUGS, add_product
from tests.intel.test_profile import NOW, ago, analyse, hit, new_comp, signal

market = test_products_api.market  # the product pages' market, read through the matrix


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    monkeypatch.setattr(intel_out, "utcnow", lambda: NOW)


async def test_compare_requires_auth(client, seeded):
    assert (await client.get("/api/v1/compare")).status_code == 401


async def test_compare_matrix(auth_client, market, sql_counter):
    s = market
    c = await auth_client("sales")
    sql_counter.clear()
    m = (await c.get("/api/v1/compare")).json()
    # auth 1, the competitor overview 7, the products' one scan 3, the seed 1
    assert len(sql_counter) == 12
    assert set(m) == {
        "as_of",
        "lookback_days",
        "collected_since",
        "products",
        "competitors",
        "cells",
    }
    assert (m["as_of"], m["lookback_days"], m["collected_since"]) == ("2026-10-20", 180, None)

    # rows: the 13 products in the list's order, with the list's pressure
    products = (await c.get("/api/v1/products")).json()
    assert [p["slug"] for p in m["products"]] == SLUGS
    assert [p["name"] for p in m["products"]] == list(JFROG_PRODUCTS)
    assert [(p["pressure"], p["pressure_baseline"]) for p in m["products"]] == [
        (p["pressure"], p["pressure_baseline"]) for p in products
    ]

    # columns: every competitor with a seeded product, by tier then threat; never JFrog
    overview = {r["slug"]: r for r in (await c.get("/api/v1/competitors/overview")).json()}
    cols = m["competitors"]
    assert "jfrog" not in [col["slug"] for col in cols]
    assert {"alpha", "beta"} <= {col["slug"] for col in cols}
    assert [col["tier"] for col in cols] == sorted(col["tier"] for col in cols)
    for col in cols:
        assert col["threat"] == overview[col["slug"]]["threat"]
        assert col["product_categories"] == overview[col["slug"]]["product_categories"]

    cells = {(x["product_slug"], x["competitor_slug"]): x for x in m["cells"]}
    alpha = cells["xray", "alpha"]
    assert alpha["their_products"] == [{"name": "Alpha Scan", "category": "Cat", "url": None}]
    # x1 is high (3); the signals under review and rejected never count
    assert (alpha["threat_level"], alpha["threat_weight"], alpha["evidence"]) == ("medium", 3, 3)
    assert [(t["signal_id"], t["weight"], t["severity"]) for t in alpha["threat_signals"]] == [
        (s["x1"].id, 3, "high")
    ]
    assert alpha["last_move"]["signal_id"] == s["x1"].id
    # no seeded product for Xray, but its signals name it: a cell with evidence only
    beta = cells["xray", "beta"]
    assert (beta["their_products"], beta["threat_level"], beta["threat_weight"]) == ([], "low", 2)
    assert beta["threat_signals"][0]["title"] == "Beta cuts scan prices"
    # no product seeded and no evidence: no cell
    assert ("connect", "alpha") not in cells

    # every cell is the product page's Competitors row for that competitor
    for slug in ("xray", "artifactory", "curation"):
        page = (await c.get(f"/api/v1/products/{slug}")).json()
        for row in page["competitors"]:
            cell = cells.get((slug, row["competitor_slug"]))
            if row["competitor_slug"] not in {col["slug"] for col in cols}:
                assert cell is None
                continue
            assert cell is not None
            assert [p["name"] for p in cell["their_products"]] == row["their_products"]
            assert (cell["threat_level"], cell["evidence"], cell["last_move"]) == (
                row["threat_level"],
                row["evidence"],
                row["last_move"],
            )

    # cells run row by row, then in the columns' order
    order = {col["slug"]: i for i, col in enumerate(cols)}
    rows = {p["slug"]: i for i, p in enumerate(m["products"])}
    keys = [(rows[x["product_slug"]], order[x["competitor_slug"]]) for x in m["cells"]]
    assert keys == sorted(keys)


async def test_compare_columns_need_a_seeded_product(
    auth_client, seeded, session, competitor_factory, make_signal
):
    """A competitor with signals naming a product but no seeded product is no column, so
    none of its evidence is served; one seeded for one product has cells for it only."""
    loose = await new_comp(session, competitor_factory, slug="loose", name="Loose", tier=1)
    sig = await signal(make_signal, loose, ago(2), title="Loose ships a scanner")
    await analyse(session, sig, hit("Xray"), hit("Curation", effect="opportunity"))
    tidy = await new_comp(session, competitor_factory, slug="tidy", name="Tidy", tier=2)
    add_product(session, tidy, "Tidy Gate", "Curation")
    await session.flush()
    m = (await (await auth_client("pm")).get("/api/v1/compare")).json()
    assert "loose" not in {col["slug"] for col in m["competitors"]}
    assert all(x["competitor_slug"] != "loose" for x in m["cells"])
    tidy_cells = [x for x in m["cells"] if x["competitor_slug"] == "tidy"]
    assert [(x["product_slug"], x["threat_level"], x["last_move"]) for x in tidy_cells] == [
        ("curation", "none", None)
    ]
