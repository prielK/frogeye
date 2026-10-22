from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.core.http import (
    BlockedAddress,
    FetchError,
    HostPaused,
    InMemoryHostGuard,
    PoliteClient,
    ResponseTooLarge,
    RobotsDisallowed,
)

ROBOTS_OK = httpx.Response(404)


def _client(settings, fake_time, **kw):
    from tests.conftest import FakeResolver

    kw.setdefault("resolver", FakeResolver())
    return PoliteClient(settings, clock=fake_time.clock, sleep=fake_time.sleep, **kw)


@respx.mock
async def test_conditional_get_sends_etag_and_handles_304(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get("https://a.test/feed").mock(return_value=httpx.Response(304))
    async with _client(settings, fake_time) as c:
        r = await c.get(
            "https://a.test/feed", etag='"v1"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT"
        )
    assert r.status == 304 and route.calls[0].request.headers["If-None-Match"] == '"v1"'
    assert route.calls[0].request.headers["If-Modified-Since"].startswith("Wed")


@respx.mock
async def test_retries_on_503_then_succeeds(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get("https://a.test/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, text="ok")]
    )
    async with _client(settings, fake_time) as c:
        assert (await c.get("https://a.test/x")).text == "ok"
    assert route.call_count == 3
    assert 1 in fake_time.slept and 2 in fake_time.slept


@respx.mock
async def test_exhausted_retries_return_final_status(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get("https://a.test/x").mock(return_value=httpx.Response(503))
    async with _client(settings, fake_time) as c:
        r = await c.get("https://a.test/x")
    assert r.status == 503 and route.call_count == 3


@respx.mock
async def test_non_retryable_returns_immediately(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get("https://a.test/x").mock(return_value=httpx.Response(404))
    async with _client(settings, fake_time) as c:
        assert (await c.get("https://a.test/x")).status == 404
    assert route.call_count == 1


@respx.mock
async def test_network_errors_exhaust_to_fetch_error(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get("https://a.test/x").mock(side_effect=httpx.ConnectTimeout("t"))
    async with _client(settings, fake_time) as c:
        with pytest.raises(FetchError):
            await c.get("https://a.test/x")
    assert route.call_count == 3


@respx.mock
async def test_honors_retry_after(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/x").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)]
    )
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/x")
    assert 7 in fake_time.slept


@respx.mock
async def test_retry_after_capped_at_30(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/x").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "999"}), httpx.Response(200)]
    )
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/x")
    assert 30 in fake_time.slept and 999 not in fake_time.slept


@respx.mock
async def test_robots_disallow_raises(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private")
    )
    async with _client(settings, fake_time) as c:
        with pytest.raises(RobotsDisallowed):
            await c.get("https://a.test/private/page")


@respx.mock
async def test_robots_fetched_once_and_skippable(settings, fake_time):
    robots = respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get(url__startswith="https://a.test/p").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/p1")
        await c.get("https://a.test/p2")
    assert robots.call_count == 1
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/p3", respect_robots=False)
    assert robots.call_count == 1


@respx.mock
async def test_user_agent_default_browser_and_override(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get("https://a.test/x").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/x")
        await c.get("https://a.test/x", browser_ua=True)
        await c.get("https://a.test/x", headers={"User-Agent": "SEC me@x.com"})
    uas = [call.request.headers["User-Agent"] for call in route.calls]
    assert settings.allow_browser_ua is True
    assert uas[0] == settings.http_user_agent
    assert "Mozilla/5.0" in uas[1] and "Chrome" in uas[1]
    assert uas[2] == "SEC me@x.com"


@respx.mock
async def test_browser_ua_ignored_when_setting_off(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get("https://a.test/x").mock(return_value=httpx.Response(200))
    off = settings.model_copy(update={"allow_browser_ua": False})
    async with _client(off, fake_time) as c:
        await c.get("https://a.test/x", browser_ua=True)
    assert route.calls[0].request.headers["User-Agent"] == settings.http_user_agent


@respx.mock
async def test_body_size_cap(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/big").mock(return_value=httpx.Response(200, content=b"x" * 3_000_000))
    async with _client(settings, fake_time) as c:
        with pytest.raises(ResponseTooLarge):
            await c.get("https://a.test/big")


@respx.mock
async def test_max_bytes_override_is_per_call(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/big").mock(return_value=httpx.Response(200, content=b"x" * 3_000_000))
    respx.get("https://a.test/small").mock(return_value=httpx.Response(200, content=b"x" * 500))
    async with _client(settings, fake_time) as c:
        assert len((await c.get("https://a.test/big", max_bytes=4_000_000)).content) == 3_000_000
        with pytest.raises(ResponseTooLarge):  # the client default still applies to others
            await c.get("https://a.test/big")
        with pytest.raises(ResponseTooLarge):  # and an override can also tighten one call
            await c.get("https://a.test/small", max_bytes=100)


@respx.mock
async def test_decodes_declared_charset(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/l1").mock(
        return_value=httpx.Response(
            200,
            content="café".encode("latin-1"),
            headers={"Content-Type": "text/html; charset=ISO-8859-1"},
        )
    )
    async with _client(settings, fake_time) as c:
        assert (await c.get("https://a.test/l1")).text == "café"


@respx.mock
async def test_per_host_min_interval(settings, fake_time):
    respx.get("https://api.gdeltproject.org/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get(url__startswith="https://api.gdeltproject.org/api").mock(
        return_value=httpx.Response(200)
    )
    async with _client(settings, fake_time) as c:
        await c.get("https://api.gdeltproject.org/api/v2/doc/doc?q=a")
        await c.get("https://api.gdeltproject.org/api/v2/doc/doc?q=b")
    assert any(s >= 15.0 for s in fake_time.slept)


def test_default_intervals():
    from app.core.http import DEFAULT_HOST_INTERVALS as D

    assert D["news.google.com"] == 10.0 and D["api.gdeltproject.org"] == 15.0


@respx.mock
async def test_cross_host_redirect_checks_target_robots(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://b.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private")
    )
    respx.get("https://a.test/go").mock(
        return_value=httpx.Response(302, headers={"Location": "https://b.test/private/x"})
    )
    target = respx.get("https://b.test/private/x").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time) as c:
        with pytest.raises(RobotsDisallowed):
            await c.get("https://a.test/go")
    assert target.call_count == 0


@respx.mock
async def test_redirect_to_rate_limited_host_waits_and_reports_final_url(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://api.gdeltproject.org/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/go").mock(
        return_value=httpx.Response(301, headers={"Location": "https://api.gdeltproject.org/api/r"})
    )
    respx.get("https://api.gdeltproject.org/api/r").mock(
        return_value=httpx.Response(302, headers={"Location": "/api/final"})
    )
    respx.get("https://api.gdeltproject.org/api/final").mock(
        return_value=httpx.Response(200, text="done")
    )
    async with _client(settings, fake_time) as c:
        r = await c.get("https://a.test/go", etag='"v1"')
    assert r.url == "https://api.gdeltproject.org/api/final" and r.text == "done"
    assert sum(1 for s in fake_time.slept if s >= 4.9) >= 2


@respx.mock
async def test_conditional_headers_only_on_first_hop(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/go").mock(
        return_value=httpx.Response(302, headers={"Location": "/dest"})
    )
    dest = respx.get("https://a.test/dest").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/go", etag='"v1"')
    assert "If-None-Match" not in dest.calls[0].request.headers


@respx.mock
async def test_too_many_redirects_raises(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/loop").mock(
        return_value=httpx.Response(302, headers={"Location": "/loop"})
    )
    async with _client(settings, fake_time) as c:
        with pytest.raises(FetchError):
            await c.get("https://a.test/loop")


@respx.mock
async def test_authorization_dropped_on_cross_host_redirect_only(settings, fake_time):
    respx.get("https://a.test/start").mock(
        return_value=httpx.Response(302, headers={"location": "https://b.test/dest"})
    )
    respx.get("https://a.test/same").mock(
        return_value=httpx.Response(302, headers={"location": "/same2"})
    )
    same2 = respx.get("https://a.test/same2").mock(return_value=httpx.Response(200))
    dest = respx.get("https://b.test/dest").mock(
        return_value=httpx.Response(302, headers={"location": "https://b.test/dest2"})
    )
    dest2 = respx.get("https://b.test/dest2").mock(return_value=httpx.Response(200))
    auth = {"authorization": "Bearer s3"}
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/same", headers=auth, respect_robots=False)
        await c.get("https://a.test/start", headers=auth, respect_robots=False)
    assert same2.calls[0].request.headers["Authorization"] == "Bearer s3"
    assert "Authorization" not in dest.calls[0].request.headers
    assert "Authorization" not in dest2.calls[0].request.headers


# -- SSRF guard ------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://10.0.0.5/admin",
        "http://0.0.0.0/",
        "ftp://a.test/file.txt",
        "file:///etc/passwd",
    ],
)
async def test_blocks_non_public_and_non_http_urls(settings, fake_time, url):
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200))
        async with _client(settings, fake_time) as c:
            with pytest.raises(BlockedAddress):
                await c.get(url)
            with pytest.raises(BlockedAddress):
                await c.get(url, respect_robots=False)
    assert route.call_count == 0  # nothing was sent, not even robots.txt


@respx.mock
async def test_redirect_to_private_address_is_blocked(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/go").mock(
        return_value=httpx.Response(302, headers={"Location": "http://10.0.0.5/admin"})
    )
    internal = respx.get("http://10.0.0.5/admin").mock(return_value=httpx.Response(200))
    internal_robots = respx.get("http://10.0.0.5/robots.txt").mock(return_value=ROBOTS_OK)
    async with _client(settings, fake_time) as c:
        with pytest.raises(BlockedAddress):
            await c.get("https://a.test/go")
    assert internal.call_count == 0 and internal_robots.call_count == 0


@respx.mock
async def test_hostname_resolving_to_any_private_address_is_blocked(settings, fake_time):
    from tests.conftest import PUBLIC_TEST_IP, FakeResolver

    resolver = FakeResolver()
    resolver.addresses["internal.test"] = ["10.1.2.3"]
    resolver.addresses["mixed.test"] = [PUBLIC_TEST_IP, "fd00::1"]
    resolver.addresses["meta.test"] = ["169.254.169.254"]
    routes = [
        respx.get(url__startswith=f"https://{h}/").mock(return_value=httpx.Response(200))
        for h in ("internal.test", "mixed.test", "meta.test")
    ]
    async with _client(settings, fake_time, resolver=resolver) as c:
        for host in ("internal.test", "mixed.test", "meta.test"):
            with pytest.raises(BlockedAddress):
                await c.get(f"https://{host}/x", respect_robots=False)
    assert all(r.call_count == 0 for r in routes)


@respx.mock
async def test_public_host_allowed_and_resolved_once(settings, fake_time):
    from tests.conftest import FakeResolver

    resolver = FakeResolver()
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get(url__startswith="https://a.test/p").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time, resolver=resolver) as c:
        assert (await c.get("https://a.test/p1")).status == 200
        assert (await c.get("https://a.test/p2")).status == 200
    assert route.call_count == 2 and resolver.calls == ["a.test"]


async def test_unresolvable_host_is_a_fetch_error(settings, fake_time):
    import socket

    async def failing(host, port):
        raise socket.gaierror(-2, "Name or service not known")

    async with _client(settings, fake_time, resolver=failing) as c:
        with pytest.raises(FetchError) as exc_info:
            await c.get("https://nowhere.test/x")
    assert not isinstance(exc_info.value, BlockedAddress)


async def test_system_resolver_blocks_localhost(settings, fake_time):
    from app.core.http import system_resolver

    assert "127.0.0.1" in await system_resolver("localhost", 80)
    async with PoliteClient(settings, clock=fake_time.clock, sleep=fake_time.sleep) as c:
        with pytest.raises(BlockedAddress):
            await c.get("http://localhost:8000/api/v1/health")


# -- SEC User-Agent ---------------------------------------------------


@respx.mock
async def test_sec_hosts_get_sec_user_agent(settings, fake_time):
    assert settings.sec_user_agent
    robots = respx.get("https://www.sec.gov/robots.txt").mock(return_value=ROBOTS_OK)
    doc = respx.get("https://www.sec.gov/Archives/edgar/x.htm").mock(
        return_value=httpx.Response(200)
    )
    other = respx.get("https://a.test/x").mock(return_value=httpx.Response(200))
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    async with _client(settings, fake_time) as c:
        await c.get("https://www.sec.gov/Archives/edgar/x.htm")
        await c.get("https://www.sec.gov/Archives/edgar/x.htm", browser_ua=True)
        await c.get("https://www.sec.gov/Archives/edgar/x.htm", headers={"user-agent": "Me x@y"})
        await c.get("https://a.test/x")
    uas = [call.request.headers["User-Agent"] for call in doc.calls]
    assert uas == [settings.sec_user_agent, settings.sec_user_agent, "Me x@y"]
    assert robots.calls[0].request.headers["User-Agent"] == settings.sec_user_agent
    assert other.calls[0].request.headers["User-Agent"] == settings.http_user_agent
    # a lower-case caller header replaces the default instead of duplicating it
    assert doc.calls[2].request.headers.get_list("User-Agent") == ["Me x@y"]


@respx.mock
async def test_redirect_to_sec_switches_user_agent(settings, fake_time):
    respx.get("https://a.test/filing").mock(
        return_value=httpx.Response(302, headers={"Location": "https://www.sec.gov/f.htm"})
    )
    dest = respx.get("https://www.sec.gov/f.htm").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/filing", respect_robots=False)
    assert dest.calls[0].request.headers["User-Agent"] == settings.sec_user_agent


async def test_sec_user_agent_falls_back_when_unset(settings, fake_time):
    unset = settings.model_copy(update={"sec_user_agent": None})
    c = _client(unset, fake_time)
    assert c._default_user_agent("https://www.sec.gov/x") == settings.http_user_agent


# -- transient errors and 429 backoff ---------------------------------


@respx.mock
async def test_remote_protocol_error_is_retried(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    route = respx.get("https://a.test/x").mock(
        side_effect=[
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            httpx.Response(200, text="ok"),
        ]
    )
    async with _client(settings, fake_time) as c:
        assert (await c.get("https://a.test/x")).text == "ok"
    assert route.call_count == 2


@respx.mock
async def test_429_without_retry_after_backs_off_twice_the_host_interval(settings, fake_time):
    route = respx.get(url__startswith="https://api.gdeltproject.org/api").mock(
        side_effect=[httpx.Response(429), httpx.Response(200)]
    )
    async with _client(settings, fake_time) as c:
        await c.get("https://api.gdeltproject.org/api/v2/doc/doc?q=a", respect_robots=False)
    assert route.call_count == 2
    assert max(fake_time.slept) >= 30.0  # GDELT interval 15 s x 2


@respx.mock
async def test_429_backoff_on_default_host(settings, fake_time):
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    respx.get("https://a.test/limited").mock(side_effect=[httpx.Response(429), httpx.Response(200)])
    async with _client(settings, fake_time) as c:
        await c.get("https://a.test/limited")
    assert 2.0 in fake_time.slept  # 2 x the 1 s default interval beats the 1 s backoff


@pytest.mark.parametrize(
    "address,public",
    [
        ("93.184.216.34", True),
        ("2606:4700::1111", True),
        ("::ffff:93.184.216.34", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.1.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("240.0.0.1", False),
        ("::1", False),
        ("::", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("ff02::1", False),
        ("::ffff:10.0.0.1", False),
        ("2002:7f00:1::1", False),  # 6to4 wrapping 127.0.0.1
    ],
)
def test_public_address_classification(address, public):
    import ipaddress

    from app.core.http import _is_public_ip

    assert _is_public_ip(ipaddress.ip_address(address)) is public


# -- per-host circuit breaker ---------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def test_guard_website_schedule_escalation_and_reset():
    clk = _Clock()
    g = InMemoryHostGuard(clk)
    assert await g.paused_until("a.test") is None
    await g.record("a.test", 403)
    assert await g.paused_until("a.test") == clk.now + timedelta(hours=23)
    clk.now += timedelta(hours=25)
    assert await g.paused_until("a.test") is None  # pause expired, strike remembered
    await g.record("a.test", 429)
    assert g.states["a.test"].strikes == 2
    assert await g.paused_until("a.test") == clk.now + timedelta(days=7)
    clk.now += timedelta(days=8)
    await g.record("a.test", 500)  # other statuses change nothing
    assert g.states["a.test"].strikes == 2
    await g.record("a.test", 200)
    assert g.states["a.test"].strikes == 0 and await g.paused_until("a.test") is None


async def test_guard_api_schedule_and_google_news_is_website():
    clk = _Clock()
    g = InMemoryHostGuard(clk)
    await g.record("api.github.com", 403)
    assert await g.paused_until("api.github.com") == clk.now + timedelta(hours=1)
    clk.now += timedelta(hours=2)
    await g.record("api.github.com", 429)
    assert await g.paused_until("api.github.com") == clk.now + timedelta(hours=6)
    await g.record("news.google.com", 429)
    assert await g.paused_until("news.google.com") == clk.now + timedelta(hours=23)
    await g.record("hn.algolia.com", 304)  # 304 counts as success
    assert g.states["hn.algolia.com"].strikes == 0


async def test_guard_block_while_paused_does_not_escalate():
    g = InMemoryHostGuard(_Clock())
    await g.record("a.test", 403)
    await g.record("a.test", 403)  # in-flight request answered after the breaker opened
    assert g.states["a.test"].strikes == 1


@respx.mock
async def test_paused_host_makes_no_request(settings, fake_time):
    clk = _Clock()
    guard = InMemoryHostGuard(clk)
    await guard.record("a.test", 403)
    robots = respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    page = respx.get("https://a.test/x").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time, guard=guard) as c:
        with pytest.raises(HostPaused, match=r"host a\.test paused until .* after HTTP 403"):
            await c.get("https://a.test/x")
        with pytest.raises(HostPaused):
            await c.get("https://a.test/x", respect_robots=False)
    assert robots.call_count == 0 and page.call_count == 0


@respx.mock
async def test_403_after_retries_records_strike_and_blocks_next(settings, fake_time):
    guard = InMemoryHostGuard(_Clock())
    route = respx.get("https://a.test/x").mock(return_value=httpx.Response(429))
    async with _client(settings, fake_time, guard=guard) as c:
        resp = await c.get("https://a.test/x", respect_robots=False)
        assert resp.status == 429 and route.call_count == 3  # retried, then one strike
        assert guard.states["a.test"].strikes == 1
        with pytest.raises(HostPaused):
            await c.get("https://a.test/x", respect_robots=False)
    assert route.call_count == 3


@respx.mock
async def test_403_records_and_success_resets(settings, fake_time):
    guard = InMemoryHostGuard(_Clock())
    respx.get("https://a.test/x").mock(side_effect=[httpx.Response(403), httpx.Response(200)])
    async with _client(settings, fake_time, guard=guard) as c:
        await c.get("https://a.test/x", respect_robots=False)
        assert guard.states["a.test"].strikes == 1
        guard.states["a.test"].blocked_until = None  # pause elapsed
        await c.get("https://a.test/x", respect_robots=False)
    assert guard.states["a.test"].strikes == 0


@respx.mock
async def test_robots_403_pauses_host_before_page_fetch(settings, fake_time):
    guard = InMemoryHostGuard(_Clock())
    respx.get("https://a.test/robots.txt").mock(return_value=httpx.Response(403))
    page = respx.get("https://a.test/x").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time, guard=guard) as c:
        with pytest.raises(HostPaused):
            await c.get("https://a.test/x")
    assert page.call_count == 0


@respx.mock
async def test_queued_requests_recheck_breaker_after_first_final_429(settings, fake_time):
    import asyncio

    guard = InMemoryHostGuard(_Clock())
    route = respx.get(url__startswith="https://a.test/x").mock(return_value=httpx.Response(429))
    async with _client(settings, fake_time, guard=guard) as c:
        results = await asyncio.gather(
            c.get("https://a.test/x1", respect_robots=False),
            c.get("https://a.test/x2", respect_robots=False),
            return_exceptions=True,
        )
    paused = [r for r in results if isinstance(r, HostPaused)]
    assert len(paused) == 1  # the second request never reached the network
    assert route.call_count == 3  # only the first request's own attempts


@respx.mock
async def test_robots_load_reraises_host_paused_and_does_not_cache(settings, fake_time):
    guard = InMemoryHostGuard(_Clock())
    await guard.record("a.test", 403)
    respx.get("https://a.test/robots.txt").mock(return_value=ROBOTS_OK)
    page = respx.get("https://a.test/x").mock(return_value=httpx.Response(200))
    async with _client(settings, fake_time, guard=guard) as c:
        with pytest.raises(HostPaused):
            await c.get("https://a.test/x")
        assert "a.test" not in c._robots  # not cached as "allow all"
        guard.states["a.test"].blocked_until = None
        await c.get("https://a.test/x")
    assert page.call_count == 1


@respx.mock
async def test_host_lock_held_across_retries_so_queued_request_is_paused(settings, fake_time):
    import asyncio

    async def yielding_sleep(seconds: float) -> None:
        await fake_time.sleep(seconds)
        await asyncio.sleep(0)  # let other tasks run during backoff, like a real sleep

    guard = InMemoryHostGuard(_Clock())
    route = respx.get(url__startswith="https://a.test/x").mock(return_value=httpx.Response(429))
    c = PoliteClient(
        settings,
        clock=fake_time.clock,
        sleep=yielding_sleep,
        resolver=__import__("tests.conftest", fromlist=["FakeResolver"]).FakeResolver(),
        guard=guard,
    )
    async with c:
        results = await asyncio.gather(
            c.get("https://a.test/x1", respect_robots=False),
            c.get("https://a.test/x2", respect_robots=False),
            return_exceptions=True,
        )
    assert isinstance(results[0], type(results[0])) and getattr(results[0], "status", None) == 429
    assert isinstance(results[1], HostPaused)
    assert route.call_count == 3  # every attempt belonged to the first request
