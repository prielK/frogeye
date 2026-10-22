"""Job-board discovery: bounded (1 request per competitor x ATS x candidate), cached, no self."""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.cli import discover_job_boards as cli
from app.cli.discover_job_boards import (
    ATS_ORDER,
    MISS_TTL,
    candidate_tokens,
    discover,
    load_hints,
)
from app.collectors.jobs.collector import ADAPTERS
from app.models import Competitor, JobPosting, Source

FIX = Path(__file__).parent.parent / "fixtures" / "jobs"
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
ATS_HOSTS = ("boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com")


def _fixture(name: str) -> str:
    return (FIX / f"{name}.json").read_text(encoding="utf-8")


def _token(url: str) -> str:
    """The board token an ATS endpoint URL asks for."""
    parts = urlsplit(url).path.strip("/").split("/")
    return parts[2] if parts[0] == "v1" else parts[-1]  # greenhouse: /v1/boards/{token}/jobs


def _miss_everything() -> None:
    """Every other board is unknown (404). Call it after the specific routes: respx matches
    routes in the order they were added."""
    for host in ATS_HOSTS:
        respx.route(host=host).mock(return_value=httpx.Response(404, json={"ok": False}))


def _calls() -> Counter:
    return Counter(str(c.request.url) for c in respx.calls)


@pytest.fixture
async def acmesec(session, competitor_factory) -> Competitor:
    comp = competitor_factory(slug="acmesec", name="Acmesec", tier=2, aliases=["Acmesec"])
    session.add(comp)
    await session.flush()
    return comp


async def _sources(session_factory, **where) -> list[Source]:
    async with session_factory() as s:
        q = select(Source).where(Source.kind == "job_board")
        for k, v in where.items():
            q = q.where(getattr(Source, k) == v)
        return list((await s.scalars(q.order_by(Source.id))).all())


def test_candidate_tokens():
    assert candidate_tokens("palo-alto-networks", "Palo Alto Networks") == [
        "palo-alto-networks",
        "paloaltonetworks",
    ]
    assert candidate_tokens("mend", "Mend.io") == ["mend", "mendio"]
    assert candidate_tokens("wiz", "Wiz", ["WizInc", "wiz"]) == ["wiz", "wizinc"]
    many = candidate_tokens("a-b", "A B C", ["x", "y", "z"])
    assert len(many) == 4 and many[:2] == ["a-b", "ab"]  # at most 4 per ATS


@respx.mock
async def test_discovery_bounded_and_cached(seeded, session_factory, make_polite_client):
    _miss_everything()
    hints = load_hints()
    competitors = (await seeded.scalars(select(Competitor).order_by(Competitor.id))).all()
    others = [c for c in competitors if not c.is_self]
    expected = {
        ADAPTERS[ats].url(token)
        for c in others
        for ats in ATS_ORDER
        for token in candidate_tokens(c.slug, c.name, hints.get(c.slug, ()))
    }
    async with make_polite_client() as http:
        first = await discover(session_factory, http, now=NOW, hints=hints)
    calls = _calls()
    assert set(calls) == expected and max(calls.values()) == 1  # 1 per competitor/ATS/token
    assert first.checked == len(expected) and first.found == [] and first.cached_misses == 0
    assert not any(_token(url) == "jfrog" for url in calls)  # the self competitor is skipped
    # the misses are cached: one disabled placeholder per competitor, never fetched
    misses = await _sources(session_factory, enabled=False)
    assert len(misses) == len(others)
    assert all(m.config["missing"] is True and m.url.startswith("discovery://") for m in misses)

    # a second run within 30 days asks nothing
    async with make_polite_client() as http:
        second = await discover(session_factory, http, now=NOW + timedelta(days=29), hints=hints)
    assert len(respx.calls) == len(expected)
    assert second.checked == 0 and second.cached_misses == len(expected)

    # after 30 days a competitor's candidates are asked again, once each
    async with make_polite_client() as http:
        later = await discover(
            session_factory, http, now=NOW + MISS_TTL + timedelta(days=1), slug="snyk"
        )
    snyk = [u for u in _calls() if _token(u) == "snyk"]
    assert later.checked == len(ATS_ORDER) and len(snyk) == len(ATS_ORDER)
    assert all(_calls()[u] == 2 for u in snyk)

    # asking for the self competitor by name checks nothing at all
    async with make_polite_client() as http:
        own = await discover(session_factory, http, now=NOW, slug="jfrog")
    assert (own.checked, own.found, own.cached_misses) == (0, [], 0)
    assert len(respx.calls) == len(expected) + len(ATS_ORDER)


