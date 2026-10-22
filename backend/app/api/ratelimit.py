"""Per-user sliding-window rate limits for endpoints that spend LLM budget on every call."""

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, status


class SlidingWindowLimiter:
    """At most `limit` hits per `window_s` seconds per key (a user id).

    Each instance keeps its own buckets, so endpoints that hold separate instances are
    limited independently. In-memory: fine for a single API process.
    """

    def __init__(
        self,
        limit: int,
        window_s: float,
        *,
        detail: str = "Too many searches; slow down",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.detail = detail
        self._clock = clock
        self._hits: defaultdict[int, deque[float]] = defaultdict(deque)

    def hit(self, key: int) -> None:
        """Count one request for `key`; 429 with Retry-After when its window is full."""
        now = self._clock()
        window = self._hits[key]
        while window and now - window[0] >= self.window_s:
            window.popleft()
        if len(window) >= self.limit:
            retry = max(1, int(self.window_s - (now - window[0])) + 1)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, self.detail, headers={"Retry-After": str(retry)}
            )
        window.append(now)

    def reset(self) -> None:
        self._hits.clear()
