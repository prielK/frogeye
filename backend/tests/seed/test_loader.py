import re
import shutil
from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy import func, select

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.models import Competitor, CompetitorProduct, Kit, Source, User
from app.seed.loader import SEED_DIR, SeedError, load_keywords, load_seed, read_seed


async def test_seed_counts(session):
    rep = await load_seed(session)
    assert (rep.competitors, rep.categories, rep.capabilities, rep.kits, rep.users) == (
        24,
        8,
        37,
        8,
        5,
    )
    # 26 rss + 7 industry + 9 releases + 10 repo stats + 2 edgar + 17*3 generated
    # (no JFrog Blog, jfrog-cli releases or generated sources for the self competitor)
    # + 33 watched pages
    assert rep.sources == 138
    assert rep.products == 45  # competitor_products.yaml, tier 1-2 only


async def test_seed_idempotent(session):
    await load_seed(session)
    await load_seed(session)
    assert (await session.scalar(select(func.count()).select_from(Competitor))) == 24
    assert (await session.scalar(select(func.count()).select_from(Source))) == 138
    assert (await session.scalar(select(func.count()).select_from(User))) == 5
    assert (await session.scalar(select(func.count()).select_from(CompetitorProduct))) == 45


async def test_competitor_descriptions(session):
    """One factual sentence per tier 1-2 competitor, with no figures."""
    await load_seed(session)
    comps = (await session.scalars(select(Competitor))).all()
    described = {c.slug for c in comps if c.description}
    assert described == {c.slug for c in comps if c.tier in (1, 2)}
    for c in comps:
        if c.description:
            text = c.description
            assert text.endswith(".") and ". " not in text, text  # one sentence
            assert not re.search(r"\d", text), text  # no market share, revenue or other figures
            assert c.name.split()[0] in text, text


async def test_competitor_products_seed(session):
    """Each tier 1-2 competitor's main products, each with a category, a public
    source URL and its JFrog counterparts; never the self competitor."""
    rep = await load_seed(session)
    rows = (
        await session.execute(
            select(CompetitorProduct, Competitor).join(
                Competitor, Competitor.id == CompetitorProduct.competitor_id
            )
        )
    ).all()
    assert len(rows) == rep.products == 45
    seen: dict[str, list[str]] = {}
    for product, comp in rows:
        assert not comp.is_self and comp.tier in (1, 2)
        assert product.jfrog_products, product.name
        assert set(product.jfrog_products) <= set(JFROG_PRODUCTS), product.jfrog_products
        assert product.url and product.url.startswith("https://"), product.name
        assert product.category and product.name
        assert not re.search(r"%|\$", product.name + product.category)  # no figures
        seen.setdefault(comp.slug, []).append(product.name)
    tier_1_2 = (
        await session.scalars(select(Competitor.slug).where(Competitor.tier.in_((1, 2))))
    ).all()
    assert set(seen) == set(tier_1_2)  # every tier 1-2 competitor has at least one product
    assert seen["sonatype"][0] == "Nexus Repository"  # YAML order is kept

    by_name = {product.name: product for product, _ in rows}
    # hardened images replace both the image source and its vetting
    for name in ("Chainguard Containers", "Docker Hardened Images"):
        assert by_name[name].jfrog_products == ["Curation", "Container Registry"], name
    # named as the vendor's page names it, and a product page, not a home page
    assert "Cloudsmith dependency firewall" not in by_name
    assert by_name["Cloudsmith software supply chain security"].jfrog_products == ["Curation"]
    endor = by_name["Endor Labs Package Firewall"].url
    assert endor == "https://docs.endorlabs.com/integrations/package-firewall"
    for product in by_name.values():
        assert product.url.rstrip("/").count("/") >= 3 or product.url == "https://socket.dev/"


