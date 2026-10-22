"""Polite HTTP client: robots.txt, per-host rate limits, retries, size cap, charset decoding.

Every outbound HTTP request in the app goes through ``PoliteClient``.
"""

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx
from protego import Protego

from app.core.config import Settings

DEFAULT_HOST_INTERVALS: dict[str, float] = {
    "news.google.com": 10.0,
    "api.gdeltproject.org": 15.0,
    "data.sec.gov": 0.15,
    "www.sec.gov": 0.15,
    "hn.algolia.com": 0.2,
}

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

RETRY_STATUSES = frozenset({429, 502, 503, 504})
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
MAX_RETRY_AFTER = 30.0
REQUEST_TIMEOUT = 20.0
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
ALLOWED_SCHEMES = frozenset({"http", "https"})
# Transient network failures worth another attempt (a dropped keep-alive shows up as
# RemoteProtocolError: "Server disconnected without sending a response").
RETRY_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


class ResponseTooLarge(FetchError):
    pass


class BlockedAddress(FetchError):
    """The URL is not a public http(s) address (SSRF guard)."""


class HostPaused(FetchError):
    """The per-host circuit breaker is open: no network call was made."""


# -- per-host circuit breaker --------------------------------------------------------

API_HOSTS = frozenset(
    {
        "api.gdeltproject.org",
        "api.github.com",
        "hn.algolia.com",
        # public job-board (ATS) APIs
        "boards-api.greenhouse.io",
        "api.lever.co",
        "api.ashbyhq.com",
    }
)
BLOCK_STATUSES = frozenset({403, 429})
# strike 1 / strike >= 2
# 23 h, not 24: a host that blocked the daily 06:00 run is retried at the next one
WEBSITE_PAUSES = (timedelta(hours=23), timedelta(days=7))
API_PAUSES = (timedelta(hours=1), timedelta(hours=6))


def pause_duration(host: str, strikes: int) -> timedelta:
    """How long ``host`` is paused after its ``strikes``-th consecutive block response."""
    first, repeat = API_PAUSES if host in API_HOSTS else WEBSITE_PAUSES
    return first if strikes <= 1 else repeat


@dataclass
class HostState:
    strikes: int = 0
    blocked_until: datetime | None = None
    last_status: int | None = None


def apply_status(host: str, state: HostState, status: int, now: datetime) -> HostState:
    """The state after ``host`` answered ``status``: 403/429 strike, 2xx/304 reset, else same.

    A block response that arrives while the host is already paused (another request was in
    flight when the breaker opened) refreshes ``last_status`` but does not escalate.
    """
    if status in BLOCK_STATUSES:
        if state.blocked_until is not None and state.blocked_until > now:
            return HostState(state.strikes, state.blocked_until, status)
        strikes = state.strikes + 1
        return HostState(strikes, now + pause_duration(host, strikes), status)
    if 200 <= status < 300 or status == 304:
        return HostState(0, None, status)
    return state


class HostGuard(Protocol):
    """Persistent per-host breaker consulted before every network request."""

    async def paused_until(self, host: str) -> datetime | None:
        """When the pause on ``host`` ends, or None if requests are allowed."""
        ...

    async def record(self, host: str, status: int) -> None:
        """Note the final response status of a request to ``host``."""
        ...


class InMemoryHostGuard:
    """Non-persistent guard (tests, one-off scripts)."""

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._now = now
        self.states: dict[str, HostState] = {}

    async def paused_until(self, host: str) -> datetime | None:
        state = self.states.get(host)
        if state is None or state.blocked_until is None or state.blocked_until <= self._now():
            return None
        return state.blocked_until

    async def last_status(self, host: str) -> int | None:
        state = self.states.get(host)
        return state.last_status if state else None

    async def record(self, host: str, status: int) -> None:
        state = self.states.get(host, HostState())
        self.states[host] = apply_status(host, state, status, self._now())


