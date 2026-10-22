import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.collectors import get_collector
from app.collectors.base import CollectorError
from app.collectors.edgar import EdgarCollector
from app.collectors.github_stats import GithubReleasesCollector, GithubStatsCollector

FIX = Path(__file__).parent.parent / "fixtures"
SEC_URL = "https://data.sec.gov/submissions/CIK0001800667.json"


def _read(rel: str) -> str:
    return (FIX / rel).read_text(encoding="utf-8")


def test_registered():
    assert isinstance(get_collector("github_repo_stats"), GithubStatsCollector)
    assert isinstance(get_collector("github_releases"), GithubReleasesCollector)
    assert isinstance(get_collector("sec_edgar"), EdgarCollector)


@respx.mock
async def test_github_stats_emits_metrics(make_source, polite_client, settings, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "tok123")
    src = await make_source(
        kind="github_repo_stats", url="https://api.github.com/repos/aquasecurity/trivy"
    )
    route = respx.get("https://api.github.com/repos/aquasecurity/trivy").mock(
        return_value=httpx.Response(200, text=_read("github/repo.json"))
    )
    res = await GithubStatsCollector().fetch(src, None, polite_client)
    assert {m.metric for m in res.metrics} == {
        "github_stars:aquasecurity/trivy",
        "github_forks:aquasecurity/trivy",
    }
    assert next(m for m in res.metrics if m.metric.startswith("github_stars")).value == 28000
    assert res.metrics[0].date == datetime.now(UTC).date()
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok123"


@respx.mock
async def test_github_stats_errors(make_source, polite_client, settings, monkeypatch):
    monkeypatch.setattr(settings, "github_token", None)
    src = await make_source(kind="github_repo_stats", url="https://api.github.com/repos/a/b")
    route = respx.get("https://api.github.com/repos/a/b")
    route.mock(return_value=httpx.Response(404))
    with pytest.raises(CollectorError, match="HTTP 404"):
        await GithubStatsCollector().fetch(src, None, polite_client)
    assert "Authorization" not in route.calls[0].request.headers
    route.mock(return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"}))
    with pytest.raises(CollectorError, match="rate limit"):
        await GithubStatsCollector().fetch(src, None, polite_client)


@respx.mock
async def test_github_releases(make_source, polite_client, settings, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "tok")
    src = await make_source(
        kind="github_releases", url="https://api.github.com/repos/aquasecurity/trivy/releases"
    )
    route = respx.get(
        "https://api.github.com/repos/aquasecurity/trivy/releases", params={"per_page": "20"}
    ).mock(return_value=httpx.Response(200, text=_read("github/releases.json")))
    res = await GithubReleasesCollector().fetch(src, None, polite_client)
    h = route.calls[0].request.headers
    assert h["Accept"] == "application/vnd.github+json"
    assert h["X-GitHub-Api-Version"] == "2022-11-28"
    assert h["Authorization"] == "Bearer tok"
    assert [i.external_id for i in res.items] == ["ghrel:101", "ghrel:102", "ghrel:100"]
    a, b = res.items[0], res.items[1]
    assert a.title == "aquasecurity/trivy v0.60.0"
    assert a.url.endswith("/releases/tag/v0.60.0")
    assert a.published_at == datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert a.raw_text.startswith("## Changes")
    assert a.payload == {"tag_name": "v0.60.0", "prerelease": False, "repo": "aquasecurity/trivy"}
    assert b.title == "aquasecurity/trivy v0.61.0-rc1"
    assert b.published_at == datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    assert b.payload["prerelease"] is True
    res = await GithubReleasesCollector().fetch(
        src, datetime(2026, 10, 1, tzinfo=UTC), polite_client
    )
    assert [i.external_id for i in res.items] == ["ghrel:101", "ghrel:102"]


@respx.mock
async def test_edgar_requires_user_agent(make_source, polite_client, settings, monkeypatch):
    monkeypatch.setattr(settings, "sec_user_agent", None)
    src = await make_source(kind="sec_edgar", url="x", config={"cik": "0001800667"})
    with pytest.raises(CollectorError, match="SEC_USER_AGENT"):
        await EdgarCollector().fetch(src, None, polite_client)


@pytest.mark.parametrize("ua", ["", "FrogEye Research you@example.com"])
async def test_edgar_rejects_empty_or_placeholder_user_agent(
    make_source, polite_client, settings, monkeypatch, ua
):
    monkeypatch.setattr(settings, "sec_user_agent", ua)
    src = await make_source(kind="sec_edgar", url="x", config={"cik": "0001800667"})
    with pytest.raises(CollectorError, match="not configured — set a real name and email"):
        await EdgarCollector().fetch(src, None, polite_client)


@respx.mock
async def test_edgar_filters_forms_and_builds_urls(
    make_source, polite_client, settings, monkeypatch
):
    monkeypatch.setattr(settings, "sec_user_agent", "FrogEye Research t@test.invalid")
    src = await make_source(kind="sec_edgar", url=SEC_URL, config={"cik": "0001800667"})
    route = respx.get(SEC_URL).mock(
        return_value=httpx.Response(200, text=_read("edgar/submissions.json"))
    )
    res = await EdgarCollector().fetch(src, datetime(2026, 8, 1, tzinfo=UTC), polite_client)
    assert [i.payload["form"] for i in res.items] == ["8-K", "10-Q"]
    first = res.items[0]
    assert first.external_id == "0001800667-26-000040"
    assert (
        first.url
        == "https://www.sec.gov/Archives/edgar/data/1800667/000180066726000040/frog-20260806.htm"
    )
    assert first.title == "JFrog Ltd 8-K: Current report (2026-08-06)"
    assert first.published_at == datetime(2026, 8, 6, tzinfo=UTC)
    assert first.payload == {"form": "8-K", "items": "2.02,9.01"}
    assert res.items[1].payload["items"] == ""
    assert route.calls[-1].request.headers["User-Agent"] == "FrogEye Research t@test.invalid"


@respx.mock
async def test_edgar_default_window_and_http_error(
    make_source, polite_client, settings, monkeypatch
):
    monkeypatch.setattr(settings, "sec_user_agent", "FrogEye Research t@test.invalid")
    src = await make_source(kind="sec_edgar", url=SEC_URL, config={"cik": "1800667"})
    data = json.loads(_read("edgar/submissions.json"))
    dates = data["filings"]["recent"]["filingDate"]
    dates[:] = ["2020-01-01"] * len(dates)
    dates[4] = datetime.now(UTC).date().isoformat()
    route = respx.get(SEC_URL).mock(return_value=httpx.Response(200, json=data))
    res = await EdgarCollector().fetch(src, None, polite_client)
    assert [i.external_id for i in res.items] == ["0001800667-26-000030"]
    route.mock(return_value=httpx.Response(500))
    with pytest.raises(CollectorError, match="HTTP 500"):
        await EdgarCollector().fetch(src, None, polite_client)
