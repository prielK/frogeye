"""Reference data for the Signals Explorer facets: capabilities by category, and KITs."""

from sqlalchemy import func, select

from app.models import Capability, CapabilityCategory, Kit


async def test_reference_endpoints_require_auth(client, seeded):
    for path in ("/capabilities", "/kits"):
        assert (await client.get(f"/api/v1{path}")).status_code == 401


async def test_capabilities_grouped_by_category_in_seed_order(auth_client, seeded):
    r = await (await auth_client("sales")).get("/api/v1/capabilities")
    assert r.status_code == 200
    body = r.json()

    categories = list(
        await seeded.scalars(select(CapabilityCategory).order_by(CapabilityCategory.order))
    )
    assert [c["slug"] for c in body] == [c.slug for c in categories]
    first = body[0]
    assert (first["slug"], first["name"]) == ("artifact-management", "Artifact management")
    assert set(first) == {"slug", "name", "capabilities"}

    # Every capability appears once, under its category, sorted by name (ignoring case).
    total = await seeded.scalar(select(func.count()).select_from(Capability))
    assert sum(len(c["capabilities"]) for c in body) == total
    for category in body:
        names = [cap["name"] for cap in category["capabilities"]]
        assert names == sorted(names, key=str.casefold)
    assert {"slug": "container-registry", "name": "Container registry"} in first["capabilities"]
    ai = next(c for c in body if c["slug"] == "ai-ml-supply-chain")
    assert {"slug": "model-registry", "name": "Model registry"} in ai["capabilities"]


async def test_kits_listed_by_priority(auth_client, seeded):
    r = await (await auth_client("exec")).get("/api/v1/kits")
    assert r.status_code == 200
    body = r.json()

    kits = list(await seeded.scalars(select(Kit).order_by(Kit.priority, Kit.slug)))
    assert [k["slug"] for k in body] == [k.slug for k in kits]
    assert set(body[0]) == {"slug", "title", "question"}
    supply = next(k for k in body if k["slug"] == "ai-supply-chain")
    assert supply["title"] == "AI supply chain"
    assert supply["question"].startswith("AI supply chain")


# ---- the intelligence pages' rules ------------------------


async def test_intel_reference_serves_the_rules_the_pages_quote(
    auth_client, seeded, settings, monkeypatch
):
    """The frontend quotes the JFrog product list, the severity weights and
    the overlap, threat, trend and brief thresholds in its "How this is computed" copy and
    its trend words; it reads them here, from the backend's own constants and settings,
    instead of keeping copies."""
    from app.agents.analysis_schema import JFROG_PRODUCTS
    from app.api.v1 import reference as reference_api

    c = await auth_client("sales")
    body = (await c.get("/api/v1/intel-reference")).json()
    assert body["jfrog_products"] == list(JFROG_PRODUCTS)
    assert body["severity_weights"] == {"high": 3, "medium": 2, "low": 1}
    assert body["overlap"] == {"high_evidence": 6, "medium_evidence": 3, "lookback_days": 180}
    assert body["threat"] == {
        "high_score": 12.0,
        "medium_score": 4.0,
        "min_signals": 3,
        "lookback_days": 180,
        "momentum_factor_min": 0.5,
        "momentum_factor_max": 2.0,
        "hiring_factor_bound": 0.25,
        "hiring_history_days": 30,
    }
    assert body["trend"] == {
        "weeks": 13,
        "recent_weeks": 4,
        "baseline_weeks": 9,
        "cap": 3.0,
        "rising_ratio": 1.25,
        "easing_ratio": 0.8,
    }
    assert body["briefs"] == {"min_signals": 3, "lookback_days": 180}
    assert body["collected_since"] is None  # no document collected yet

    tuned = settings.model_copy(update={"threat_high_score": 15.0, "pressure_rising_ratio": 1.5})
    monkeypatch.setattr(reference_api, "get_settings", lambda: tuned)
    body = (await c.get("/api/v1/intel-reference")).json()
    assert (body["threat"]["high_score"], body["trend"]["rising_ratio"]) == (15.0, 1.5)


async def test_intel_reference_says_since_when_frogeye_collects(auth_client, seeded, make_document):
    """The "collected since" day is the local day FrogEye filed its first document
    (22:30 UTC is the next day in Jerusalem)."""
    from datetime import UTC, datetime

    await make_document(created_at=datetime(2026, 9, 30, 22, 30, tzinfo=UTC))
    await make_document(created_at=datetime(2026, 10, 5, 8, tzinfo=UTC))
    body = (await (await auth_client("pm")).get("/api/v1/intel-reference")).json()
    assert body["collected_since"] == "2026-10-01"


async def test_intel_reference_requires_auth(client, seeded):
    assert (await client.get("/api/v1/intel-reference")).status_code == 401
