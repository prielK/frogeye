from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import delete, func, select

from app.agents.runtime.accounted import BudgetExceeded
from app.agents.runtime.context import RunCtx
from app.models import JfrogKbDoc, RetrievalItem
from app.rag.kb import KbStats, build_kb, load_jfrog_context, load_kb_urls

CTX = RunCtx(agent_run_id=None, node="kb")
A, B = "https://jfrog.test/a", "https://jfrog.test/b"
FIX = Path(__file__).parent.parent / "fixtures" / "kb"
RUNTIME = "https://jfrog.com/runtime/"
ADVANCED = "https://jfrog.com/advanced-security/"


def _page(title: str, body: str) -> httpx.Response:
    html = (
        f"<html><head><title>{title}</title></head>"
        f"<body><main><h1>{title}</h1><p>{body}</p></main></body></html>"
    )
    return httpx.Response(200, text=html, headers={"content-type": "text/html"})


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


@respx.mock
async def test_build_kb_upserts_and_indexes_changed_only(
    session, session_factory, polite_client, accounted_llm
):
    respx.get("https://jfrog.test/robots.txt").mock(return_value=httpx.Response(404))
    body = "Artifactory is a universal binary repository manager for every package type. " * 5
    pa = respx.get(A).mock(return_value=_page("Page A", body))
    respx.get(B).mock(return_value=_page("Page B", body + " Xray scans."))
    llm = accounted_llm()

    first = await build_kb(session_factory, llm, polite_client, ctx=CTX, urls=[A, B])
    assert first == KbStats(fetched=2, changed=2, indexed=2, skipped=0)
    assert await _count(session, JfrogKbDoc) == 2
    assert await _count(session, RetrievalItem) >= 2
    calls_after_first = len(llm.backend.requests)
    assert calls_after_first > 0

    second = await build_kb(session_factory, llm, polite_client, ctx=CTX, urls=[A, B])
    assert second == KbStats(fetched=2, changed=0, indexed=0, skipped=0)
    assert await _count(session, JfrogKbDoc) == 2
    assert len(llm.backend.requests) == calls_after_first

    respx.get(A).mock(return_value=_page("Page A", body + " Now with new AppTrust text."))
    third = await build_kb(session_factory, llm, polite_client, ctx=CTX, urls=[A, B])
    assert third == KbStats(fetched=2, changed=1, indexed=1, skipped=0)
    assert pa.called


async def _kb_chunks(session, url: str) -> int:
    kb_id = await session.scalar(select(JfrogKbDoc.id).where(JfrogKbDoc.url == url))
    return await session.scalar(
        select(func.count())
        .select_from(RetrievalItem)
        .where(RetrievalItem.kind == "jfrog_kb", RetrievalItem.ref_id == kb_id)
    )


def _failing_embed_for(marker: str):
    """A scripted embed that fails for texts containing `marker` while `fail[0]` is set."""
    from tests.agents.fakes import fake_embedding

    fail = [True]

    def _embed(*, texts, dims, **_):
        if fail[0] and any(marker in t for t in texts):
            raise RuntimeError("embeddings down")
        return [fake_embedding(t, dims) for t in texts]

    return _embed, fail


@respx.mock
async def test_build_kb_index_failure_skips_page_and_leaves_it_reindexable(
    session, session_factory, polite_client, accounted_llm
):
    """An LLMError on one page is a skip, the crawl goes on, and the page's hash is
    not committed, so the next crawl indexes it."""
    respx.get("https://jfrog.test/robots.txt").mock(return_value=httpx.Response(404))
    body = "Artifactory is a universal binary repository manager for every package type. " * 5
    respx.get(A).mock(return_value=_page("Page A", body))
    respx.get(B).mock(return_value=_page("Page B", body + " Xray scans."))
    embed, fail = _failing_embed_for("Page A")
    llm = accounted_llm({"embed": embed})

    first = await build_kb(session_factory, llm, polite_client, ctx=CTX, urls=[A, B])
    assert first == KbStats(fetched=2, changed=2, indexed=1, skipped=1)
    assert await _kb_chunks(session, A) == 0
    assert await _kb_chunks(session, B) >= 1

    fail[0] = False
    second = await build_kb(session_factory, llm, polite_client, ctx=CTX, urls=[A, B])
    assert second == KbStats(fetched=2, changed=1, indexed=1, skipped=0)
    assert await _kb_chunks(session, A) >= 1

    third = await build_kb(session_factory, llm, polite_client, ctx=CTX, urls=[A, B])
    assert third == KbStats(fetched=2, changed=0, indexed=0, skipped=0)


