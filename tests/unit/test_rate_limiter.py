"""Unit tests for Telegram user rate limiter."""

import pytest

from src.telegram_adapter.rate_limiter import InMemoryRateLimiter


@pytest.mark.unit
def test_rate_limiter_allows_under_limit() -> None:
    """Verify requests under limit are allowed."""
    limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60)
    user_id = 12345

    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is True


@pytest.mark.unit
def test_rate_limiter_blocks_over_limit() -> None:
    """Verify requests exceeding limit are blocked."""
    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)
    user_id = 999

    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is True
    # 3rd request should be blocked
    assert limiter.is_allowed(user_id) is False


@pytest.mark.unit
def test_rate_limiter_isolates_different_users() -> None:
    """Verify rate limit counters are separate for each user."""
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)
    user_a = 100
    user_b = 200

    assert limiter.is_allowed(user_a) is True
    assert limiter.is_allowed(user_a) is False
    # User B should still be allowed
    assert limiter.is_allowed(user_b) is True
