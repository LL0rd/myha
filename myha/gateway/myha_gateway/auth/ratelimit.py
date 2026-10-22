"""In-memory sliding-window rate limiter, keyed by an opaque string such as a client IP."""

import time
from collections import OrderedDict, deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """At most ``limit`` allowed calls per key within any ``window_s`` seconds.

    Only allowed calls are recorded, so a client that keeps knocking is free again
    ``window_s`` after its last allowed call. Keys are kept in the order of their newest
    hit: keys whose window is empty are swept from the old end, and beyond ``max_keys`` the
    key with the oldest hit is forgotten, so a flood of addresses cannot grow memory without
    bound (a forgotten key starts afresh). Not thread-safe; it runs on the event loop.
    """

    def __init__(
        self,
        limit: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        if limit < 1 or window_s <= 0:
            raise ValueError("limit and window_s must be positive")
        self.limit, self.window_s, self.max_keys = limit, window_s, max_keys
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        """Number of tracked keys."""
        return len(self._hits)

    def allow(self, key: str) -> bool:
        """Record a call for ``key`` and return ``True``, or ``False`` if it is over the limit."""
        now = self._clock()
        horizon = now - self.window_s
        while self._hits and next(iter(self._hits.values()))[-1] <= horizon:
            self._hits.popitem(last=False)
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._hits.popitem(last=False)
            hits = self._hits[key] = deque()
        else:
            while hits[0] <= horizon:  # the sweep left at least the newest hit in the window
                hits.popleft()
            if len(hits) >= self.limit:
                return False
        hits.append(now)
        self._hits.move_to_end(key)
        return True
