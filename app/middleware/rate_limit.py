"""In-memory rate limiter (per process). Use Redis for multi-instance production."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[datetime]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str, *, max_requests: int, window_seconds: int) -> bool:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=window_seconds)
        with self._lock:
            recent = [t for t in self._hits[key] if t > cutoff]
            if len(recent) >= max_requests:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True


auth_rate_limiter = RateLimiter()
