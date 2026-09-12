"""Failed-login throttle, per email, in process memory."""
import datetime as dt
import threading
from collections import deque


class LoginThrottle:
    def __init__(self, max_failures: int, window: dt.timedelta) -> None:
        self._max = max_failures
        self._window = window
        self._failures: dict[str, deque[dt.datetime]] = {}
        self._lock = threading.Lock()

    # Bounds memory under a flood of distinct made-up emails: past this many
    # tracked keys, a sweep drops every bucket whose failures have all expired.
    _SWEEP_AT = 10_000

    def _prune(self, key: str, now: dt.datetime) -> deque[dt.datetime]:
        bucket = self._failures.get(key)
        if bucket is None:
            return deque()
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        if not bucket:
            del self._failures[key]
        return bucket

    def _sweep(self, now: dt.datetime) -> None:
        for key in list(self._failures):
            self._prune(key, now)

    def retry_after_seconds(self, key: str) -> int | None:
        """Seconds until another attempt is allowed, or None if allowed now."""
        now = dt.datetime.now(dt.timezone.utc)
        with self._lock:
            bucket = self._prune(key, now)
            if len(bucket) < self._max:
                return None
            return max(1, int((bucket[0] + self._window - now).total_seconds()))

    def record_failure(self, key: str) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        with self._lock:
            if len(self._failures) >= self._SWEEP_AT:
                self._sweep(now)
            self._prune(key, now)
            self._failures.setdefault(key, deque()).append(now)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)
