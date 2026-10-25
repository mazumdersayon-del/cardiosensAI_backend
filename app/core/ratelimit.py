"""Fixed-window rate limiter.

In-memory, so limits apply per API process. For several instances behind a load
balancer, back this with Redis (same interface: `hit(key, limit, window)`).
"""

import threading
import time

from app.core.errors import too_many


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, limit: int, window_s: int) -> None:
        now = time.monotonic()
        with self._lock:
            count, start = self._buckets.get(key, (0, now))
            if now - start >= window_s:
                count, start = 0, now
            count += 1
            self._buckets[key] = (count, start)
            if len(self._buckets) > 100_000:  # crude memory guard
                self._buckets.clear()
        if count > limit:
            raise too_many()

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


limiter = RateLimiter()
