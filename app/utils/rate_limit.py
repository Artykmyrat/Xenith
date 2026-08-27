"""A small in-memory rate limiter used to slow down credential brute-force.

The panel runs with a single uvicorn worker (see main.py), so keeping the
state in the process is enough and avoids pulling in another dependency.
Sync endpoints are served from a threadpool, hence the lock.
"""

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict


class SlidingWindowRateLimiter:
    """Counts events per key within a sliding time window.

    Only failures are recorded; a successful attempt clears the key, so a
    legitimate user is never penalised for someone else's typos.
    """

    def __init__(self, attempts: int, window: int):
        self.attempts = attempts
        self.window = window
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return self.attempts > 0 and self.window > 0

    def _prune(self, hits: Deque[float], now: float) -> None:
        cutoff = now - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def retry_after(self, key: str) -> int:
        """Seconds to wait before `key` is allowed again, 0 when it is allowed."""
        if not self.enabled:
            return 0

        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0

            self._prune(hits, now)
            if not hits:
                del self._hits[key]
                return 0

            if len(hits) < self.attempts:
                return 0

            return max(1, int(self.window - (now - hits[0])) + 1)

    def record_failure(self, key: str) -> None:
        if not self.enabled:
            return

        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            self._prune(hits, now)
            hits.append(now)

            # Keep the dict from growing without bound on a noisy public host.
            for stale_key in [k for k, v in self._hits.items() if v and v[-1] <= now - self.window]:
                del self._hits[stale_key]

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
