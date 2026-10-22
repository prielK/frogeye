"""Redis pub/sub events bus (channel `frogeye:events`).

Message shape: {"type", "payload", "ts"}. Publishing never raises: a down Redis is logged and
the run carries on (socket timeouts are <= 1s so it cannot stall it).
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import redis.asyncio as aioredis

from app.core.config import get_settings

log = logging.getLogger(__name__)

CHANNEL = "frogeye:events"


async def publish(event_type: str, payload: dict) -> bool:
    """Publish one event; True if sent to Redis, False on any failure (never raises).

    A short-lived client per call keeps this correct across event loops (each Celery task runs
    in its own asyncio.run) and leaks nothing; events are a few dozen per run.
    """
    try:
        msg = {"type": event_type, "payload": payload, "ts": datetime.now(UTC).isoformat()}
        data = json.dumps(msg, default=str)
        async with aioredis.from_url(
            get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1
        ) as client:
            await client.publish(CHANNEL, data)
        return True
    except Exception as exc:
        log.warning("events publish failed for %s: %s", event_type, type(exc).__name__)
        return False


async def subscribe() -> AsyncIterator[dict]:
    # Dedicated client without a read timeout: listening blocks by design.
    client = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=1)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(CHANNEL)
        while True:
            m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if m is None:
                continue
            try:
                yield json.loads(m["data"])
            except ValueError:
                log.warning("events: dropped malformed message")
    finally:
        await pubsub.aclose()
        await client.aclose()
