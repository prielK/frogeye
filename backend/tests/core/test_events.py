import asyncio
import json

from app.core import events


async def test_events_publish_never_raises_when_redis_down(monkeypatch, caplog):
    class _S:
        redis_url = "redis://127.0.0.1:1/0"

    monkeypatch.setattr(events, "get_settings", lambda: _S())
    assert await events.publish("signal.breaking", {"signal_id": 1}) is False  # never raises
    assert any("publish" in r.message.lower() for r in caplog.records)


async def test_events_roundtrip():
    received = []

    async def consume():
        async for msg in events.subscribe():
            received.append(msg)
            return

    task = asyncio.create_task(consume())
    try:
        for _ in range(50):
            assert (
                await events.publish(
                    "briefing.published", {"date": "2026-10-18", "status": "ready"}
                )
                is True
            )
            await asyncio.sleep(0.1)
            if task.done():
                break
        await asyncio.wait_for(task, timeout=3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    msg = received[0]
    assert msg["type"] == "briefing.published"
    assert msg["payload"] == {"date": "2026-10-18", "status": "ready"}
    assert "ts" in msg
    json.dumps(msg)


def test_publish_works_across_event_loops():
    """Celery runs each task in a fresh asyncio.run; a loop-bound cached client would break."""
    assert asyncio.run(events.publish("run.progress", {"n": 1})) is True
    assert asyncio.run(events.publish("run.progress", {"n": 2})) is True
