"""In-process token-bucket rate limiter.

Per process: with several server processes the effective limit multiplies by
the process count. Good enough for a single-instance launch; put limits in your
proxy (or Redis) when you scale out.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, per_minute: int, burst: int | None = None):
        self.rate = per_minute / 60.0
        self.capacity = float(burst or max(1, per_minute // 4))
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < cost:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - cost, now)
            if len(self._buckets) > 50_000:  # bound memory under abuse
                cutoff = now - 600
                self._buckets = {k: v for k, v in self._buckets.items() if v[1] > cutoff}
            return True
