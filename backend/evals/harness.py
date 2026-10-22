"""The eval environment: a fresh schema in the throwaway test database, the seed, the
fixture corpus, and an AccountedLLM over the record/replay backend.

Only `TEST_DATABASE_URL` is used, and only when its database name says "test": the setup
drops and recreates the `public` schema, as tests/conftest.py does.
"""

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.agents.tools  # noqa: F401  (registers the built-in tools)
from app.agents.runtime.accounted import AccountedLLM, LLMError
from app.agents.runtime.context import RunCtx
from app.agents.runtime.openai_backend import OpenAIBackend
from app.agents.schemas import RunEnums, build_enums
from app.agents.tools import search_intel
from app.agents.tools.base import ToolDeps
from app.core.config import Settings, get_settings
from app.models import Competitor, Document, DocumentCompetitor, JfrogKbDoc, RawItem, Source
from app.models.enums import DocStatus, Reliability, SourceKind
from app.processing.prefilter import AliasMatcher
from app.rag.indexing import index_documents, index_kb_doc
from app.seed.loader import load_seed
from evals.recorder import CORPUS, Mode, RecordingBackend, suite_scope

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parent
DATASETS = ROOT / "datasets"
RECORDINGS = ROOT / "recordings"
RESULTS = ROOT / "results"
PROMPTS = ROOT / "prompts"
MANIFEST = RECORDINGS / "manifest.json"
# The day the datasets were exported. The agents' search tool counts its recency filter
# back from it, so a replay next year sees the same tool results as the recording did.
EVAL_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
# Record mode stops starting live calls this far below --max-usd, so calls already in
# flight finish under the ledger's hard cap (which is --max-usd itself).
BUDGET_HEADROOM_USD = 0.01


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("TEST_DATABASE_URL is not set: the evals run in the test database only")
    if "test" not in (make_url(url).database or ""):
        raise SystemExit("TEST_DATABASE_URL must name a test database (its schema is recreated)")
    return url


@dataclass
class Env:
    mode: Mode
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    llm: AccountedLLM
    backend: RecordingBackend
    enums: RunEnums
    deps: ToolDeps
    ctx: RunCtx
    unindexed: list[str] = field(default_factory=list)  # corpus refs not embedded


def load_manifest() -> dict[str, Any] | None:
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else None


def write_manifest(settings: Settings, *, live_usd: float, live_calls: int) -> None:
    """Record the models the recordings were made with, and log this record run's live
    spend (from its ledger) when it made any call."""
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    prior = load_manifest() or {}
    runs = list(prior.get("record_runs", []))
    recorded_at = prior.get("recorded_at")
    if live_calls or recorded_at is None:
        recorded_at = datetime.now(UTC).isoformat(timespec="seconds")
        runs.append({"at": recorded_at, "live_usd": round(live_usd, 6), "live_calls": live_calls})
    data = {"recorded_at": recorded_at, "models": _models(settings), "record_runs": runs}
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def _models(settings: Settings) -> dict[str, Any]:
    return {
        "model_fast": settings.model_fast,
        "model_smart": settings.model_smart,
        "reasoning_effort_fast": settings.reasoning_effort_fast,
        "reasoning_effort_smart": settings.reasoning_effort_smart,
        "embedding_model": settings.embedding_model,
        "embedding_dims": settings.embedding_dims,
    }


def eval_settings(mode: Mode, max_usd: float) -> Settings:
    """The app's settings with the eval's own budget. A replay uses the models the
    recordings were made with (whatever this machine's .env says); a record run uses the
    configured ones. The search tool's rerank default is pinned off (the app default)."""
    update: dict[str, Any] = {
        "llm_daily_budget_usd": max_usd,
        "llm_total_budget_usd": max_usd,
        "rag_rerank_default": False,
    }
    manifest = load_manifest()
    if mode == "replay" and manifest is not None:
        update |= manifest["models"]
    return get_settings().model_copy(update=update)


def _alembic_upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