@respx.mock
async def test_found_board_becomes_an_enabled_source_with_postings(
    acmesec, session_factory, make_polite_client
):
    respx.get(ADAPTERS["lever"].url("acmesec")).mock(
        return_value=httpx.Response(200, text=_fixture("lever"))
    )
    _miss_everything()
    async with make_polite_client() as http:
        out = await discover(session_factory, http, now=NOW, slug="acmesec")
    assert out.found == [{"competitor": "acmesec", "ats": "lever", "token": "acmesec", "open": 2}]
    # greenhouse was asked first and missed; ashby is never asked once a board is found
    assert [_token(u) for u in _calls()] == ["acmesec", "acmesec"]
    assert not any("ashbyhq" in u for u in _calls())
    (board,) = await _sources(session_factory, competitor_id=acmesec.id)
    assert board.enabled and board.config == {"ats": "lever", "token": "acmesec"}
    assert board.url == "https://api.lever.co/v0/postings/acmesec?mode=json"
    assert board.name == "Acmesec · Lever jobs" and board.reliability == "A"
    assert board.last_fetched_at is not None  # the probe is the board's first snapshot
    async with session_factory() as s:
        n = await s.scalar(
            select(func.count()).select_from(JobPosting).where(JobPosting.source_id == board.id)
        )
    assert n == 2

    # a competitor with a board is not probed again
    async with make_polite_client() as http:
        again = await discover(session_factory, http, now=NOW + timedelta(days=40), slug="acmesec")
    assert again.checked == 0 and len(respx.calls) == 2


@respx.mock
async def test_a_partial_probe_records_the_board_but_not_as_listed_in_full(
    acmesec, session_factory, make_polite_client
):
    """The probe is the board's first snapshot only when it listed the whole board; a
    Greenhouse page short of its meta.total stores its postings, measures nothing, and the
    next complete listing (the daily run) is the first snapshot. A complete probe is one."""
    short = json.loads(_fixture("greenhouse"))
    short["meta"]["total"] = len(short["jobs"]) + 5
    respx.get(ADAPTERS["greenhouse"].url("acmesec")).mock(
        return_value=httpx.Response(200, json=short)
    )
    _miss_everything()
    async with make_polite_client() as http:
        out = await discover(session_factory, http, now=NOW, slug="acmesec")
    assert [f["token"] for f in out.found] == ["acmesec"]
    (board,) = await _sources(session_factory, competitor_id=acmesec.id)
    assert board.enabled and board.last_fetched_at is not None
    assert board.first_complete_fetch_at is None  # pending: no open-role count yet


@respx.mock
async def test_a_complete_probe_is_the_first_snapshot(acmesec, session_factory, make_polite_client):
    respx.get(ADAPTERS["lever"].url("acmesec")).mock(
        return_value=httpx.Response(200, text=_fixture("lever"))
    )
    _miss_everything()
    async with make_polite_client() as http:
        await discover(session_factory, http, now=NOW, slug="acmesec")
    (board,) = await _sources(session_factory, competitor_id=acmesec.id)
    assert board.first_complete_fetch_at == NOW


