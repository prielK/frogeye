# ruff: noqa: E501
import pytest
from sqlalchemy import select

from app.models import DocumentCompetitor
from app.processing.prefilter import AliasMatcher, prefilter_documents

CASES = [
    # (title, text, source_slug, expected_competitor_slugs, expected_scope)
    ("Harness the power of AI in marketing", "Brands harness AI to grow.", None, set(), None),
    (
        "Harness launches dependency firewall",
        "Harness adds a firewall to its artifact registry.",
        None,
        {"harness"},
        "competitor",
    ),
    ("Socket.IO 5 released", "Better WebSocket handling for Node.", None, set(), None),
    (
        "Socket raises $60M",
        "Socket blocks malicious packages in npm.",
        None,
        {"socket"},
        "competitor",
    ),
    ("How to mend a broken fence", "Mend it with wood.", None, set(), None),
    ("Sonatype Nexus Repository 3.97 released", "New release.", None, {"sonatype"}, "competitor"),
    ("JFrog announces Q3 results", "Revenue grew.", None, set(), None),  # self only
    ("New npm worm hits 500 packages", "A supply chain attack spreads.", None, set(), "industry"),
    ("Amazon EC2 adds new instance types", "Compute news.", "aws-whats-new", set(), None),
    ("AWS CodeArtifact adds Cargo support", "Rust crates.", "aws-whats-new", {"aws"}, "competitor"),
    ("Our engineering culture", "Team post.", "chainguard-blog", {"chainguard"}, "competitor"),
]


@pytest.mark.parametrize("title,text,src,expected,scope", CASES)
async def test_prefilter_cases(
    seeded, session, source_by_key, slug_of, title, text, src, expected, scope
):
    m = await AliasMatcher.from_db(session)
    res = m.match(title=title, text=text, source=source_by_key(src))
    assert {slug_of(i) for i in res.competitor_ids} == expected
    assert res.scope_hint == scope
    assert res.passed == (scope is not None)


async def test_matched_terms_and_order(seeded, session, source_by_key, slug_of):
    m = await AliasMatcher.from_db(session)
    res = m.match(
        title="Harness and Sonatype ship a dependency firewall",
        text="Sonatype again. Harness CI too.",
        source=source_by_key(None),
    )
    assert res.competitor_ids == sorted(res.competitor_ids)
    assert len(res.matched_terms) == len(set(res.matched_terms))
    assert "Sonatype" in res.matched_terms


async def test_ambiguous_alias_is_case_sensitive(seeded, session, source_by_key):
    m = await AliasMatcher.from_db(session)
    res = m.match(title="wiz kid", text="a devops pipeline story", source=source_by_key(None))
    assert res.competitor_ids == []


async def test_jfrog_own_feed_does_not_autopass(seeded, session, make_source, slug_of):
    # an auto-pass feed tied to JFrog itself no longer tags (or passes) its items.
    from app.models import Competitor

    jf = (await session.execute(select(Competitor).where(Competitor.slug == "jfrog"))).scalar_one()
    src = await make_source(competitor_id=jf.id, config={})
    m = await AliasMatcher.from_db(session)
    res = m.match(title="Quarterly note", text="Nothing named.", source=src)
    assert res.passed is False and res.competitor_ids == [] and res.scope_hint is None
    assert res.reason == "jfrog_only"  # ops can see why it was dropped


