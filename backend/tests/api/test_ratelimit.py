import pytest
from fastapi import HTTPException

from app.api.ratelimit import SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limit_then_429_with_retry_after():
    clock = Clock()
    limiter = SlidingWindowLimiter(2, 60.0, clock=clock)
    limiter.hit(1)
    clock.now += 10
    limiter.hit(1)
    with pytest.raises(HTTPException) as exc:
        limiter.hit(1)
    assert exc.value.status_code == 429
    assert exc.value.detail == "Too many searches; slow down"
    assert exc.value.headers == {"Retry-After": "51"}  # the oldest hit leaves in 50 s


def test_window_slides():
    clock = Clock()
    limiter = SlidingWindowLimiter(1, 60.0, clock=clock)
    limiter.hit(1)
    clock.now += 59.9
    with pytest.raises(HTTPException):
        limiter.hit(1)
    clock.now += 0.1  # the first hit is now 60 s old
    limiter.hit(1)


def test_buckets_are_per_key_and_per_instance():
    clock = Clock()
    a, b = SlidingWindowLimiter(1, 60.0, clock=clock), SlidingWindowLimiter(1, 60.0, clock=clock)
    a.hit(1)
    a.hit(2)  # another user
    b.hit(1)  # another limiter
    with pytest.raises(HTTPException):
        a.hit(1)
    a.reset()
    a.hit(1)