async def system_resolver(host: str, port: int) -> list[str]:
    """All addresses ``host`` resolves to, via the event loop's getaddrinfo."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:  # ::ffff:a.b.c.d is the IPv4 address a.b.c.d
            return _is_public_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None and not _is_public_ip(ip.sixtofour):
            return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global  # e.g. 100.64.0.0/10 shared address space
    )


def _is_sec_host(host: str) -> bool:
    return host == "sec.gov" or host.endswith(".sec.gov")


@dataclass
class HttpResponse:
    status: int
    url: str
    headers: dict[str, str]
    content: bytes
    text: str


def _decode(content: bytes, response: httpx.Response) -> str:
    encoding = response.charset_encoding or response.encoding or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def _retry_delay(
    attempt: int, retry_after: str | None, *, status: int | None = None, host_interval: float = 0
) -> float:
    """Delay before retry number ``attempt`` (1-based).

    A 429 without Retry-After waits at least twice the host's minimum interval: the host has
    said we are too fast, so retrying at the normal pace would just be refused again.
    """
    if retry_after is not None:
        try:
            return min(float(int(retry_after.strip())), MAX_RETRY_AFTER)
        except ValueError:
            pass
    delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1]
    if status == 429:
        delay = max(delay, 2 * host_interval)
    return min(delay, MAX_RETRY_AFTER)


def _drop(headers: dict[str, str], name: str) -> None:
    """Remove header ``name`` (lower-case) whatever its spelling."""
    for k in [k for k in headers if k.lower() == name]:
        del headers[k]


class PoliteClient:
    def __init__(
        self,
        settings: Settings,
        *,
        min_interval: float = 1.0,
        host_intervals: dict[str, float] | None = None,
        max_bytes: int = 2_000_000,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        resolver: Resolver = system_resolver,
        guard: HostGuard | None = None,
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._resolver = resolver
        self._public_hosts: set[str] = set()
        self._min_interval = min_interval
        self._host_intervals = DEFAULT_HOST_INTERVALS if host_intervals is None else host_intervals
        self._max_bytes = max_bytes
        self._clock = clock
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_start: dict[str, float] = {}
        self._robots: dict[str, Protego | None] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> "PoliteClient":
        self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        respect_robots: bool = True,
        browser_ua: bool = False,
        max_bytes: int | None = None,
    ) -> HttpResponse:
        """GET ``url`` politely. ``max_bytes`` overrides the client's body cap for this call
        only (every hop of it); other calls and the robots.txt fetch keep the default."""
        base: dict[str, str] = {}
        if etag:
            base["If-None-Match"] = etag
        if last_modified:
            base["If-Modified-Since"] = last_modified
        caller_ua: str | None = None
        for k, v in (headers or {}).items():
            if k.lower() == "user-agent":
                caller_ua = v  # a caller-supplied User-Agent always wins
                continue
            _drop(base, k.lower())  # header names are case-insensitive: no duplicates
            base[k] = v

        # Redirects are followed manually so every hop gets its own address check, robots
        # check, User-Agent choice, per-host rate limiting and retries.
        for hop in range(MAX_REDIRECTS + 1):
            await self._check_url(url)
            user_agent = caller_ua or self._default_user_agent(url, browser_ua=browser_ua)
            hdrs = {"User-Agent": user_agent, **base}
            if respect_robots:
                await self._check_robots(url, user_agent)
            resp = await self._fetch(url, hdrs, max_bytes=max_bytes)
            location = resp.headers.get("location")
            if resp.status not in REDIRECT_STATUSES or not location:
                return resp
            if hop == MAX_REDIRECTS:
                break
            next_url = urljoin(url, location)
            if urlsplit(next_url).netloc != urlsplit(url).netloc:
                # Credential-leak guard: never forward Authorization cross-host.
                _drop(base, "authorization")
            url = next_url
            _drop(base, "if-none-match")
            _drop(base, "if-modified-since")
        raise FetchError(f"{url}: more than {MAX_REDIRECTS} redirects")

    # -- internals -------------------------------------------------------

    def _default_user_agent(self, url: str, *, browser_ua: bool = False) -> str:
        host = (urlsplit(url).hostname or "").lower()
        if _is_sec_host(host) and self._settings.sec_user_agent:
            # SEC's fair-access policy requires a declared UA with contact details.
            return self._settings.sec_user_agent
        # Browser UA only for sources that ask for it (bot-blocking vendor feeds).
        if browser_ua and self._settings.allow_browser_ua:
            return BROWSER_UA
        return self._settings.http_user_agent

    async def _check_url(self, url: str) -> None:
        """SSRF guard: only http(s), and only hosts whose every address is public."""
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise BlockedAddress(f"{url}: scheme {scheme or '(none)'} is not allowed")
        host = (parts.hostname or "").lower()
        if not host:
            raise BlockedAddress(f"{url}: no host")
        if host in self._public_hosts:
            return
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                port = parts.port or (443 if scheme == "https" else 80)
            except ValueError as exc:  # out-of-range port
                raise BlockedAddress(f"{url}: invalid port") from exc
            try:
                resolved = await self._resolver(host, port)
            except OSError as exc:  # socket.gaierror and friends
                raise FetchError(f"{url}: cannot resolve {host}: {exc}") from exc
            try:
                addresses = [ipaddress.ip_address(a.split("%", 1)[0]) for a in resolved]
            except ValueError as exc:
                raise BlockedAddress(f"{url}: unparseable address for {host}") from exc
        if not addresses:
            raise FetchError(f"{url}: {host} did not resolve")
        blocked = [str(a) for a in addresses if not _is_public_ip(a)]
        if blocked:
            raise BlockedAddress(f"{url}: {host} resolves to non-public {', '.join(blocked)}")
        self._public_hosts.add(host)

    async def _check_robots(self, url: str, user_agent: str) -> None:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            lock = self._robots_locks.setdefault(host, asyncio.Lock())
            async with lock:
                if host not in self._robots:
                    self._robots[host] = await self._load_robots(parts.scheme, host)
        parser = self._robots[host]
        if parser is not None and not parser.can_fetch(url, user_agent):
            raise RobotsDisallowed(f"robots.txt disallows {url}")

    async def _load_robots(self, scheme: str, host: str) -> Protego | None:
        url = f"{scheme}://{host}/robots.txt"
        await self._check_url(url)  # same host as the page, but never fetch unchecked
        try:
            resp = await self._fetch(url, {"User-Agent": self._default_user_agent(url)})
        except HostPaused:
            raise  # never cache "allow all" because the breaker was open
        except FetchError:
            return None
        if resp.status != 200:
            return None
        return Protego.parse(resp.text)

    async def _fetch(
        self, url: str, headers: dict[str, str], *, max_bytes: int | None = None
    ) -> HttpResponse:
        """One network exchange, gated by the per-host circuit breaker.

        The host lock is held across the whole retry sequence (attempts and backoff sleeps) and
        the final status is recorded before it is released: same-host requests queue behind a
        struggling one and, if it trips the breaker, find the host paused and send nothing.
        """
        host = (urlsplit(url).hostname or "").lower()
        async with self._locks.setdefault(host, asyncio.Lock()):
            await self._raise_if_paused(host)
            cap = self._max_bytes if max_bytes is None else max_bytes
            resp = await self._fetch_with_retries(url, headers, cap)
            if self._guard is not None:
                await self._guard.record(host, resp.status)
            return resp

    async def _raise_if_paused(self, host: str) -> None:
        guard = self._guard
        if guard is None:
            return
        until = await guard.paused_until(host)
        if until is not None:
            last = getattr(guard, "last_status", None)
            status = await last(host) if last else None
            after = f"HTTP {status}" if status else "a block response"
            raise HostPaused(f"host {host} paused until {until.isoformat()} after {after}")

    async def _fetch_with_retries(
        self, url: str, headers: dict[str, str], max_bytes: int
    ) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            retry_after: str | None = None
            status: int | None = None
            try:
                resp = await self._send(url, headers, max_bytes)
            except RETRY_EXCEPTIONS as exc:
                last_error = exc
            except httpx.HTTPError as exc:
                raise FetchError(f"{url}: {exc!r}") from exc
            else:
                if resp.status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    return resp
                retry_after = resp.headers.get("retry-after")
                status = resp.status
            if attempt == MAX_ATTEMPTS:
                break
            delay = _retry_delay(
                attempt, retry_after, status=status, host_interval=self._interval_for(url)
            )
            await self._sleep(delay)
        raise FetchError(f"{url}: giving up after {MAX_ATTEMPTS} attempts: {last_error!r}")

    def _interval_for(self, url: str) -> float:
        return self._host_intervals.get(urlsplit(url).hostname or "", self._min_interval)

    async def _send(self, url: str, headers: dict[str, str], max_bytes: int) -> HttpResponse:
        assert self._client is not None, "PoliteClient must be used as an async context manager"
        host = urlsplit(url).hostname or ""
        interval = self._interval_for(url)
        last = self._last_start.get(host)
        if last is not None:
            wait = interval - (self._clock() - last)
            if wait > 0:
                await self._sleep(wait)
        self._last_start[host] = self._clock()

        async with self._client.stream("GET", url, headers=headers) as response:
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ResponseTooLarge(f"{url}: content-length {declared} > cap")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ResponseTooLarge(f"{url}: body exceeds {max_bytes} bytes")
                chunks.append(chunk)
            content = b"".join(chunks)
            return HttpResponse(
                status=response.status_code,
                url=str(response.url),
                headers=dict(response.headers),
                content=content,
                text=_decode(content, response),
            )