@respx.mock
async def test_a_board_that_is_already_a_source_is_reported_as_not_recorded(
    acmesec, session_factory, make_polite_client, make_source, session
):
    """When the found board's URL already belongs to another source the insert
    conflicts and nothing is recorded for this competitor; the JSON line says so instead of
    listing it as found."""
    await make_source(
        kind="job_board",
        name="Someone else's board",
        url=ADAPTERS["lever"].url("acmesec"),
        enabled=False,
        config={"ats": "lever", "token": "acmesec"},
    )
    await session.commit()
    respx.get(ADAPTERS["lever"].url("acmesec")).mock(
        return_value=httpx.Response(200, text=_fixture("lever"))
    )
    _miss_everything()
    async with make_polite_client() as http:
        out = await discover(session_factory, http, now=NOW, slug="acmesec")
    assert out.found == []
    assert out.not_recorded == [
        {
            "competitor": "acmesec",
            "ats": "lever",
            "token": "acmesec",
            "open": 2,
            "reason": "already a source",
        }
    ]
    assert out.as_json()["not_recorded"] == out.not_recorded
    assert await _sources(session_factory, competitor_id=acmesec.id) == []


@respx.mock
async def test_unverified_or_empty_boards_are_misses(acmesec, session_factory, make_polite_client):
    other = json.loads(_fixture("greenhouse").replace("Acmesec", "Globex"))
    respx.get(ADAPTERS["greenhouse"].url("acmesec")).mock(
        return_value=httpx.Response(200, json=other)
    )
    respx.get(ADAPTERS["ashby"].url("acmesec")).mock(
        return_value=httpx.Response(200, json={"apiVersion": "1", "jobs": []})
    )
    _miss_everything()
    async with make_polite_client() as http:
        out = await discover(session_factory, http, now=NOW, slug="acmesec")
    assert out.found == [] and out.checked == 3
    (miss,) = await _sources(session_factory, competitor_id=acmesec.id)
    assert not miss.enabled and miss.config["missing"] is True
    assert set(miss.config["checked"]) == {"greenhouse:acmesec", "lever:acmesec", "ashby:acmesec"}


@respx.mock
async def test_transient_errors_are_not_cached_and_dry_run_writes_nothing(
    acmesec, session_factory, make_polite_client
):
    gh = respx.get(ADAPTERS["greenhouse"].url("acmesec")).mock(return_value=httpx.Response(500))
    _miss_everything()
    async with make_polite_client() as http:
        first = await discover(session_factory, http, now=NOW, slug="acmesec")
    assert first.checked == 3
    (miss,) = await _sources(session_factory, competitor_id=acmesec.id)
    assert set(miss.config["checked"]) == {"lever:acmesec", "ashby:acmesec"}  # 500 not cached

    gh.mock(return_value=httpx.Response(200, text=_fixture("greenhouse")))
    async with make_polite_client() as http:
        dry = await discover(session_factory, http, now=NOW, slug="acmesec", dry_run=True)
    assert dry.found == [
        {"competitor": "acmesec", "ats": "greenhouse", "token": "acmesec", "open": 3}
    ]
    assert dry.checked == 1 and dry.cached_misses == 0  # greenhouse is asked first
    (still,) = await _sources(session_factory, competitor_id=acmesec.id)
    assert still.id == miss.id and not still.enabled  # nothing created by a dry run


def test_cli_prints_one_json_line(monkeypatch, capsys):
    async def fake_run(slug, dry_run):
        assert (slug, dry_run) == ("snyk", True)
        return {"checked": 3, "found": [], "cached_misses": 0}

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    cli.main(["--competitor", "snyk", "--dry-run"])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert json.loads(out[0]) == {"checked": 3, "found": [], "cached_misses": 0}


def test_cli_unknown_competitor_exits_2(monkeypatch, capsys):
    async def fake_run(slug, dry_run):
        raise LookupError(f"no competitor {slug!r}")

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--competitor", "nope"])
    assert exc.value.code == 2 and "nope" in capsys.readouterr().err
