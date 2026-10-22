"""Idempotent seed loader: YAML files -> reference tables."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analysis_schema import JFROG_PRODUCTS
from app.collectors.base import CollectorError
from app.collectors.jobs.collector import adapter_for
from app.core.security import hash_password
from app.models import (
    Capability,
    CapabilityCategory,
    Competitor,
    CompetitorProduct,
    Kit,
    Source,
    User,
)
from app.models.enums import PageKind, Relationship, Role

SEED_DIR = Path(__file__).parent

_GENERATED_LABELS = {"google_news": "Google News", "gdelt": "GDELT", "hn": "Hacker News"}
_GENERATED_TIERS = (0, 1, 2)
_PAGE_KINDS = {m.value for m in PageKind}

_COMPETITOR_FIELDS = [
    "name",
    "tier",
    "relationship",
    "is_self",
    "website",
    "ticker",
    "cik",
    "github_orgs",
    "aliases",
    "ambiguous_aliases",
    "negative_keywords",
    "disambiguation_hint",
    "description",  # one factual sentence per tier 1-2 competitor
]
_COMPETITOR_DEFAULTS: dict[str, Any] = {
    "website": None,
    "ticker": None,
    "cik": None,
    "github_orgs": [],
    "aliases": [],
    "ambiguous_aliases": [],
    "negative_keywords": [],
    "disambiguation_hint": None,
    "description": None,
}
_PRODUCT_FIELDS = ["category", "url", "jfrog_products"]


class SeedError(Exception):
    """Raised when the seed data is inconsistent (e.g. unknown competitor slug)."""


@dataclass
class SourceSpec:
    kind: str
    name: str
    url: str
    competitor_slug: str | None
    reliability: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class SeedData:
    competitors: list[dict]
    categories: list[dict]
    capabilities: list[dict]
    kits: list[dict]
    users: list[dict]
    sources: list[SourceSpec]
    products: list[dict] = field(default_factory=list)  # competitor_products.yaml rows


@dataclass
class SeedReport:
    competitors: int = 0
    categories: int = 0
    capabilities: int = 0
    kits: int = 0
    sources: int = 0
    users: int = 0
    products: int = 0


@dataclass
class Keywords:
    context_terms: list[str]
    industry: list[str]


def _yaml(seed_dir: Path, name: str) -> Any:
    return yaml.safe_load((seed_dir / name).read_text(encoding="utf-8"))


def load_keywords(seed_dir: Path = SEED_DIR) -> Keywords:
    data = _yaml(seed_dir, "keywords.yaml")
    return Keywords(context_terms=list(data["context_terms"]), industry=list(data["industry"]))


def _expand_sources(raw: dict, competitors: list[dict]) -> list[SourceSpec]:
    """sources.yaml -> source specs.

    Nothing tied to the `self` competitor (JFrog) produces news, so its RSS feeds,
    GitHub releases and generated search sources are skipped. Its `github_repo_stats` stays:
    that writes metrics, not documents. (EDGAR rows are seeded as listed; SEC is disabled.)
    """
    by_slug = {c["slug"]: c for c in competitors}

    def check(slug: str | None, where: str) -> str | None:
        if slug is not None and slug not in by_slug:
            raise SeedError(f"unknown competitor slug {slug!r} in sources.yaml ({where})")
        return slug

    def is_self(slug: str | None) -> bool:
        return slug is not None and bool(by_slug[slug]["is_self"])

    specs: list[SourceSpec] = []
    for item in raw.get("rss", []):
        slug = check(item.get("competitor"), item["name"])
        if is_self(slug):
            continue
        specs.append(
            SourceSpec(
                kind="rss",
                name=item["name"],
                url=item["url"],
                competitor_slug=slug,
                reliability="A",
                enabled=item.get("enabled", True),
                config={"auto_pass": True, **item.get("config", {})},
            )
        )
    for item in raw.get("industry_rss", []):
        specs.append(
            SourceSpec(
                kind="rss",
                name=item["name"],
                url=item["url"],
                competitor_slug=None,
                reliability="B",
                enabled=item.get("enabled", True),
                config=dict(item.get("config", {})),
            )
        )
    for item in raw.get("github_repos", []):
        repo, slug = item["repo"], check(item["competitor"], item["repo"])
        if not is_self(slug):
            specs.append(
                SourceSpec(
                    kind="github_releases",
                    name=f"{repo} releases",
                    url=f"https://api.github.com/repos/{repo}/releases",
                    competitor_slug=slug,
                    reliability="A",
                    config={"auto_pass": True, "repo": repo},
                )
            )
        specs.append(
            SourceSpec(
                kind="github_repo_stats",
                name=f"{repo} · GitHub stats",
                url=f"https://api.github.com/repos/{repo}",
                competitor_slug=slug,
                reliability="A",
                config={"repo": repo},
            )
        )
    for item in raw.get("edgar", []):
        slug = check(item["competitor"], "edgar")
        specs.append(
            SourceSpec(
                kind="sec_edgar",
                name=f"{by_slug[slug]['name']} · SEC filings",
                url=f"https://data.sec.gov/submissions/CIK{item['cik']}.json",
                competitor_slug=slug,
                reliability="A",
                enabled=item.get("enabled", True),
                config={"cik": item["cik"]},
            )
        )
    for item in raw.get("job_boards") or []:
        # a pinned public job board; app.cli.discover_job_boards finds the rest (not self)
        slug = check(item["competitor"], "job_boards")
        if is_self(slug):
            continue
        ats, token = item["ats"], item["token"]
        try:
            adapter = adapter_for(ats)
        except CollectorError:
            raise SeedError(f"unknown ATS {ats!r} in sources.yaml (job_boards)") from None
        specs.append(
            SourceSpec(
                kind="job_board",
                name=f"{by_slug[slug]['name']} · {adapter.label} jobs",
                url=adapter.url(token),
                competitor_slug=slug,
                reliability="A",
                enabled=item.get("enabled", True),
                config={"ats": ats, "token": token},
            )
        )
    for item in raw.get("pages") or []:
        # a watched company page; never the self competitor's
        slug = check(item["competitor"], "pages")
        page = item["page"]
        if page not in _PAGE_KINDS:
            raise SeedError(f"unknown page kind {page!r} in sources.yaml (pages)")
        if is_self(slug):
            continue
        specs.append(
            SourceSpec(
                kind="page_watch",
                name=f"{by_slug[slug]['name']} · {page} page",
                url=item["url"],
                competitor_slug=slug,
                reliability="A",
                enabled=item.get("enabled", True),
                config={"page": page, "url": item["url"], "fetch_fulltext": False},
            )
        )
    for kind, cfg in raw.get("generated", {}).items():
        if kind not in _GENERATED_LABELS:
            raise SeedError(f"unknown generated source kind {kind!r}")
        for c in competitors:
            if c["tier"] in _GENERATED_TIERS and not c["is_self"]:
                specs.append(
                    SourceSpec(
                        kind=kind,
                        name=f"{c['name']} · {_GENERATED_LABELS[kind]}",
                        url=f"generated://{kind}/{c['slug']}",
                        competitor_slug=c["slug"],
                        reliability=cfg["reliability"],
                        # search hits only mention the competitor: never auto-pass
                        config={"auto_pass": False, **cfg.get("config", {})},
                    )
                )
    keys = [(s.kind, s.url) for s in specs]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise SeedError(f"duplicate sources in sources.yaml: {sorted(dupes)}")
    return specs


_PRODUCT_KEYS = ("name", "category", "url", "jfrog_products")
_PRODUCTS_FILE = "competitor_products.yaml"


def _expand_products(raw: Any, competitors: list[dict]) -> list[dict]:
    """competitor_products.yaml -> product rows. Each product names at least one JFrog
    counterpart from JFROG_PRODUCTS and a public https source URL; the self
    competitor never has products. Any malformed entry is a SeedError, never a bare
    KeyError."""
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise SeedError(f"{_PRODUCTS_FILE} must be a mapping of competitor slug to products")
    by_slug = {c["slug"]: c for c in competitors}
    rows: list[dict] = []
    for slug, items in raw.items():
        comp = by_slug.get(slug)
        if comp is None:
            raise SeedError(f"unknown competitor slug {slug!r} in {_PRODUCTS_FILE}")
        if comp["is_self"]:
            raise SeedError(f"{_PRODUCTS_FILE} lists the self competitor {slug!r}")
        if not isinstance(items, list):
            raise SeedError(f"{_PRODUCTS_FILE}: {slug!r} must map to a list of products")
        names: set[str] = set()
        for n, item in enumerate(items):
            if not isinstance(item, dict):
                raise SeedError(f"{_PRODUCTS_FILE}: product {n} of {slug!r} must be a mapping")
            missing = [k for k in _PRODUCT_KEYS if item.get(k) in (None, "")]
            if missing:
                raise SeedError(
                    f"{_PRODUCTS_FILE}: product {n} of {slug!r} lacks {', '.join(missing)}"
                )
            where = f"{slug}/{item['name']}"
            if item["name"] in names:
                raise SeedError(f"duplicate product {where!r} in {_PRODUCTS_FILE}")
            names.add(item["name"])
            jfrog = list(item["jfrog_products"])
            if not jfrog:
                raise SeedError(f"product {where!r} names no JFrog counterpart")
            unknown = [p for p in jfrog if p not in JFROG_PRODUCTS]
            if unknown:
                raise SeedError(f"unknown JFrog product(s) {unknown} for {where!r}")
            url = item["url"]
            parts = urlsplit(url) if isinstance(url, str) else None
            if parts is None or parts.scheme != "https" or not parts.netloc:
                raise SeedError(f"product {where!r} needs a public https source URL")
            rows.append(
                {
                    "competitor_slug": slug,
                    "name": item["name"],
                    "category": item["category"],
                    "url": url,
                    "jfrog_products": jfrog,
                }
            )
    return rows


def read_seed(seed_dir: Path = SEED_DIR) -> SeedData:
    """Parse and validate the seed YAML. Pure: no DB access."""
    competitors = []
    for c in _yaml(seed_dir, "competitors.yaml"):
        rel = Relationship(c["relationship"])
        competitors.append({**c, "relationship": rel.value, "is_self": rel is Relationship.self})

    categories, capabilities = [], []
    for order, cat in enumerate(_yaml(seed_dir, "capabilities.yaml")):
        categories.append({"slug": cat["slug"], "name": cat["name"], "order": order})
        for cap in cat["capabilities"]:
            capabilities.append({**cap, "category_slug": cat["slug"]})

    kits = _yaml(seed_dir, "kits.yaml")
    users_raw = _yaml(seed_dir, "users.yaml")
    users = [
        {**u, "role": Role(u["role"]).value, "password": users_raw["password"]}
        for u in users_raw["users"]
    ]
    sources = _expand_sources(_yaml(seed_dir, "sources.yaml"), competitors)
    products = _expand_products(_yaml(seed_dir, _PRODUCTS_FILE), competitors)
    return SeedData(competitors, categories, capabilities, kits, users, sources, products)


async def _upsert(
    session: AsyncSession, model, rows: list[dict], conflict: list[str], update: list[str]
) -> dict[tuple, int]:
    """Bulk upsert; returns {conflict-key-tuple: id}. Only `update` columns are overwritten."""
    stmt = pg_insert(model).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=conflict, set_={c: stmt.excluded[c] for c in update}
    )
    cols = [getattr(model, c) for c in conflict]
    result = await session.execute(stmt.returning(model.id, *cols))
    return {tuple(r[1:]): r[0] for r in result}


async def load_seed(session: AsyncSession, seed_dir: Path = SEED_DIR) -> SeedReport:
    data = read_seed(seed_dir)

    comp_rows = [
        {
            "slug": c["slug"],
            **_COMPETITOR_DEFAULTS,
            **{k: v for k, v in c.items() if k in _COMPETITOR_FIELDS},
        }
        for c in data.competitors
    ]
    comp_ids = {
        k[0]: v
        for k, v in (
            await _upsert(session, Competitor, comp_rows, ["slug"], _COMPETITOR_FIELDS)
        ).items()
    }

    cat_ids = {
        k[0]: v
        for k, v in (
            await _upsert(session, CapabilityCategory, data.categories, ["slug"], ["name", "order"])
        ).items()
    }
    cap_rows = [
        {
            "slug": c["slug"],
            "name": c["name"],
            "description": c.get("description"),
            "category_id": cat_ids[c["category_slug"]],
        }
        for c in data.capabilities
    ]
    await _upsert(session, Capability, cap_rows, ["slug"], ["name", "description", "category_id"])

    kit_fields = ["title", "question", "priority", "owner_roles", "keywords"]
    await _upsert(session, Kit, data.kits, ["slug"], kit_fields)

    src_rows = [
        {
            "kind": s.kind,
            "url": s.url,
            "name": s.name,
            "competitor_id": comp_ids[s.competitor_slug] if s.competitor_slug else None,
            "reliability": s.reliability,
            "enabled": s.enabled,
            "config": s.config,
        }
        for s in data.sources
    ]
    # enabled (set on insert only), etag / last_modified / last_fetched_at / health are not updated
    await _upsert(
        session,
        Source,
        src_rows,
        ["kind", "url"],
        ["name", "competitor_id", "reliability", "config"],
    )

    # The seed owns competitor_products: rows are upserted on (competitor, name) and a product
    # dropped from the YAML is deleted (nothing refers to these ids; briefs quote names).
    prod_rows = [
        {
            "competitor_id": comp_ids[p["competitor_slug"]],
            "name": p["name"],
            **{k: p[k] for k in _PRODUCT_FIELDS},
        }
        for p in data.products
    ]
    kept = (
        list(
            (
                await _upsert(
                    session,
                    CompetitorProduct,
                    prod_rows,
                    ["competitor_id", "name"],
                    _PRODUCT_FIELDS,
                )
            ).values()
        )
        if prod_rows
        else []
    )
    await session.execute(delete(CompetitorProduct).where(CompetitorProduct.id.not_in(kept)))

    emails = [u["email"] for u in data.users]
    existing = {
        u.email: u for u in await session.scalars(select(User).where(User.email.in_(emails)))
    }
    for u in data.users:
        row = existing.get(u["email"])
        if row is None:
            session.add(
                User(
                    email=u["email"],
                    name=u["name"],
                    role=u["role"],
                    password_hash=hash_password(u["password"]),
                )
            )
        else:
            row.name, row.role = u["name"], u["role"]
    await session.flush()

    return SeedReport(
        competitors=len(data.competitors),
        categories=len(data.categories),
        capabilities=len(data.capabilities),
        kits=len(data.kits),
        sources=len(data.sources),
        users=len(data.users),
        products=len(data.products),
    )
