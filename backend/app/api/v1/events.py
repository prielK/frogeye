"""Server-sent events: the Redis event bus (`app.core.events`) forwarded to the browser.

Frames are `event: {type}` + `data: {json}` where the JSON is the bus message
`{"type", "payload", "ts"}`; a `: ping` comment goes out after `ping_s` of silence. The
stream is resilient: if subscribing fails or the subscription drops, it keeps pinging
and re-subscribes with exponential backoff (1 s doubling to a 15 s cap). It never raises to
the client and ends when the client disconnects.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import events
from app.core.db import get_session
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

PING = ": ping\n\n"
PING_S = 15.0
BACKOFF_S = 1.0
MAX_BACKOFF_S = 15.0
POLL_S = 1.0  # how often a quiet stream checks whether the client is still there
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

Subscriber = Callable[[], AsyncIterator[dict[str, Any]]]


def backoff_delays(first: float, cap: float) -> Iterator[float]:
    """first, 2*first, 4*first, ... capped at `cap`, forever."""
    delay = first
    while True:
        yield min(delay, cap)
        delay = min(delay * 2, cap)


def format_event(msg: dict[str, Any]) -> str:
    # An SSE field ends at a line break, so the type is flattened to one line; the JSON
    # data is one line already (json.dumps escapes newlines).
    event_type = " ".join(str(msg.get("type") or "message").split())
    return f"event: {event_type}\ndata: {json.dumps(msg, default=str)}\n\n"


async def _next(stream: AsyncIterator[dict[str, Any]]) -> dict[str, Any]:
    return await anext(stream)


async def _close(
    pending: asyncio.Task | None, stream: AsyncIterator[dict[str, Any]] | None
) -> None:
    """Cancel an in-flight read and close the subscription; never raises."""
    try:
        if pending is not None:
            pending.cancel()
            await asyncio.wait({pending})
            if not pending.cancelled():
                pending.exception()  # retrieved: no "exception was never retrieved" noise
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()
    except Exception:
        logger.debug("events: error while closing a subscription", exc_info=True)


async def event_stream(
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    max_events: int | None = None,
    ping_s: float = PING_S,
    subscriber: Subscriber | None = None,
    backoff_s: float = BACKOFF_S,
    max_backoff_s: float = MAX_BACKOFF_S,
) -> AsyncIterator[str]:
    """SSE frames for one client until `is_disconnected()` (or `max_events` forwarded).

    `subscriber` defaults to `events.subscribe` (tests pass a fake). A read that is still
    waiting is never cancelled to send a ping, so a quiet subscription stays subscribed.
    """
    subscribe = subscriber or events.subscribe
    loop = asyncio.get_running_loop()
    tick = min(ping_s, POLL_S)
    delays = backoff_delays(backoff_s, max_backoff_s)
    last_frame = loop.time()
    sent = 0

    while not await is_disconnected():
        started = loop.time()
        stream: AsyncIterator[dict[str, Any]] | None = None
        pending: asyncio.Task | None = None
        try:
            stream = subscribe()
            while True:
                if pending is None:
                    pending = asyncio.create_task(_next(stream))
                done, _ = await asyncio.wait({pending}, timeout=tick)
                if done:
                    task, pending = pending, None
                    msg = task.result()  # raises if the subscription broke
                    if not isinstance(msg, dict):
                        logger.warning("events: dropped a non-object message")
                        continue
                    yield format_event(msg)
                    sent += 1
                    last_frame = loop.time()
                    delays = backoff_delays(backoff_s, max_backoff_s)  # healthy again
                    if max_events is not None and sent >= max_events:
                        return
                elif loop.time() - last_frame >= ping_s:
                    yield PING
                    last_frame = loop.time()
                if await is_disconnected():
                    return
        except Exception as exc:  # incl. StopAsyncIteration: an ended subscription is a drop
            logger.warning("events: subscription lost (%s); re-subscribing", type(exc).__name__)
            if loop.time() - started >= max_backoff_s:
                delays = backoff_delays(backoff_s, max_backoff_s)  # it was up a good while
        finally:
            await _close(pending, stream)

        deadline = loop.time() + next(delays)
        while (remaining := deadline - loop.time()) > 0:
            if await is_disconnected():
                return
            await asyncio.sleep(min(tick, remaining))
            if loop.time() - last_frame >= ping_s:
                yield PING
                last_frame = loop.time()


@router.get(
    "",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-sent events: `event: {type}` / `data: {type, payload, ts}`, "
            "plus `: ping` comments every 15 s.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_events(
    request: Request,
    _: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    # Auth is done: end its transaction so the long-lived stream holds no DB connection.
    await session.rollback()
    return StreamingResponse(
        event_stream(request.is_disconnected), media_type="text/event-stream", headers=SSE_HEADERS
    )
