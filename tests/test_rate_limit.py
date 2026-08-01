"""RateLimiter: sliding-window-log semantics, driven by an injected clock."""

from __future__ import annotations

from app.rate_limit import RateLimiter


class FakeClock:
    """A controllable clock: advances only when the test tells it to."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_up_to_the_limit_then_rejects() -> None:
    clock = FakeClock()
    limiter = RateLimiter(3, 60.0, now=clock)

    for _ in range(3):
        allowed, retry_after = limiter.check("k")
        assert allowed is True
        assert retry_after == 0.0

    allowed, retry_after = limiter.check("k")
    assert allowed is False
    assert retry_after > 0.0


def test_rejected_calls_do_not_themselves_count_against_the_window() -> None:
    """A client parked exactly at the limit must be able to recover, not stay stuck."""
    clock = FakeClock()
    limiter = RateLimiter(2, 60.0, now=clock)

    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is True
    # Rejected 5 times in a row — none of these should extend the window.
    for _ in range(5):
        assert limiter.check("k")[0] is False

    clock.advance(60.1)
    assert limiter.check("k")[0] is True


def test_window_slides_and_old_hits_expire() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2, 10.0, now=clock)

    assert limiter.check("k")[0] is True
    clock.advance(5.0)
    assert limiter.check("k")[0] is True
    clock.advance(4.9)
    # Still within 10s of the first hit -> at the limit.
    assert limiter.check("k")[0] is False
    clock.advance(0.2)
    # First hit (at t=0) is now > 10s old and drops out of the window.
    assert limiter.check("k")[0] is True


def test_retry_after_reflects_when_the_oldest_hit_expires() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1, 30.0, now=clock)

    assert limiter.check("k")[0] is True
    clock.advance(10.0)
    allowed, retry_after = limiter.check("k")
    assert allowed is False
    assert retry_after == 20.0  # 30s window - 10s elapsed


def test_keys_are_independent() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1, 60.0, now=clock)

    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is False
    # A different key has its own untouched budget.
    assert limiter.check("b")[0] is True


def test_reset_clears_all_recorded_hits() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1, 60.0, now=clock)

    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is False

    limiter.reset()

    assert limiter.check("a")[0] is True