async def _load_corpus(session: AsyncSession) -> None:
    """The fixture documents (with their ids, so prompts name the same documents as the
    recordings) and JFrog KB pages. Each document's prefilter terms are computed by the
    app's own matcher on the excerpt; its competitor links are the corpus labels."""
    sources = {s.name: s for s in (await session.scalars(select(Source))).all()}
    comp_ids = dict((await session.execute(select(Competitor.slug, Competitor.id))).all())
    matcher = await AliasMatcher.from_db(session)
    docs = read_jsonl(DATASETS / "corpus" / "documents.jsonl")
    for n, row in enumerate(docs, 1):
        source = sources.get(row["source"])
        if source is None:  # a source the seed no longer has: same name, no competitor
            source = Source(
                kind=SourceKind.rss,
                name=row["source"],
                url=f"https://eval.invalid/source/{n}",
                reliability=Reliability.C,
            )
            session.add(source)
            await session.flush()
            sources[row["source"]] = source
        raw = RawItem(
            source_id=source.id,
            url=row["url"],
            external_id=f"eval-{row['id']}",
            content_hash=_sha(row["text"]),
            payload={},
        )
        session.add(raw)
        await session.flush()
        match = matcher.match(title=row["title"], text=row["text"], source=source)
        scope = str(match.scope_hint) if match.passed else None
        session.add(
            Document(
                id=row["id"],
                raw_item_id=raw.id,
                source_id=source.id,
                canonical_url=row["url"],
                url_hash=_sha(row["url"]),
                title=row["title"],
                published_at=_at(row["published_at"]),
                clean_text=row["text"],
                content_hash=_sha(row["text"]),
                status=DocStatus.new,
                scope_hint=scope,
                prefilter={"matched_terms": match.matched_terms, "scope_hint": scope},
            )
        )
        await session.flush()
        for slug in row["competitors"]:
            session.add(DocumentCompetitor(document_id=row["id"], competitor_id=comp_ids[slug]))
    for row in read_jsonl(DATASETS / "corpus" / "jfrog_kb.jsonl"):
        session.add(
            JfrogKbDoc(
                id=row["id"],
                url=row["url"],
                title=row["title"],
                text=row["text"],
                content_hash=_sha(row["text"]),
                fetched_at=_at(row["fetched_at"]),
            )
        )
    await session.flush()
    for table in ("documents", "jfrog_kb_docs"):  # explicit ids: move the sequences past them
        await session.execute(
            text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), max(id)) FROM {table}")
        )


async def _index_corpus(env: Env) -> None:
    """Embed every corpus item, one at a time and in id order, so the vector index is built
    the same way on every run. An item whose embedding is not recorded is listed in
    `env.unindexed` (the retrieval suite then reports itself stale)."""
    async with env.session_factory() as session:
        doc_ids = list(await session.scalars(select(Document.id).order_by(Document.id)))
        kb_ids = list(await session.scalars(select(JfrogKbDoc.id).order_by(JfrogKbDoc.id)))
    with suite_scope(CORPUS):
        for doc_id in doc_ids:
            try:
                await index_documents(env.session_factory, env.llm, [doc_id], ctx=env.ctx)
            except LLMError:
                env.unindexed.append(f"doc:{doc_id}")
        for kb_id in kb_ids:
            try:
                await index_kb_doc(env.session_factory, env.llm, kb_id, ctx=env.ctx)
            except LLMError:
                env.unindexed.append(f"kb:{kb_id}")


@asynccontextmanager
async def open_env(mode: Mode, max_usd: float) -> AsyncIterator[Env]:
    url = test_database_url()
    settings = eval_settings(mode, max_usd)
    live = None
    if mode == "record":
        if not settings.openai_api_key:
            raise SystemExit("record mode needs OPENAI_API_KEY (the eval service loads .env)")
        live = OpenAIBackend(settings)
    backend = RecordingBackend(
        RECORDINGS, mode, live, budget_usd=max(max_usd - BUDGET_HEADROOM_USD, 0.0)
    )

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await asyncio.to_thread(_alembic_upgrade, url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await load_seed(session)
        await session.flush()
        await _load_corpus(session)
        await session.commit()
        enums = await build_enums(session)

    llm = AccountedLLM(backend, session_factory, settings)
    ctx = RunCtx(agent_run_id=None, node="eval")
    env = Env(
        mode=mode,
        settings=settings,
        session_factory=session_factory,
        llm=llm,
        backend=backend,
        enums=enums,
        deps=ToolDeps(session_factory, llm, settings, ctx),
        ctx=ctx,
    )
    clock = search_intel.utcnow
    search_intel.utcnow = lambda: EVAL_NOW
    try:
        await _index_corpus(env)
        yield env
    finally:
        search_intel.utcnow = clock
        await engine.dispose()