async def test_competitor_products_resync_and_validation(session, tmp_path):
    seed = tmp_path / "seed"
    shutil.copytree(SEED_DIR, seed)
    path = seed / "competitor_products.yaml"
    shipped = yaml.safe_load(path.read_text(encoding="utf-8"))
    await load_seed(session)

    # the seed owns the table: a product dropped from the YAML is removed on the next load,
    # and a changed one is updated in place
    changed = {**shipped, "aqua": [p for p in shipped["aqua"] if p["name"] != "Trivy"]}
    changed["socket"] = [{**shipped["socket"][0], "category": "Changed"}, *shipped["socket"][1:]]
    path.write_text(yaml.safe_dump(changed, sort_keys=False), encoding="utf-8")
    rep = await load_seed(session, seed)
    assert rep.products == 44
    names = (
        await session.scalars(
            select(CompetitorProduct.name)
            .join(Competitor, Competitor.id == CompetitorProduct.competitor_id)
            .where(Competitor.slug == "aqua")
        )
    ).all()
    assert "Trivy" not in names and names
    first_socket = shipped["socket"][0]["name"]
    category = await session.scalar(
        select(CompetitorProduct.category).where(CompetitorProduct.name == first_socket)
    )
    assert category == "Changed"
    assert (await session.scalar(select(func.count()).select_from(CompetitorProduct))) == 44

    good = {
        "name": "X",
        "category": "C",
        "url": "https://example.com/x",
        "jfrog_products": ["Xray"],
    }
    for bad, match in [
        ({"nope": [good]}, "nope"),  # unknown competitor
        ({"jfrog": [good]}, "self"),
        ({"aqua": [{**good, "jfrog_products": ["Nexus"]}]}, "Nexus"),  # not a JFrog product
        ({"aqua": [{**good, "jfrog_products": []}]}, "counterpart"),
        ({"aqua": [{**good, "url": "http://example.com/x"}]}, "https"),
        ({"aqua": [{**good, "url": "https://"}]}, "https"),  # no host
        ({"aqua": [good, good]}, "duplicate"),
        # a missing key is a SeedError naming it, never a bare KeyError
        ({"aqua": [{k: v for k, v in good.items() if k != "category"}]}, "category"),
        ({"aqua": [{k: v for k, v in good.items() if k != "name"}]}, "name"),
        ({"aqua": [{k: v for k, v in good.items() if k != "url"}]}, "url"),
        ({"aqua": [{k: v for k, v in good.items() if k != "jfrog_products"}]}, "jfrog_products"),
        ({"aqua": ["Trivy"]}, "mapping"),
        ({"aqua": {"name": "Trivy"}}, "list"),
        (["aqua"], "mapping"),
    ]:
        path.write_text(yaml.safe_dump(bad, sort_keys=False), encoding="utf-8")
        with pytest.raises(SeedError, match=match):
            read_seed(seed)


async def test_jfrog_is_self(session):
    await load_seed(session)
    jf = await session.scalar(select(Competitor).where(Competitor.slug == "jfrog"))
    assert jf.is_self and jf.tier == 0 and jf.relationship == "self"


async def test_generated_google_news_query_config(session):
    await load_seed(session)
    s = await session.scalar(
        select(Source).where(Source.kind == "google_news", Source.name.contains("Socket"))
    )
    assert s.reliability == "C" and s.config["days"] == 2
    assert s.url == "generated://google_news/socket"
    assert s.name == "Socket · Google News"


async def test_repo_and_edgar_sources(session):
    await load_seed(session)
    stats = await session.scalar(
        select(Source).where(Source.url == "https://api.github.com/repos/aquasecurity/trivy")
    )
    assert stats.kind == "github_repo_stats" and stats.reliability == "A"
    assert stats.name == "aquasecurity/trivy · GitHub stats"
    rel = await session.scalar(
        select(Source).where(
            Source.url == "https://api.github.com/repos/aquasecurity/trivy/releases"
        )
    )
    assert rel.kind == "github_releases" and rel.name == "aquasecurity/trivy releases"
    assert rel.enabled and rel.reliability == "A"
    edgar = await session.scalar(
        select(Source).where(Source.kind == "sec_edgar", Source.name.contains("JFrog"))
    )
    assert edgar.url == "https://data.sec.gov/submissions/CIK0001800667.json"
    assert edgar.config["cik"] == "0001800667"
    assert edgar.enabled is False  # SEC needs a declared contact; disabled by user choice


