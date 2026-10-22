"""Job-board collection: ATS adapters, department/seniority rules, posting sync, failures."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import respx
from sqlalchemy import select

from app.collectors import get_collector
from app.collectors.base import CollectorError, PostingIn
from app.collectors.jobs.collector import ADAPTERS, JobBoardCollector
from app.collectors.jobs.common import country_of
from app.collectors.jobs.store import sync_postings
from app.collectors.jobs.taxonomy import (
    DEPARTMENTS,
    SENIORITIES,
    normalize_department,
    seniority_of,
)
from app.core.http import API_HOSTS, InMemoryHostGuard, pause_duration
from app.models import Competitor, JobPosting, PipelineRun, Source
from app.processing.ingest import run_ingestion

FIX = Path(__file__).parent.parent / "fixtures" / "jobs"
GH_URL = "https://boards-api.greenhouse.io/v1/boards/acmesec/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/acmesec?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/acmesec?includeCompensation=true"
URLS = {"greenhouse": GH_URL, "lever": LEVER_URL, "ashby": ASHBY_URL}


def _fixture(name: str) -> str:
    return (FIX / f"{name}.json").read_text(encoding="utf-8")


@pytest.fixture
async def acmesec(session, competitor_factory) -> Competitor:
    comp = competitor_factory(slug="acmesec", name="Acmesec", tier=2)
    session.add(comp)
    await session.flush()
    return comp


@pytest.fixture
def board(make_source, acmesec):
    async def _make(ats: str = "greenhouse", **overrides) -> Source:
        data = {
            "kind": "job_board",
            "name": f"Acmesec · {ats} jobs",
            "url": URLS[ats],
            "competitor_id": acmesec.id,
            "reliability": "A",
            "config": {"ats": ats, "token": "acmesec"},
        }
        data.update(overrides)
        return await make_source(**data)

    return _make


def _posting(n: int, **kw) -> PostingIn:
    data = {
        "external_id": f"job-{n}",
        "title": f"Software Engineer {n}",
        "department_raw": "Engineering",
        "location": "Tel Aviv, Israel",
        "url": f"https://jobs.example.test/{n}",
        "description_text": f"Build things, part {n}.",
    }
    data.update(kw)
    return PostingIn(**data)


# -- adapters ------------------------------------------------------------------------------


def test_registered():
    assert isinstance(get_collector("job_board"), JobBoardCollector)
    assert set(ADAPTERS) == {"greenhouse", "lever", "ashby"}


@respx.mock
async def test_greenhouse_lever_ashby_parse(board, polite_client, settings):
    for ats, url in URLS.items():
        respx.get(url).mock(return_value=httpx.Response(200, text=_fixture(ats)))
    collector = JobBoardCollector()

    gh = await collector.fetch(await board("greenhouse"), None, polite_client)
    assert gh.postings_complete is True and gh.items == [] and gh.metrics == []
    assert [p.external_id for p in gh.postings] == ["4012345", "4012346", "4012347"]
    eng, res, ae = gh.postings
    assert eng.title == "Senior Software Engineer, Supply Chain"
    assert eng.department_raw == "R&D" and eng.location == "Tel Aviv, Israel"
    assert eng.country == "Israel" and eng.remote is False
    assert eng.posted_at == datetime(2026, 9, 1, 13, 15, tzinfo=UTC)
    assert eng.url == "https://job-boards.greenhouse.io/acmesec/jobs/4012345"
    # content is HTML-escaped HTML: unescaped, tags stripped, entities decoded
    assert "Build scanners for npm & PyPI packages" in eng.description_text
    assert "Design artifact provenance services\nused by thousands" in eng.description_text
    assert "<" not in eng.description_text and "&amp;" not in eng.description_text
    assert res.remote is True and res.country == "United States"
    assert ae.posted_at is None  # no first_published: unknown, never guessed
    assert ae.country == "United Kingdom"

    lv = await collector.fetch(await board("lever"), None, polite_client)
    assert lv.postings_complete is True
    adv, pm = lv.postings
    assert adv.external_id == "3c1f6a2e-8b1d-4d7e-9a51-1f2e3d4c5b6a"
    assert adv.title == "Developer Advocate"
    assert (adv.department_raw, adv.team) == ("Marketing", "Developer Relations")
    assert adv.location == "Berlin, Germany" and adv.country == "Germany"
    assert adv.remote is False and adv.employment_type == "Full-time"
    assert adv.posted_at == datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    assert adv.url == "https://jobs.lever.co/acmesec/3c1f6a2e-8b1d-4d7e-9a51-1f2e3d4c5b6a"
    assert adv.description_text.startswith("Acmesec is looking for a Developer Advocate")
    assert "Write tutorials and talks" in adv.description_text  # the lists are kept
    assert pm.remote is True and pm.country == "United States"

    ab = await collector.fetch(await board("ashby"), None, polite_client)
    mle, csm = ab.postings
    assert mle.title == "Machine Learning Engineer"
    assert (mle.department_raw, mle.team) == ("Engineering", "AI Platform")
    assert mle.location == "New York, NY" and mle.country == "United States"
    assert mle.remote is False and mle.employment_type == "Full-time"
    assert mle.posted_at == datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    assert mle.url == "https://jobs.ashbyhq.com/acmesec/6f2a9c1e-0b3d-4e5f-8a7b-2c1d0e9f8a7b"
    assert (
        mle.description_text == "Train models that flag risky dependencies for Acmesec customers."
    )
    assert csm.remote is True and csm.location == "Remote (EMEA)"

    # APIs, not pages: no robots.txt request (respx would reject an unmocked one), generic UA
    for call in respx.calls:
        assert urlsplit(str(call.request.url)).path != "/robots.txt"
        assert call.request.headers["User-Agent"] == settings.http_user_agent


@respx.mock
async def test_large_board_fits_the_ats_cap_and_description_is_capped(
    board, polite_client, settings
):
    # 700 postings with long bodies: ~3.5 MB, over PoliteClient's 2 MB default cap
    body = "<p>" + ("supply chain " * 400) + "</p>"
    jobs = [
        {
            "id": i,
            "title": f"Engineer {i}",
            "location": {"name": "Remote"},
            "absolute_url": f"https://job-boards.greenhouse.io/acmesec/jobs/{i}",
            "content": body.replace("<", "&lt;").replace(">", "&gt;"),
            "departments": [],
            "offices": [],
        }
        for i in range(700)
    ]
    payload = json.dumps({"jobs": jobs, "meta": {"total": 700}})
    assert 2_000_000 < len(payload) < settings.ats_max_bytes == 12_000_000
    respx.get(GH_URL).mock(return_value=httpx.Response(200, text=payload))
    res = await JobBoardCollector().fetch(await board("greenhouse"), None, polite_client)
    assert len(res.postings) == 700 and res.postings_complete
    assert all(len(p.description_text) <= 4000 for p in res.postings)


@respx.mock
async def test_greenhouse_partial_listing_is_not_complete(board, polite_client):
    data = json.loads(_fixture("greenhouse"))
    data["meta"]["total"] = 5  # the board says 5 jobs, the response lists 3
    respx.get(GH_URL).mock(return_value=httpx.Response(200, json=data))
    res = await JobBoardCollector().fetch(await board("greenhouse"), None, polite_client)
    assert len(res.postings) == 3 and res.postings_complete is False


async def test_collector_refuses_placeholders_and_self(make_source, polite_client, session):
    missing = await make_source(
        kind="job_board", url="discovery://job_board/x", config={"missing": True}
    )
    with pytest.raises(CollectorError, match="no public job board"):
        await JobBoardCollector().fetch(missing, None, polite_client)
    bare = await make_source(kind="job_board", url="https://api.lever.co/v0/postings/x")
    with pytest.raises(CollectorError, match="config"):
        await JobBoardCollector().fetch(bare, None, polite_client)
    unknown = await make_source(
        kind="job_board", url="https://x.test/jobs", config={"ats": "myspace", "token": "x"}
    )
    with pytest.raises(CollectorError, match="myspace"):
        await JobBoardCollector().fetch(unknown, None, polite_client)
    jfrog = Competitor(slug="jf-self", name="JF", tier=0, relationship="self", is_self=True)
    session.add(jfrog)
    await session.flush()
    own = Source(
        kind="job_board",
        name="self board",
        url="https://api.lever.co/v0/postings/jf-self?mode=json",
        reliability="A",
        config={"ats": "lever", "token": "jf-self"},
        competitor=jfrog,
    )
    with pytest.raises(CollectorError, match="self"):  # no request is even made
        await JobBoardCollector().fetch(own, None, polite_client)


# -- taxonomy ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "raw", "department", "seniority"),
    [
        ("Senior Software Engineer, Supply Chain", "R&D", "Engineering", "Senior"),
        ("Staff Security Researcher", "Research", "Security", "Staff & Principal"),
        ("Account Executive, EMEA", "Sales", "Sales", "Mid"),
        ("VP Marketing", None, "Marketing", "VP & C-level"),
        ("Solutions Engineer", "Sales", "Solutions & SE", "Mid"),
        ("Security Sales Engineer", None, "Solutions & SE", "Mid"),
        ("Developer Advocate", "Marketing", "DevRel & community", "Mid"),
        ("Product Designer", "Product", "Design", "Mid"),
        ("Senior Product Manager", "Product", "Product", "Senior"),
        ("Product Marketing Manager", None, "Marketing", "Mid"),
        ("Machine Learning Engineer", None, "Data & ML", "Mid"),
        ("Engineering Manager, Platform", "Engineering", "Engineering", "Manager"),
        ("Director of Customer Success", None, "Customer success", "Director"),
        ("Customer Success Manager", None, "Customer success", "Mid"),
        ("Software Engineering Intern", None, "Engineering", "Intern"),
        ("Junior Accountant", "Finance", "G&A", "Junior"),
        ("Recruiter", "People", "G&A", "Mid"),
        ("Office Coordinator", "G&A", "G&A", "Mid"),
        ("Head of Partnerships", None, "Sales", "Director"),
        ("Chief Technology Officer", None, "Engineering", "VP & C-level"),
        ("Principal Architect", None, "Engineering", "Staff & Principal"),
        ("Coordinator", "Customer Support", "Customer success", "Mid"),
        ("Barista", "Kitchen", "Other", "Mid"),
        ("Mystery role", None, "Other", "Mid"),
    ],
)
def test_department_and_seniority_rules(title, raw, department, seniority):
    assert normalize_department(raw, title) == department
    assert seniority_of(title) == seniority


@pytest.mark.parametrize(
    ("title", "department", "seniority"),
    [
        # a vertical named in a sales or SE title is not a finance role
        ("Account Executive, Financial Services", "Sales", "Mid"),
        ("Solutions Architect, Financial Services", "Solutions & SE", "Mid"),
        ("Enterprise Account Executive - Financial Institutions", "Sales", "Mid"),
        ("Financial Analyst", "G&A", "Mid"),  # finance roles stay G&A
        ("Finance Manager", "G&A", "Manager"),
        ("FP&A Lead", "G&A", "Senior"),
        # The broad finance rule stays for finance roles ...
        ("Financial Auditor", "G&A", "Mid"),
        ("Financial Risk Analyst", "G&A", "Mid"),
        ("Financial Crimes Investigator", "G&A", "Mid"),
        # ... but a sales or SE role, or a finance word naming a market vertical, is no G&A
        ("Enterprise Account Executive - Banking & Finance", "Sales", "Mid"),
        ("Solutions Engineer - Finance", "Solutions & SE", "Mid"),
        ("Sales Director, Finance and Insurance", "Sales", "Director"),
        ("Financial Services Industry Lead", "Other", "Senior"),
        # a finance role inside a GTM org is G&A: FP&A, a controller, accounting or a
        # finance manager wins over Sales; a sales role in a finance market stays Sales
        ("Sales Finance Manager", "G&A", "Manager"),
        ("Senior Manager, GTM Finance", "G&A", "Manager"),
        ("FP&A Manager, Go-to-Market", "G&A", "Manager"),
        ("Revenue Accountant", "G&A", "Mid"),
        ("Sales Controller", "G&A", "Mid"),
        ("Go-to-Market Finance Lead", "G&A", "Senior"),
        ("Sales Manager, Financial Services", "Sales", "Manager"),
        # UI and UX engineers count as Design
        ("UI/UX Engineer", "Design", "Mid"),
        ("Senior UX Engineer", "Design", "Senior"),
        # "lead" is a word; Leadership, Leader and Leading are not Senior
        ("Leadership Development Partner", "Other", "Mid"),
        ("Thought Leadership Writer", "Other", "Mid"),
        ("Lead Software Engineer", "Engineering", "Senior"),
        ("Engineering Lead", "Engineering", "Senior"),
        ("Technical Leader", "Engineering", "Senior"),  # a leader is still senior
        ("Team Leader", "Other", "Manager"),  # unchanged: a team leader manages
    ],
)
def test_taxonomy_fixes_from_the_task_2_review(title, department, seniority):
    assert normalize_department(None, title) == department
    assert seniority_of(title) == seniority


def test_taxonomies_are_the_exact_lists():
    assert list(DEPARTMENTS) == [
        "Engineering",
        "Security",
        "Product",
        "Design",
        "Data & ML",
        "Sales",
        "Solutions & SE",
        "Customer success",
        "Marketing",
        "DevRel & community",
        "G&A",
        "Other",
    ]
    assert list(SENIORITIES) == [
        "Intern",
        "Junior",
        "Mid",
        "Senior",
        "Staff & Principal",
        "Manager",
        "Director",
        "VP & C-level",
    ]


# -- sync -------------------------------------------------------------------


async def _rows(session, source_id: int) -> dict[str, JobPosting]:
    rows = (
        await session.scalars(select(JobPosting).where(JobPosting.source_id == source_id))
    ).all()
    return {r.external_id: r for r in rows}


async def test_sync_opens_updates_closes(session, board, acmesec):
    src = await board()
    t1 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    t2, t3, t4 = t1 + timedelta(days=1), t1 + timedelta(days=2), t1 + timedelta(days=3)

    first = await sync_postings(
        session,
        src,
        [
            _posting(1, title="Senior Software Engineer, Supply Chain", department_raw="R&D"),
            _posting(2, title="Staff Security Researcher"),
            _posting(3, title="Account Executive, EMEA", department_raw="Sales"),
        ],
        complete=True,
        now=t1,
    )
    assert (first.new, first.updated, first.closed, first.open) == (3, 0, 0, 3)
    rows = await _rows(session, src.id)
    one = rows["job-1"]
    assert (one.department, one.seniority) == ("Engineering", "Senior")
    assert (rows["job-2"].department, rows["job-2"].seniority) == ("Security", "Staff & Principal")
    assert one.competitor_id == acmesec.id and one.ats == "greenhouse"
    assert one.first_seen_at == one.last_seen_at == t1 and one.closed_at is None
    assert one.tags == [] and one.content_hash
    for r in rows.values():  # the tagger has been over all three
        r.tags, r.tagged_at, r.tag_version = ["sca"], t1, "1"
    await session.flush()

    # snapshot 2 (complete): job-3 is gone, job-2's description changed, job-1 unchanged
    second = await sync_postings(
        session,
        src,
        [
            _posting(1, title="Senior Software Engineer, Supply Chain", department_raw="R&D"),
            _posting(2, title="Staff Security Researcher", description_text="New scope."),
        ],
        complete=True,
        now=t2,
    )
    assert (second.new, second.updated, second.closed, second.open) == (0, 1, 1, 2)
    rows = await _rows(session, src.id)
    assert rows["job-3"].closed_at == t2
    assert rows["job-2"].tags == [] and rows["job-2"].tagged_at is None
    assert rows["job-2"].tag_version is None and rows["job-2"].description_text == "New scope."
    assert rows["job-1"].tags == ["sca"]  # unchanged content keeps its tags
    assert rows["job-1"].first_seen_at == t1 and rows["job-1"].last_seen_at == t2

    # snapshot 3 is not complete (e.g. a partial listing): nothing closes
    third = await sync_postings(session, src, [_posting(1)], complete=False, now=t3)
    assert third.closed == 0 and third.open == 2
    rows = await _rows(session, src.id)
    assert rows["job-2"].closed_at is None and rows["job-2"].last_seen_at == t2
    assert rows["job-1"].last_seen_at == t3

    # a posting that comes back is open again, with its first sighting kept
    fourth = await sync_postings(
        session,
        src,
        [_posting(1), _posting(2, description_text="New scope."), _posting(3)],
        complete=True,
        now=t4,
    )
    assert fourth.open == 3 and fourth.new == 0 and fourth.closed == 0
    rows = await _rows(session, src.id)
    assert rows["job-3"].closed_at is None and rows["job-3"].first_seen_at == t1


async def test_sync_dedups_batch_and_keeps_known_posted_at(session, board):
    src = await board()
    t1 = datetime(2026, 10, 1, tzinfo=UTC)
    posted = datetime(2026, 9, 1, tzinfo=UTC)
    out = await sync_postings(
        session, src, [_posting(1, posted_at=posted), _posting(1)], complete=True, now=t1
    )
    assert out.new == 1 and out.open == 1
    await sync_postings(session, src, [_posting(1)], complete=True, now=t1 + timedelta(days=1))
    row = (await _rows(session, src.id))["job-1"]
    assert row.posted_at == posted  # a later listing without a date keeps the known one


async def test_empty_listing_closes_only_on_the_second_in_a_row(session, board):
    # one empty-but-successful listing may be an ATS glitch; a second one confirms it
    src = await board()
    t = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    day = timedelta(days=1)
    both = [_posting(1), _posting(2)]
    await sync_postings(session, src, both, complete=True, now=t)
    assert src.consecutive_empty == 0

    once = await sync_postings(session, src, [], complete=True, now=t + day)
    assert (once.closed, once.open) == (0, 2) and src.consecutive_empty == 1
    rows = await _rows(session, src.id)
    assert all(r.closed_at is None and r.last_seen_at == t for r in rows.values())

    # a listing with postings in between resets the streak
    back = await sync_postings(session, src, both, complete=True, now=t + 2 * day)
    assert back.closed == 0 and src.consecutive_empty == 0
    again = await sync_postings(session, src, [], complete=True, now=t + 3 * day)
    assert again.closed == 0 and src.consecutive_empty == 1

    twice = await sync_postings(session, src, [], complete=True, now=t + 4 * day)
    assert (twice.closed, twice.open) == (2, 0) and src.consecutive_empty == 2
    rows = await _rows(session, src.id)
    assert all(r.closed_at == t + 4 * day for r in rows.values())

    # an incomplete listing with postings also resets it
    await sync_postings(session, src, [_posting(3)], complete=False, now=t + 5 * day)
    assert src.consecutive_empty == 0


async def test_only_a_complete_listing_marks_the_board_listed_in_full(session, board):
    """A partial listing (a Greenhouse page short of its `meta.total`) stores
    its postings but measures nothing; the first complete listing is the board's first
    snapshot (`first_complete_fetch_at`) and later listings never move it. An empty complete
    listing is a complete one too: a board with no open role is a measured zero."""
    src = await board()
    t = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    day = timedelta(days=1)
    await sync_postings(session, src, [_posting(1)], complete=False, now=t)
    assert src.first_complete_fetch_at is None
    await sync_postings(session, src, [_posting(1), _posting(2)], complete=True, now=t + day)
    assert src.first_complete_fetch_at == t + day
    await sync_postings(session, src, [_posting(2)], complete=True, now=t + 2 * day)
    await sync_postings(session, src, [_posting(2)], complete=False, now=t + 3 * day)
    assert src.first_complete_fetch_at == t + day

    empty = await board("lever", url="https://api.lever.co/v0/postings/acme-empty?mode=json")
    await sync_postings(session, empty, [], complete=True, now=t)
    assert empty.first_complete_fetch_at == t


@pytest.mark.parametrize(
    ("location", "country"),
    [
        # two-letter codes that are both a country and a US state: never guessed as the US
        ("Tel Aviv, IL", "Israel"),
        ("Herzliya, IL", "Israel"),
        ("Berlin, DE", "Germany"),
        ("Bengaluru, IN", "India"),
        ("Pune, IN", "India"),
        ("Toronto, CA", "Canada"),
        ("Peoria, IL", None),  # an unknown city: unknown, not a guess
        ("Somewhere, DE", None),
        ("Chicago, IL", "United States"),
        ("San Francisco, CA", "United States"),
        ("Boston, MA", "United States"),
        ("Springfield, IL, USA", "United States"),
        # unambiguous states, names and codes
        ("Austin, TX", "United States"),
        ("New York, NY", "United States"),
        ("Remote - US", "United States"),
        ("Remote", None),
        ("Remote (EMEA)", None),
        ("London, UK", "United Kingdom"),
        ("London, United Kingdom", "United Kingdom"),
        ("Tel Aviv-Yafo, Israel", "Israel"),
        (None, None),
    ],
)
def test_country_of_locations(location, country):
    assert country_of(location) == country


@pytest.mark.parametrize(
    ("location", "country"),
    [
        # "CA" is California unless the place is a known Canadian one
        ("Santa Barbara, CA", "United States"),
        ("Remote, CA", "United States"),
        ("Toronto, CA", "Canada"),
        ("Vancouver, BC, CA", "Canada"),  # a province names Canada too
        ("Montreal, Quebec, CA", "Canada"),
        ("Waterloo, ON, CA", "Canada"),
        # a known city of a third country makes "CA" no US state: unknown
        ("Bogota, CA", None),
        ("Tel Aviv, CA", None),
        ("San Jose, CA", "United States"),  # a known US city stays the US
        # IL, IN and DE stay decided by the city: unknown without one
        ("Peoria, IL", None),
        ("Fort Wayne, IN", None),
        ("Somewhere, DE", None),
    ],
)
def test_ca_means_california_unless_the_place_is_canadian(location, country):
    assert country_of(location) == country


# -- failures close nothing ---------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    ["too_large", "http_500", "timeout", "truncated_json"],
)
@respx.mock
async def test_truncated_or_error_fetch_closes_nothing(
    failure, session, session_factory, board, make_polite_client, settings, monkeypatch
):
    src = await board("greenhouse")
    await sync_postings(
        session, src, [_posting(1), _posting(2)], complete=True, now=datetime.now(UTC)
    )
    await session.commit()

    route = respx.get(GH_URL)
    if failure == "too_large":
        monkeypatch.setattr(settings, "ats_max_bytes", 10_000)
        route.mock(return_value=httpx.Response(200, content=b'{"jobs": [' + b" " * 20_000))
    elif failure == "http_500":
        route.mock(return_value=httpx.Response(500, text="oops"))
    elif failure == "timeout":
        route.mock(side_effect=httpx.ConnectTimeout("timed out"))
    else:
        route.mock(return_value=httpx.Response(200, text=_fixture("greenhouse")[:400]))

    run_id = await run_ingestion(
        session_factory, source_ids=[src.id], http_factory=make_polite_client
    )

    async with session_factory() as s:
        run = await s.get(PipelineRun, run_id)
        fresh = await s.get(Source, src.id)
        rows = await _rows(s, src.id)
    assert run.stats["sources_failed"] == 1 and run.stats["postings_closed"] == 0
    assert all(r.closed_at is None for r in rows.values()) and len(rows) == 2
    assert fresh.consecutive_failures == 1 and fresh.last_error
    expected = {
        "too_large": "ResponseTooLarge",
        "http_500": "HTTP 500",
        "timeout": "FetchError",
        "truncated_json": "invalid JSON",
    }[failure]
    assert expected in fresh.last_error


# -- breaker ---------------------------------------------------------------------------------


@pytest.mark.parametrize("ats", ["greenhouse", "lever", "ashby"])
async def test_ats_hosts_use_api_pauses(ats):
    url = ADAPTERS[ats].url("acmesec")
    assert url == URLS[ats]  # the exact endpoint
    host = urlsplit(url).hostname
    assert host in API_HOSTS
    assert pause_duration(host, 1) == timedelta(hours=1)
    assert pause_duration(host, 2) == timedelta(hours=6)
    now = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
    guard = InMemoryHostGuard(lambda: now)
    await guard.record(host, 429)
    assert await guard.paused_until(host) == now + timedelta(hours=1)  # not the 23 h site pause
