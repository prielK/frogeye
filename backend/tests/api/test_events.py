"""GET /events (SSE) and its generator. ASGITransport buffers whole responses, so the
infinite stream is exercised by calling `event_stream` directly."""

import asyncio
import json
from itertools import islice

from app.api.v1 import events as events_api
from app.api.v1.events import backoff_delays, event_stream
from app.core import events
from app.main import app


def _never_disconnected():
    async def _check() -> bool:
        return False

    return _check


async def _first(gen, timeout: float = 5.0) -> str:
    return await asyncio.wait_for(anext(gen), timeout)


async def test_events_requires_auth(client, seeded):
    r = await client.get("/api/v1/events")
    assert r.status_code == 401


async def test_events_stream_forwards_published_event():
    gen = event_stream(_never_disconnected(), max_events=1)
    first = asyncio.ensure_future(anext(gen))
    try:
        # Pub/sub keeps no history: publish until the stream has subscribed and forwards one.
        for _ in range(50):
            assert await events.publish("signal.breaking", {"signal_id": 7}) is True
            done, _ = await asyncio.wait({first}, timeout=0.1)
            if done:
                break
        frame = await asyncio.wait_for(first, 3)
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await gen.aclose()
    head, data, blank = frame.split("\n", 2)
    assert head == "event: signal.breaking"
    assert data.startswith("data: ") and blank == "\n"
    msg = json.loads(data.removeprefix("data: "))
    assert msg["type"] == "signal.breaking" and msg["payload"] == {"signal_id": 7}
    assert "ts" in msg


class FakeBus:
    """`subscriber` stand-in: each call to the instance is one subscription attempt, played
    from `plans` -- a list of items per attempt, where an Exception item is raised and the
    string "hang" blocks forever (an idle but healthy subscription)."""

    def __init__(self, *plans: list) -> None:
        self.plans = list(plans)
        self.attempts = 0
        self.closed = 0

    def __call__(self):
        plan = self.plans[self.attempts] if self.attempts < len(self.plans) else ["hang"]
        self.attempts += 1
        return self._run(plan)

    async def _run(self, plan):
        try:
            for item in plan:
                if isinstance(item, Exception):
                    raise item
                if item == "hang":
                    await asyncio.Event().wait()
                yield item
        finally:
            self.closed += 1


def _msg(n: int) -> dict:
    return {"type": "run.progress", "payload": {"n": n}, "ts": "2026-10-18T06:00:00+00:00"}


async def test_events_sends_ping_when_idle():
    bus = FakeBus(["hang"])
    gen = event_stream(_never_disconnected(), ping_s=0.05, subscriber=bus)
    try:
        assert await _first(gen) == ": ping\n\n"
        assert await _first(gen) == ": ping\n\n"
    finally:
        await gen.aclose()
    assert bus.attempts == 1 and bus.closed == 1  # one subscription, closed with the stream


async def test_events_resubscribe_after_failed_subscribe():
    """A failing subscribe never reaches the client; pings continue and it retries."""
    bus = FakeBus(
        [ConnectionError("redis down")],
        [ConnectionError("still down")],
        [_msg(1)],
    )
    gen = event_stream(
        _never_disconnected(), ping_s=0.01, subscriber=bus, backoff_s=0.03, max_backoff_s=0.05
    )
    frames = []
    try:
        while not frames or frames[-1].startswith(":"):
            frames.append(await _first(gen))
    finally:
        await gen.aclose()
    assert bus.attempts == 3
    assert frames[:-1] and set(frames[:-1]) == {": ping\n\n"}  # pings while Redis is down
    assert frames[-1].startswith("event: run.progress\ndata: ")
    assert json.loads(frames[-1].split("data: ", 1)[1])["payload"] == {"n": 1}


async def test_events_resubscribe_after_dropped_subscription():
    bus = FakeBus([_msg(1), ConnectionError("connection lost")], [_msg(2)])
    gen = event_stream(_never_disconnected(), ping_s=1.0, subscriber=bus, backoff_s=0.01)
    try:
        first = await _first(gen)
        second = await _first(gen)
    finally:
        await gen.aclose()
    assert '"n": 1' in first and '"n": 2' in second
    assert bus.attempts == 2 and bus.closed == 2


async def test_events_subscription_ending_is_retried():
    bus = FakeBus([], [_msg(3)])  # a subscription that just ends counts as a drop
    gen = event_stream(_never_disconnected(), subscriber=bus, backoff_s=0.01)
    try:
        assert '"n": 3' in await _first(gen)
    finally:
        await gen.aclose()


async def test_events_stops_when_client_disconnects():
    checks = 0

    async def disconnected() -> bool:
        nonlocal checks
        checks += 1
        return checks > 3

    bus = FakeBus(["hang"])
    frames = [f async for f in event_stream(disconnected, ping_s=0.01, subscriber=bus)]
    assert all(f == ": ping\n\n" for f in frames)
    assert bus.closed == bus.attempts  # the subscription is torn down on the way out


async def test_events_stops_during_backoff_when_client_disconnects():
    calls = 0

    async def disconnected() -> bool:
        nonlocal calls
        calls += 1
        return calls > 4

    bus = FakeBus(*([ConnectionError("down")] for _ in range(100)))
    frames = [
        f
        async for f in event_stream(
            disconnected, ping_s=0.01, subscriber=bus, backoff_s=10.0, max_backoff_s=10.0
        )
    ]  # would take 10 s per retry if the backoff wait ignored the disconnect
    assert bus.attempts == 1 and all(f == ": ping\n\n" for f in frames)


async def test_events_max_events():
    bus = FakeBus([_msg(1), _msg(2), _msg(3)])
    frames = [f async for f in event_stream(_never_disconnected(), max_events=2, subscriber=bus)]
    assert len(frames) == 2 and bus.closed == 1


def test_backoff_delays_double_to_a_cap():
    assert list(islice(backoff_delays(1.0, 15.0), 7)) == [1.0, 2.0, 4.0, 8.0, 15.0, 15.0, 15.0]


async def test_events_endpoint_headers_and_releases_session(
    auth_client, seeded, session_factory, monkeypatch
):
    """The route streams `event_stream` with SSE headers, and the auth transaction is over
    before the (long-lived) stream starts."""
    from app.core.db import get_session

    sessions, seen = [], []

    async def _override():
        async with session_factory() as s:
            sessions.append(s)
            yield s

    async def finite_stream(is_disconnected, **_):
        seen.append([s.in_transaction() for s in sessions])
        yield ": ping\n\n"
        yield 'event: x\ndata: {"type": "x"}\n\n'

    monkeypatch.setattr(events_api, "event_stream", finite_stream)
    c = await auth_client("sales")
    app.dependency_overrides[get_session] = _override
    sessions.clear()
    r = await c.get("/api/v1/events")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"
    assert r.text == ': ping\n\nevent: x\ndata: {"type": "x"}\n\n'
    assert seen and sessions and seen == [[False] * len(sessions)]