async def test_seed_skips_self_news_sources(session):
    # every source tied to JFrog itself stops producing news. github_repo_stats stays:
    # it writes metrics, not documents. EDGAR stays seeded but disabled (no SEC contact).
    await load_seed(session)
    jf = await session.scalar(select(Competitor).where(Competitor.is_self))
    own = (await session.scalars(select(Source).where(Source.competitor_id == jf.id))).all()
    assert not [s.name for s in own if s.enabled and s.kind != "github_repo_stats"]
    assert {(s.kind, s.enabled) for s in own} == {
        ("github_repo_stats", True),
        ("sec_edgar", False),
    }
    stats = next(s for s in own if s.kind == "github_repo_stats")
    assert stats.url == "https://api.github.com/repos/jfrog/jfrog-cli"
    generated = await session.scalars(
        select(Source.url).where(Source.url.like(f"generated://%/{jf.slug}"))
    )
    assert generated.all() == []
    # the pure reader agrees: nothing self-tied but the stats and the disabled filing feed
    specs = [s for s in read_seed().sources if s.competitor_slug == "jfrog"]
    assert sorted((s.kind, s.enabled) for s in specs) == [
        ("github_repo_stats", True),
        ("sec_edgar", False),
    ]


async def test_users_hashed(session):
    await load_seed(session)
    u = await session.scalar(select(User).where(User.email == "exec@frogeye.local"))
    assert u.role == "exec" and u.password_hash.startswith("$2")


async def test_reseed_preserves_health_fields(session):
    await load_seed(session)
    s = await session.scalar(select(Source).where(Source.name == "Sonatype Blog"))
    s.etag = "abc"
    s.consecutive_failures = 3
    s.last_fetched_at = datetime(2026, 1, 1, tzinfo=UTC)
    s.enabled = False
    await session.flush()
    await load_seed(session)
    await session.refresh(s)
    assert s.etag == "abc" and s.consecutive_failures == 3
    assert s.last_fetched_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert s.enabled is False


async def test_kit_keywords_are_specific(session):
    await load_seed(session)
    kits = (await session.scalars(select(Kit))).all()
    assert len(kits) == 8
    for k in kits:
        assert 4 <= len(k.keywords) <= 8
        for kw in k.keywords:
            assert kw.lower() not in {"ai", "pricing", "security", "packages", "package"}


def test_load_keywords():
    kw = load_keywords(SEED_DIR)
    assert "devops" in kw.context_terms and "malicious package" in kw.industry


async def test_unknown_competitor_slug_raises(session, tmp_path):
    shutil.copytree(SEED_DIR, tmp_path / "seed")
    p = tmp_path / "seed" / "sources.yaml"
    p.write_text(
        p.read_text(encoding="utf-8").replace("competitor: cloudsmith", "competitor: nope"),
        encoding="utf-8",
    )
    with pytest.raises(SeedError, match="nope"):
        await load_seed(session, tmp_path / "seed")
    with pytest.raises(SeedError, match="nope"):
        read_seed(tmp_path / "seed")


