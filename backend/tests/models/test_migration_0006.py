"""Data migration 0006: JFrog-self sources disabled, scope-jfrog signals suppressed."""

import asyncio
import importlib.util
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from app.models import Competitor, Document, DocumentCompetitor, Source
from app.models.gold import Signal, SignalCompetitor

MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / "0006_drop_self_news.py"
SELF_KINDS = ("rss", "google_news", "gdelt", "hn", "github_releases", "github_repo_stats")


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _step(name: str):
    """Run the migration's upgrade() or downgrade() on the test's own connection."""

    def run(sync_conn) -> None:
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        spec = importlib.util.spec_from_file_location("migration_0006", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert (module.revision, module.down_revision) == ("0006", "0005")
        with Operations.context(MigrationContext.configure(sync_conn)):
            getattr(module, name)()

    return run


async def test_migration_0006(connection, seeded, make_source, make_signal, make_document):
    session = seeded
    comps = {c.slug: c for c in (await session.execute(select(Competitor))).scalars()}
    jf, sonatype = comps["jfrog"], comps["sonatype"]
    # self-tied rows as a database seeded before JFrog-self sources were disabled holds them, all
    # enabled
    own = {
        kind: await make_source(
            kind=kind,
            url=f"https://self.test/{kind}",
            competitor_id=jf.id,
            reliability="A",
            enabled=True,
        )
        for kind in SELF_KINDS
    }
    rival = await make_source(competitor_id=sonatype.id, enabled=True)
    industry = await make_source(enabled=True)
    legacy = [
        await make_signal(scope="jfrog", status=st)
        for st in ("pending", "auto", "needs_review", "approved", "rejected")
    ]
    kept = {
        (await make_signal(scope="competitor", status="approved")).id: "approved",
        (await make_signal(scope="industry", status="auto")).id: "auto",
        (await make_signal(scope="competitor", status="needs_review")).id: "needs_review",
    }
    # relevant docs triaged as scope "jfrog" and never clustered must not open a signal
    jfrog_triage = {"scope": "jfrog", "relevant": True}
    waiting = await make_document(status="relevant", triage=jfrog_triage)
    member = await make_document(status="relevant", triage=jfrog_triage, story_id=legacy[0].id)
    docs = {
        waiting.id: "irrelevant",
        member.id: "relevant",  # already in a (now suppressed) story: kept as filed
        (await make_document(status="relevant", triage={"scope": "competitor"})).id: "relevant",
        (await make_document(status="relevant", triage={"scope": "industry"})).id: "relevant",
        (await make_document(status="new")).id: "new",
        (await make_document(status="irrelevant", triage=jfrog_triage)).id: "irrelevant",
    }
    # legacy JFrog company links on visible signals and on documents ("Sonatype vs
    # JFrog", "JFrog researchers find npm malware") go; every other link stays.
    versus = await make_signal(
        scope="competitor", status="approved", competitors=["sonatype", "jfrog"]
    )
    research = await make_signal(scope="industry", status="auto", competitors=["jfrog"])
    kept |= {versus.id: "approved", research.id: "auto"}
    linked = await make_document(status="relevant", triage={"scope": "competitor"})
    session.add_all(
        [
            DocumentCompetitor(document_id=linked.id, competitor_id=sonatype.id),
            DocumentCompetitor(document_id=linked.id, competitor_id=jf.id),
            DocumentCompetitor(document_id=member.id, competitor_id=jf.id),
        ]
    )
    await session.flush()
    docs[linked.id] = "relevant"

    async def links() -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
        sig = await session.execute(
            select(SignalCompetitor.signal_id, SignalCompetitor.competitor_id)
        )
        doc = await session.execute(
            select(DocumentCompetitor.document_id, DocumentCompetitor.competitor_id)
        )
        return {tuple(r) for r in sig}, {tuple(r) for r in doc}

    assert (versus.id, jf.id) in (await links())[0]

    async def enabled() -> dict[int, bool]:
        return dict((await session.execute(select(Source.id, Source.enabled))).all())

    async def statuses() -> dict[int, str]:
        return dict((await session.execute(select(Signal.id, Signal.status))).all())

    async def doc_statuses() -> dict[int, str]:
        rows = await session.execute(
            select(Document.id, Document.status).where(Document.id.in_(docs))
        )
        return dict(rows.all())

    async def check(on: dict[int, bool]) -> None:
        st = await statuses()
        assert {k: on[s.id] for k, s in own.items()} == {
            k: k == "github_repo_stats" for k in SELF_KINDS
        }
        assert on[rival.id] and on[industry.id]
        assert all(st[s.id] == "suppressed" for s in legacy)
        assert {i: st[i] for i in kept} == kept
        assert await doc_statuses() == docs
        assert await links() == ({(versus.id, sonatype.id)}, {(linked.id, sonatype.id)})

    assert (await doc_statuses())[waiting.id] == "relevant"
    before = await enabled()
    await connection.run_sync(_step("upgrade"))
    after = await enabled()
    await check(after)
    # idempotent: a second run changes nothing and does not fail
    await connection.run_sync(_step("upgrade"))
    assert await enabled() == after
    await check(after)
    # only self-tied, non-stats rows changed; every other source (seeded ones too) kept its flag
    self_ids = set(await session.scalars(select(Source.id).where(Source.competitor_id == jf.id)))
    self_stats = set(
        await session.scalars(
            select(Source.id).where(
                Source.competitor_id == jf.id, Source.kind == "github_repo_stats"
            )
        )
    )
    changed = {i for i in before if before[i] != after[i]}
    assert changed and changed <= self_ids - self_stats
    assert len(self_stats) == 2 and all(after[i] for i in self_stats)  # seeded jfrog-cli too

    # downgrade is a no-op on data: nothing is re-enabled, un-suppressed or made relevant
    await connection.run_sync(_step("downgrade"))
    assert await enabled() == after
    await check(after)


async def test_downgrade_0006_then_upgrade(engine):
    url = os.environ["TEST_DATABASE_URL"]

    async def version() -> str:
        async with engine.connect() as conn:
            return await conn.scalar(text("select version_num from alembic_version"))

    head = ScriptDirectory.from_config(_cfg(url)).get_current_head()
    assert await version() == head
    try:
        await asyncio.to_thread(command.downgrade, _cfg(url), "0005")
        assert await version() == "0005"
    finally:
        await asyncio.to_thread(command.upgrade, _cfg(url), "head")
    assert await version() == head
