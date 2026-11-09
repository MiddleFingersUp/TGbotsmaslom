"""In-memory rate limiter for Telegram user updates per NFR-1 and SECURITY.md §9."""

import time
from collections import defaultdict


class InMemoryRateLimiter:
    """Sliding-window rate limiter per user."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._user_requests: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        """Check if request from user_id is within rate limit."""
        now = time.time()
        window_start = now - self.window_seconds

        # Filter out requests outside the time window
        requests = [t for t in self._user_requests[user_id] if t > window_start]
        self._user_requests[user_id] = requests

        if len(requests) >= self.max_requests:
            return False

        self._user_requests[user_id].append(now)
        return True

    def reset(self) -> None:
        """Clear rate limit counters (useful in tests)."""
        self._user_requests.clear()