async def test_prefilter_self_only_does_not_pass(
    seeded, session, source_by_key, slug_of, make_document
):
    # JFrog never becomes a document's company. A JFrog-only story is dropped; one that
    # sets JFrog against a competitor is that competitor's news.
    m = await AliasMatcher.from_db(session)
    alone = m.match(
        title="JFrog stock rises after earnings beat",
        text="Analysts raised their JFrog price target after Artifactory revenue grew.",
        source=source_by_key(None),
    )
    assert alone.passed is False
    assert alone.competitor_ids == [] and alone.scope_hint is None
    assert alone.reason == "jfrog_only"
    miss = m.match(title="Cooking tips", text="Pasta recipes.", source=source_by_key(None))
    assert miss.passed is False and miss.reason is None
    versus = m.match(
        title="Sonatype vs JFrog",
        text="Sonatype Nexus and JFrog Artifactory compared for enterprise teams.",
        source=source_by_key(None),
    )
    assert versus.passed is True and versus.scope_hint == "competitor"
    assert {slug_of(i) for i in versus.competitor_ids} == {"sonatype"}
    assert versus.reason is None

    doc = await make_document(
        source_id=source_by_key(None).id,
        title="JFrog stock rises after earnings beat",
        clean_text="Analysts raised their JFrog price target.",
    )
    stats = await prefilter_documents(session, [doc.id], m)
    assert (stats.passed, stats.rejected) == (0, 1)
    await session.refresh(doc)
    assert doc.status == "irrelevant"
    # the reason is persisted with the prefilter result
    assert doc.prefilter == {"matched_terms": [], "scope_hint": None, "reason": "jfrog_only"}
    links = await session.scalars(
        select(DocumentCompetitor).where(DocumentCompetitor.document_id == doc.id)
    )
    assert links.all() == []


async def test_prefilter_documents_writes_links_and_status(
    seeded, session, make_document, make_source
):
    src = await make_source()
    good = await make_document(
        source_id=src.id, title="Snyk acquires a startup", clean_text="Deal news."
    )
    bad = await make_document(source_id=src.id, title="Cooking tips", clean_text="Pasta recipes.")
    done = await make_document(
        source_id=src.id, title="Snyk again", clean_text="x", status="relevant"
    )
    m = await AliasMatcher.from_db(session)
    stats = await prefilter_documents(session, [good.id, bad.id, done.id], m)
    assert (stats.passed, stats.rejected) == (1, 1)

    await session.refresh(good)
    await session.refresh(bad)
    await session.refresh(done)
    assert good.status == "new" and good.scope_hint == "competitor"
    assert good.prefilter["matched_terms"] == ["Snyk"]
    assert bad.status == "irrelevant"
    assert bad.prefilter == {"matched_terms": [], "scope_hint": None}
    assert done.status == "relevant" and done.prefilter is None

    # idempotent: second run processes nothing new for the passed doc's links
    await prefilter_documents(session, [good.id, bad.id], m)
    rows = (
        (
            await session.execute(
                select(DocumentCompetitor).where(DocumentCompetitor.document_id == good.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_hacker_news_hit_without_alias_is_rejected(seeded, session, make_document, slug_of):
    # generated search sources carry a competitor but must not auto-pass. Before the fix
    # "Show HN: Agentfork" from the GitHub HN search was tagged GitHub just for being a hit.
    from app.models import Source

    hn = await session.scalar(select(Source).where(Source.name == "GitHub · Hacker News"))
    assert hn.config["auto_pass"] is False
    m = await AliasMatcher.from_db(session)
    miss = m.match(title="Show HN: Agentfork", text="A tiny fork manager for agents.", source=hn)
    assert not miss.passed and miss.competitor_ids == [] and miss.scope_hint is None
    hit = m.match(
        title="GitHub Actions adds immutable releases",
        text="Workflows can now pin artifacts.",
        source=hn,
    )
    assert hit.passed and {slug_of(i) for i in hit.competitor_ids} == {"github"}

    rejected = await make_document(source_id=hn.id, title="Show HN: Agentfork", clean_text="")
    passed = await make_document(
        source_id=hn.id, title="GitHub Actions adds immutable releases", clean_text=""
    )
    stats = await prefilter_documents(session, [rejected.id, passed.id], m)
    assert (stats.passed, stats.rejected) == (1, 1)
    await session.refresh(rejected)
    assert rejected.status == "irrelevant"