@respx.mock
async def test_build_kb_reindexes_unchanged_page_without_chunks(
    session, session_factory, polite_client, accounted_llm
):
    """Self-heal: a page stored with its current hash but no jfrog_kb chunks (the
    live Artifactory row left by the pre-fix crash) is indexed on the next crawl."""
    respx.get("https://jfrog.test/robots.txt").mock(return_value=httpx.Response(404))
    body = "Artifactory is a universal binary repository manager for every package type. " * 5
    respx.get(A).mock(return_value=_page("Page A", body))
    llm = accounted_llm()
    assert (await build_kb(session_factory, llm, polite_client, ctx=CTX, urls=[A])).indexed == 1
    kb_id = await session.scalar(select(JfrogKbDoc.id).where(JfrogKbDoc.url == A))
    await session.execute(
        delete(RetrievalItem).where(RetrievalItem.kind == "jfrog_kb", RetrievalItem.ref_id == kb_id)
    )
    await session.flush()
    hash_before = await session.scalar(
        select(JfrogKbDoc.content_hash).where(JfrogKbDoc.id == kb_id)
    )

    stats = await build_kb(session_factory, llm, polite_client, ctx=CTX, urls=[A])
    assert stats == KbStats(fetched=1, changed=0, indexed=1, skipped=0)
    assert await _kb_chunks(session, A) >= 1
    session.expire_all()
    kb = await session.get(JfrogKbDoc, kb_id)
    assert kb.content_hash == hash_before  # the content did not change


@respx.mock
async def test_build_kb_budget_stop_propagates_and_page_stays_reindexable(
    session, session_factory, polite_client, accounted_llm, test_settings_override
):
    respx.get("https://jfrog.test/robots.txt").mock(return_value=httpx.Response(404))
    body = "Artifactory is a universal binary repository manager for every package type. " * 5
    respx.get(A).mock(return_value=_page("Page A", body))
    broke = accounted_llm(settings=test_settings_override(llm_daily_budget_usd=0.0))
    with pytest.raises(BudgetExceeded):
        await build_kb(session_factory, broke, polite_client, ctx=CTX, urls=[A])
    assert await _kb_chunks(session, A) == 0

    stats = await build_kb(session_factory, accounted_llm(), polite_client, ctx=CTX, urls=[A])
    assert stats == KbStats(fetched=1, changed=1, indexed=1, skipped=0)
    assert await _kb_chunks(session, A) >= 1


@respx.mock
async def test_build_kb_skips_robots_disallowed(
    session, session_factory, polite_client, accounted_llm
):
    respx.get("https://jfrog.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /b\n")
    )
    respx.get(A).mock(return_value=_page("Page A", "Distribution moves releases. " * 10))
    blocked = respx.get(B).mock(return_value=_page("Page B", "never fetched"))

    stats = await build_kb(session_factory, accounted_llm(), polite_client, ctx=CTX, urls=[A, B])

    assert stats == KbStats(fetched=1, changed=1, indexed=1, skipped=1)
    assert not blocked.called
    assert [d.url for d in await session.scalars(select(JfrogKbDoc))] == [A]