def test_job_board_pins(tmp_path):
    # sources.yaml may pin a competitor's board; the seed ships none (discovery finds them)
    assert [s for s in read_seed().sources if s.kind == "job_board"] == []
    shutil.copytree(SEED_DIR, tmp_path / "seed")
    p = tmp_path / "seed" / "sources.yaml"
    pins = (
        "\njob_boards:\n"
        "  - {competitor: snyk, ats: greenhouse, token: snyk}\n"
        "  - {competitor: docker, ats: ashby, token: docker, enabled: false}\n"
        "  - {competitor: jfrog, ats: lever, token: jfrog}\n"  # self: skipped
    )
    text = p.read_text(encoding="utf-8").replace("\njob_boards: []\n", "\n")
    p.write_text(text + pins, encoding="utf-8")
    boards = [s for s in read_seed(tmp_path / "seed").sources if s.kind == "job_board"]
    assert [(s.competitor_slug, s.url, s.enabled) for s in boards] == [
        ("snyk", "https://boards-api.greenhouse.io/v1/boards/snyk/jobs?content=true", True),
        (
            "docker",
            "https://api.ashbyhq.com/posting-api/job-board/docker?includeCompensation=true",
            False,
        ),
    ]
    assert boards[0].config == {"ats": "greenhouse", "token": "snyk"}
    assert boards[0].name == "Snyk · Greenhouse jobs" and boards[0].reliability == "A"
    p.write_text(
        text + "\njob_boards:\n  - {competitor: snyk, ats: myspace, token: x}\n", encoding="utf-8"
    )
    with pytest.raises(SeedError, match="myspace"):
        read_seed(tmp_path / "seed")


async def test_generated_search_sources_never_auto_pass(session):
    # Google News / GDELT / HN hits only mention a competitor; the prefilter must see it.
    await load_seed(session)
    generated = (
        await session.scalars(select(Source).where(Source.kind.in_(["google_news", "gdelt", "hn"])))
    ).all()
    assert len(generated) == 17 * 3  # tiers 0-2 minus the self competitor
    assert all(s.config.get("auto_pass") is False for s in generated)
    gdelt = next(s for s in generated if s.kind == "gdelt")
    assert gdelt.config["timespan"] == "1d"  # YAML config is kept alongside the default
    # generated sources (GDELT included) are enabled for every tier
    assert all(s.enabled for s in generated)
    own_feed = await session.scalar(select(Source).where(Source.name == "Sonatype Blog"))
    assert own_feed.config["auto_pass"] is True


async def test_seeded_watched_pages(session):
    """Tier 1-2 competitors only, never JFrog, giants watch pricing only."""
    await load_seed(session)
    rows = (
        await session.execute(
            select(Source, Competitor)
            .join(Competitor, Competitor.id == Source.competitor_id)
            .where(Source.kind == "page_watch")
        )
    ).all()
    assert len(rows) == 33
    for src, comp in rows:
        assert not comp.is_self and comp.tier in (1, 2)
        assert src.config["page"] in {"pricing", "about", "careers"}
        assert src.config["url"] == src.url and src.url.startswith("https://")
        assert src.config["fetch_fulltext"] is False and src.enabled
    pages = {}
    for src, comp in rows:
        pages.setdefault(comp.slug, set()).add(src.config["page"])
    for giant in ("aws", "azure", "google-cloud", "github"):
        assert pages[giant] == {"pricing"}
    assert "jfrog" not in pages


def test_pages_for_self_are_skipped_and_unknowns_rejected(tmp_path):
    seed = tmp_path / "seed"
    shutil.copytree(SEED_DIR, seed)
    src = seed / "sources.yaml"
    src.write_text(
        src.read_text(encoding="utf-8")
        + '  - {competitor: jfrog, page: pricing, url: "https://jfrog.com/pricing/"}\n',
        encoding="utf-8",
    )
    data = read_seed(seed)
    watched = [s for s in data.sources if s.kind == "page_watch"]
    assert len(watched) == 33 and all(s.competitor_slug != "jfrog" for s in watched)
    src.write_text(
        src.read_text(encoding="utf-8")
        + '  - {competitor: snyk, page: blog, url: "https://snyk.io/blog/"}\n',
        encoding="utf-8",
    )
    with pytest.raises(SeedError, match="page kind"):
        read_seed(seed)
