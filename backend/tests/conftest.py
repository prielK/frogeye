import asyncio
import itertools
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends
from httpx import ASGITransport
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import require_roles
from app.core.db import get_session
from app.main import app
from app.models import Document, RawItem, Source
from app.models.enums import Reliability, SourceKind
from app.seed.loader import load_seed


def _alembic_upgrade(url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ["TEST_DATABASE_URL"]
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    # alembic's async env calls asyncio.run(), so run it off the event loop thread
    await asyncio.to_thread(_alembic_upgrade, url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            yield conn
        finally:
            await trans.rollback()


@pytest.fixture
def session_factory(connection: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async def _override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    await load_seed(session)
    return session


_HARNESS_STATEMENTS = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")


@pytest.fixture
def sql_counter(connection: AsyncConnection) -> Iterator[list[str]]:
    """The SQL statements run on the test connection, for query budgets.
    The harness's own savepoints are left out: every API request opens its session in one
    (`client`), production does not. `.clear()` it right before the measured call."""
    seen: list[str] = []

    def count(conn, cursor, statement, *rest):
        if not statement.lstrip().upper().startswith(_HARNESS_STATEMENTS):
            seen.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", count)
    yield seen
    event.remove(connection.sync_connection, "before_cursor_execute", count)


_seq = itertools.count(1)

# The tests' pinned clock: the latest NOW a test module pins (tests/intel/test_profile.py,
# test_hiring.py, test_posting_tags.py; the API tests patch app.api.intel_out.utcnow to it).
PINNED_NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _wall_clock() -> datetime:
    return datetime.now(UTC)


def filed_at() -> datetime:
    """A factory document's default `created_at`: the wall clock, never earlier than
    just after PINNED_NOW. Under a pinned clock it is never "already filed", so it cannot move
    the collected-since day whatever day the suite runs; wall-clock tests keep a recent
    filing time. A test that needs a collection day passes `created_at` itself."""
    return max(_wall_clock(), PINNED_NOW + timedelta(seconds=1))


@pytest.fixture
def make_source(session: AsyncSession):
    async def _make(**overrides) -> Source:
        n = next(_seq)
        data = {
            "kind": SourceKind.rss,
            "name": f"source-{n}",
            "url": f"https://example.com/feed-{n}.xml",
            "reliability": Reliability.B,
        }
        data.update(overrides)
        src = Source(**data)
        session.add(src)
        await session.flush()
        return src

    return _make


@pytest.fixture
def make_document(session: AsyncSession, make_source):
    async def _make(**overrides) -> Document:
        n = next(_seq)
        source_id = overrides.pop("source_id", None) or (await make_source()).id
        raw = RawItem(
            source_id=source_id,
            url=f"https://example.com/a-{n}",
            external_id=f"ext-{n}",
            content_hash=f"raw-hash-{n}",
            payload={},
        )
        session.add(raw)
        await session.flush()
        data = {
            "raw_item_id": raw.id,
            "source_id": source_id,
            "canonical_url": f"https://example.com/a-{n}",
            "url_hash": f"url-hash-{n}",
            "content_hash": f"content-hash-{n}",
            "created_at": filed_at(),
        }
        data.update(overrides)
        doc = Document(**data)
        session.add(doc)
        await session.flush()
        return doc

    return _make


PERSONA_EMAIL = {
    "analyst": "analyst@frogeye.local",
    "sales": "sales@frogeye.local",
    "pm": "pm@frogeye.local",
    "exec": "exec@frogeye.local",
    "pmm_devrel": "devrel@frogeye.local",
}


@pytest.fixture
async def auth_client(client: httpx.AsyncClient, seeded):
    """Factory: returns a client logged in as the seeded persona for a role.

    Each client has its own cookie jar; all share the overridden DB session.
    """
    opened: list[httpx.AsyncClient] = []

    async def _make(role: str) -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        opened.append(c)
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": PERSONA_EMAIL[str(role)], "password": "frogeye-demo"},
        )
        assert r.status_code == 200, r.text
        return c

    try:
        yield _make
    finally:
        for c in opened:
            await c.aclose()


@pytest.fixture
def app_with_route():
    @app.get("/api/v1/_t", dependencies=[Depends(require_roles("analyst"))])
    async def _t() -> dict:
        return {"ok": True}

    route = app.router.routes[-1]
    try:
        yield
    finally:
        app.router.routes.remove(route)


@pytest.fixture
def settings():
    from app.core.config import get_settings

    return get_settings()


@pytest.fixture
def test_settings_override(settings):
    """Factory: a copy of the settings with some fields replaced (the cached one is untouched)."""

    def _make(**overrides):
        return settings.model_copy(update=overrides)

    return _make


@pytest.fixture
def fake_backend():
    """Factory: `fake_backend(script)` -> FakeBackend (see tests/agents/fakes.py)."""
    from tests.agents.fakes import FakeBackend

    def _make(script=None, **kw):
        return FakeBackend(script, **kw)

    return _make


@pytest.fixture
def accounted_llm(session_factory, settings, fake_backend):
    """Factory: an AccountedLLM over a FakeBackend, writing through the test session_factory.

    `accounted_llm(script, *, backend=None, settings=None, now=None)`; the backend is
    reachable as `llm.backend`.
    """
    from app.agents.runtime.accounted import AccountedLLM

    base_settings = settings

    def _make(script=None, *, backend=None, settings=None, now=None):
        kw = {"now": now} if now is not None else {}
        return AccountedLLM(
            backend or fake_backend(script),
            session_factory,
            settings or base_settings,
            **kw,
        )

    return _make


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


PUBLIC_TEST_IP = "93.184.216.34"


class FakeResolver:
    """Stands in for DNS: every test host resolves to a public address unless overridden."""

    def __init__(self) -> None:
        self.addresses: dict[str, list[str]] = {}
        self.calls: list[str] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        return self.addresses.get(host, [PUBLIC_TEST_IP])


@pytest.fixture
def fake_resolver() -> FakeResolver:
    return FakeResolver()


@pytest.fixture
def make_polite_client(settings, fake_time, fake_resolver):
    """Factory for PoliteClient with fake time and fake DNS (no real network lookups)."""
    from app.core.http import PoliteClient

    def _make(**kw):
        kw.setdefault("clock", fake_time.clock)
        kw.setdefault("sleep", fake_time.sleep)
        kw.setdefault("resolver", fake_resolver)
        return PoliteClient(settings, **kw)

    return _make


@pytest.fixture
async def polite_client(make_polite_client):
    async with make_polite_client() as c:
        yield c


@pytest.fixture
def a_day_passes(session_factory):
    """A job board or watched page is fetched at most once per local day. A test that
    takes several daily snapshots moves the sources' last attempt back a day between runs."""
    from datetime import timedelta

    from sqlalchemy import update

    async def _shift(*source_ids: int) -> None:
        async with session_factory() as s:
            await s.execute(
                update(Source)
                .where(Source.id.in_(source_ids))
                .values(last_attempt_at=Source.last_attempt_at - timedelta(days=1))
            )
            await s.commit()

    return _shift


@pytest.fixture
def freeze_now(monkeypatch):
    """Pin the collectors' notion of 'now' (used by the RSS future-date rule)."""
    from datetime import datetime

    from app.collectors import rss

    now = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(rss, "utcnow", lambda: now)
    return now


@pytest.fixture
def competitor_factory():
    from app.models import Competitor

    def _make(**fields) -> Competitor:
        n = next(_seq)
        data = {
            "slug": f"comp-{n}",
            "name": f"Comp{n}",
            "tier": 1,
            "relationship": "competitor",
            "aliases": [],
            "ambiguous_aliases": [],
            "negative_keywords": [],
            "github_orgs": [],
        }
        data.update(fields)
        return Competitor(**data)

    return _make


@pytest.fixture
def raw_item_factory(session: AsyncSession, make_source):
    async def _make(**fields) -> RawItem:
        n = next(_seq)
        source_id = fields.pop("source_id", None) or (await make_source()).id
        data = {
            "source_id": source_id,
            "url": f"https://example.com/raw-{n}",
            "external_id": f"raw-ext-{n}",
            "content_hash": f"raw-ch-{n}",
            "payload": {},
        }
        data.update(fields)
        item = RawItem(**data)
        session.add(item)
        await session.flush()
        return item

    return _make


@pytest.fixture
async def slug_of(seeded: AsyncSession):
    from app.models import Competitor

    rows = (await seeded.execute(select(Competitor.id, Competitor.slug))).all()
    by_id = {i: s for i, s in rows}
    return by_id.__getitem__


@pytest.fixture
async def source_by_key(seeded: AsyncSession, make_source):
    """Sync lookup: None -> generic industry RSS source (no competitor); else a seeded source."""
    names = {"aws-whats-new": "AWS What's New", "chainguard-blog": "Chainguard Unchained"}
    by_key = {
        key: (await seeded.execute(select(Source).where(Source.name == name))).scalar_one()
        for key, name in names.items()
    }
    generic = await make_source(name="Generic industry feed")

    def _get(key: str | None) -> Source:
        return generic if key is None else by_key[key]

    return _get


class FakeCollector:
    def __init__(self, by_name: dict):
        self.by_name = by_name
        self.seen: list[tuple[str, object]] = []

    async def fetch(self, source, since, http):
        self.seen.append((source.name, since))
        out = self.by_name[source.name]
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def fake_collectors(monkeypatch):
    from app.api.v1 import sources as sources_api
    from app.processing import ingest as ingest_mod

    def _install(by_name: dict) -> FakeCollector:
        fc = FakeCollector(by_name)
        monkeypatch.setattr(ingest_mod, "get_collector", lambda kind: fc)
        monkeypatch.setattr(sources_api, "get_collector", lambda kind: fc)
        return fc

    return _install


@pytest.fixture
async def docs_fixture(seeded: AsyncSession, make_document):
    """Sonatype: 3 dated + 1 undated + 1 duplicate; other competitors: 2 docs."""
    from datetime import datetime, timedelta

    from app.models import Competitor, DocumentCompetitor

    comps = {c.slug: c.id for c in (await seeded.execute(select(Competitor))).scalars().all()}
    src = (await seeded.execute(select(Source).where(Source.name == "Sonatype Blog"))).scalar_one()
    base = datetime(2026, 10, 10, tzinfo=UTC)

    async def doc(slug, **kw):
        d = await make_document(source_id=src.id, **kw)
        seeded.add(DocumentCompetitor(document_id=d.id, competitor_id=comps[slug]))
        await seeded.flush()
        return d

    out = {}
    out["s1"] = await doc(
        "sonatype",
        title="Sonatype oldest",
        published_at=base,
        status="relevant",
        scope_hint="competitor",
        clean_text=" " * 2 + "Hello" + chr(10) * 2 + " world  " + "x" * 400,
        prefilter={"matched_terms": ["nexus"], "scope_hint": "competitor"},
    )
    out["s2"] = await doc(
        "sonatype", title="Sonatype middle 100%_", published_at=base + timedelta(days=1)
    )
    out["s3"] = await doc(
        "sonatype", title="Sonatype newest", published_at=base + timedelta(days=2)
    )
    out["undated"] = await doc("sonatype", title="Sonatype undated", published_at=None)
    out["dup"] = await doc(
        "sonatype", title="Sonatype dup", published_at=base + timedelta(days=3), status="duplicate"
    )
    out["g1"] = await doc(
        "gitlab", title="GitLab thing", published_at=base + timedelta(days=4), scope_hint="industry"
    )
    out["j1"] = await doc(
        "jfrog", title="JFrog thing", published_at=base + timedelta(days=5), scope_hint="jfrog"
    )
    return out


@pytest.fixture
def make_signal(session: AsyncSession):
    """Factory: a flushed Signal with sensible defaults.

    Overrides are Signal columns plus optional links: `competitors` (slugs, ids or
    Competitor rows), `kits` (slugs, ids or Kit rows), `documents` (Document rows; the first
    is primary), `evidence` ([(Document, quote)] or [(Document, quote, verified)]), and
    `centroid` (a vector, or a string that is embedded with the fake embedder).
    """

    async def _make(**overrides):
        from app.models import Competitor, Kit
        from app.models.gold import Evidence, Signal, SignalCompetitor, SignalDocument, SignalKit
        from tests.agents.fakes import fake_embedding

        n = next(_seq)
        competitors = overrides.pop("competitors", [])
        kits = overrides.pop("kits", [])
        documents = overrides.pop("documents", [])
        evidence = overrides.pop("evidence", [])
        centroid = overrides.pop("centroid", None)
        if isinstance(centroid, str):
            centroid = fake_embedding(centroid, 512)
        data = {
            "title": f"Signal {n}",
            "scope": "competitor",
            "signal_type": "product_launch",
            "fact": f"Fact {n}",
            "impact": f"Impact {n}",
            "importance": 50,
            "status": "approved",
            "centroid": centroid,
        }
        data.update(overrides)
        if "analyzed_at" not in overrides and data["status"] != "pending":
            # Every non-pending signal has been through the analyst (newsroom selection depends on
            # it).
            seen = overrides.get("first_seen_at")
            now = datetime.now(UTC)
            data["analyzed_at"] = min(seen, now) if seen is not None else now
        sig = Signal(**data)
        session.add(sig)
        await session.flush()

        async def _ids(model, items):
            out = []
            for it in items:
                if isinstance(it, str):
                    out.append(
                        (
                            await session.execute(select(model.id).where(model.slug == it))
                        ).scalar_one()
                    )
                else:
                    out.append(it if isinstance(it, int) else it.id)
            return out

        for cid in await _ids(Competitor, competitors):
            session.add(SignalCompetitor(signal_id=sig.id, competitor_id=cid))
        for kid in await _ids(Kit, kits):
            session.add(SignalKit(signal_id=sig.id, kit_id=kid))
        for i, doc in enumerate(documents):
            session.add(SignalDocument(signal_id=sig.id, document_id=doc.id, is_primary=i == 0))
        for ev in evidence:
            doc, quote, *rest = ev
            session.add(
                Evidence(
                    signal_id=sig.id,
                    document_id=doc.id,
                    quote=quote,
                    verified=rest[0] if rest else True,
                )
            )
        await session.flush()
        return sig

    return _make
