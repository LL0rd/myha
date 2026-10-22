"""The sliding-window rate limiter."""

from myha_gateway.auth.ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limit_per_window_then_free_again() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=5, window_s=60, clock=clock)
    assert [limiter.allow("ip") for _ in range(6)] == [True] * 5 + [False]
    clock.now += 59.9
    assert not limiter.allow("ip")
    clock.now += 0.1  # the first hits leave the window after exactly 60 s
    assert limiter.allow("ip")


def test_window_slides_instead_of_resetting() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_s=60, clock=clock)
    assert limiter.allow("ip")
    clock.now += 30
    assert limiter.allow("ip")
    assert not limiter.allow("ip")
    clock.now += 30  # only the first hit has left the window
    assert limiter.allow("ip")
    assert not limiter.allow("ip")


def test_keys_are_separate() -> None:
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=FakeClock())
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")


def test_denied_attempts_do_not_extend_the_block() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=clock)
    assert limiter.allow("ip")
    for _ in range(10):
        clock.now += 5
        assert not limiter.allow("ip")
    clock.now += 10
    assert limiter.allow("ip")


def test_tracked_keys_are_bounded_and_the_least_recent_goes_first() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=clock, max_keys=3)
    for key in ("a", "b", "c"):
        assert limiter.allow(key)
    assert not limiter.allow("b")  # a refusal records nothing, so b stays where it was
    assert limiter.allow("d")  # evicts a, the key with the oldest hit
    assert len(limiter) == 3
    assert limiter.allow("a")  # forgotten, so allowed again (evicts b)
    assert limiter.allow("b")  # evicts c
    assert not limiter.allow("d")


def test_flood_of_keys_keeps_memory_at_the_default_cap() -> None:
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=FakeClock())
    for n in range(10_500):
        assert limiter.allow(f"10.0.{n // 256}.{n % 256}")
    assert len(limiter) == 10_000


def test_keys_with_an_empty_window_are_dropped() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_s=60, clock=clock)
    assert limiter.allow("a")
    clock.now += 60
    assert limiter.allow("b")  # every call sweeps the expired keys at the old end
    assert len(limiter) == 1