@respx.mock
async def test_build_kb_skips_404_and_empty(session_factory, polite_client, accounted_llm):
    respx.get("https://jfrog.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(A).mock(return_value=httpx.Response(404))
    stats = await build_kb(session_factory, accounted_llm(), polite_client, ctx=CTX, urls=[A])
    assert stats == KbStats(fetched=0, changed=0, indexed=0, skipped=1)


def test_context_pack_loaded_and_under_limit():
    text = load_jfrog_context()
    assert text is load_jfrog_context()  # cached
    assert 300 <= len(text.split()) <= 900
    for needle in ("Artifactory", "Xray", "system of record", "Oct 2026", "swampUP"):
        assert needle in text
    assert "@" not in text


def test_kb_urls_seed():
    urls = load_kb_urls()
    assert len(urls) == 15
    assert all(u.startswith("https://jfrog.com/") for u in urls)
    assert len(set(urls)) == len(urls)
    assert {RUNTIME, ADVANCED} <= set(urls)


@respx.mock
async def test_kb_pages_describe_product(session, session_factory, polite_client, accounted_llm):
    """KB crawl note: the article extractor kept only /runtime/'s
    customer testimonials (about Enterprise+, another product) and /advanced-security/'s
    closing call to action, so neither page could ground a claim about its product. With
    fixture pages of the live pages' shape (on which the article extractor keeps exactly
    that), the stored text describes each product, in page order, and no testimonial,
    navigation, form or footer text is stored."""
    pages = {RUNTIME: "runtime.html", ADVANCED: "advanced_security.html"}
    respx.get("https://jfrog.com/robots.txt").mock(return_value=httpx.Response(404))
    for url, name in pages.items():
        body = (FIX / name).read_text(encoding="utf-8")
        respx.get(url).mock(
            return_value=httpx.Response(200, text=body, headers={"content-type": "text/html"})
        )

    stats = await build_kb(session_factory, accounted_llm(), polite_client, ctx=CTX, urls=[*pages])

    assert stats == KbStats(fetched=2, changed=2, indexed=2, skipped=0)
    stored = {kb.url: kb for kb in await session.scalars(select(JfrogKbDoc))}
    runtime, advanced = stored[RUNTIME].text, stored[ADVANCED].text
    assert runtime.startswith("Secure Your Runtime Environment\n")
    for phrase in (
        "Real-time visibility into running workloads JFrog Runtime shows which images",
        "run in each Kubernetes cluster",
        "correlates vulnerabilities with the workloads that are live",
        "traced to its build and release in Artifactory for fast remediation",
    ):
        assert phrase in runtime, phrase
    for gone in ("Enterprise+", "Mercedes", "Mandadi", "patchwork", "Pricing", "rights reserved"):
        assert gone not in runtime, gone
    assert advanced.startswith("Go Beyond the CVE\n")
    for phrase in (
        "Contextual Analysis\nChecks whether a CVE is reachable and exploitable",
        "Secrets Detection\nFinds exposed API keys, tokens and passwords",
        "Infrastructure as Code\nScans Terraform and other IaC files",
        "JFrog's differentiated approach",  # the closing copy stays, after the product
    ):
        assert phrase in advanced, phrase
    for gone in ("Home", "Work email", "rights reserved"):
        assert gone not in advanced, gone
    assert stored[RUNTIME].title == "JFrog Runtime | Runtime Security"


def test_cli_exits_2_without_openai_key(monkeypatch, capsys):
    import pytest

    from app.cli import build_kb as cli
    from app.core.config import get_settings

    monkeypatch.setattr(
        cli, "get_settings", lambda: get_settings().model_copy(update={"openai_api_key": None})
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().out


# ---- the two untested KB branches ----------------------------------------


def test_an_unknown_extract_mode_is_a_seed_error(tmp_path, monkeypatch):
    """jfrog_kb.yaml names an extraction mode per page; a typo fails loudly, naming it."""
    from app.rag import kb

    (tmp_path / "jfrog_kb.yaml").write_text(
        "pages:\n  - url: https://jfrog.test/x\n    name: X\n    extract: magic\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "SEED_DIR", tmp_path)
    kb._kb_pages.cache_clear()
    try:
        with pytest.raises(ValueError, match="unknown extract 'magic' for https://jfrog.test/x"):
            kb._kb_pages()
    finally:
        monkeypatch.undo()
        kb._kb_pages.cache_clear()
    assert kb.load_kb_urls()  # the real seed reads again


def test_sections_mode_falls_back_to_the_article_extractor(monkeypatch):
    """A sections page whose blocks hold under 200 characters (a page rendered by
    JavaScript, say) is read by the article extractor instead; a full one is not."""
    from app.processing.normalize import ExtractedText
    from app.rag import kb

    seen: list[str] = []

    def article(html: str, url: str) -> ExtractedText:
        seen.append(url)
        return ExtractedText(text="from the article extractor", title=None)

    monkeypatch.setattr(kb, "extract_text", article)
    shell = "<html><body><div id='root'><p>Loading...</p></div></body></html>"
    assert kb._extract(shell, RUNTIME, "sections").text == "from the article extractor"
    lines = "".join(f"<p>Runtime security for workload number {n}.</p>" for n in range(8))
    full = f"<html><body><main>{lines}</main></body></html>"  # distinct lines: no dedup
    got = kb._extract(full, RUNTIME, "sections")
    assert got.text.startswith("Runtime security for workload number 0.") and seen == [RUNTIME]
