"""Simple asyncio token-bucket rate limiter.

Used to gate per-source request rates at the documented ceiling (Coinglass:
api-key-max-limit header reports 300 over an unspecified window — assumed
per-minute) instead of inferring the ceiling from concurrency * latency.

Concurrency caps are kept as a separate safety net for connection-pool /
memory bounds; rate is the actual contract with the upstream.
"""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float | None = None) -> None:
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else max(1.0, rate_per_sec))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                needed = (n - self._tokens) / self.rate
            await asyncio.sleep(needed)
