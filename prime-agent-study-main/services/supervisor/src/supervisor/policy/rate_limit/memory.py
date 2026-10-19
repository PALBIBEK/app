"""In-memory rate limiter — sliding window algorithm.

Simple in-process rate limiting using deque-based sliding windows.
Suitable for:
- Single-process deployments
- Development/testing
- Per-process limits (combined with distributed limiter)

NOT suitable for:
- Multi-replica production deployments
- Global rate limits across processes

For distributed rate limiting, use PostgresRateLimiter.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from supervisor.policy.types import DEFAULT_RATE_WINDOW_MS, RateDecision, RateDecisionStatus

__all__ = ["MemoryRateLimiter"]


class MemoryRateLimiter:
    """In-memory sliding window rate limiter.

    Uses deque to track request timestamps within window.
    Thread-safe for concurrent access via atomic operations.

    Example:
        limiter = MemoryRateLimiter(
            max_per_window=100,
            window_ms=60_000,  # 1 minute
        )

        # Check and record atomically
        decision = await limiter.check_and_record("user_123")
        if not decision.allowed:
            raise RateLimitExceeded(
                principal_id="user_123",
                retry_after_ms=decision.retry_after_ms,
            )
    """

    __slots__ = ("_max", "_window_ms", "_windows")

    def __init__(
        self,
        *,
        max_per_window: int,
        window_ms: int = DEFAULT_RATE_WINDOW_MS,
    ) -> None:
        """Initialize rate limiter.

        Args:
            max_per_window: Maximum requests per window
            window_ms: Window duration in milliseconds
        """
        if max_per_window <= 0:
            raise ValueError("max_per_window must be positive")
        if window_ms <= 0:
            raise ValueError("window_ms must be positive")

        self._max = max_per_window
        self._window_ms = window_ms
        # Per-principal sliding windows
        self._windows: dict[str, deque[int]] = defaultdict(deque)

    async def check(self, principal_id: str) -> RateDecision:
        """Check if request is allowed (without recording).

        Args:
            principal_id: Principal to check

        Returns:
            Rate decision
        """
        now_ms = int(time.time() * 1000)
        window = self._windows.get(principal_id, deque())

        # Prune expired entries
        cutoff_ms = now_ms - self._window_ms
        while window and window[0] < cutoff_ms:
            window.popleft()

        current_count = len(window)
        remaining = max(0, self._max - current_count)
        allowed = current_count < self._max

        # Calculate retry_after if limited
        retry_after_ms: int | None = None
        if not allowed and window:
            # When oldest entry expires
            oldest = window[0]
            retry_after_ms = max(0, oldest + self._window_ms - now_ms)

        return RateDecision(
            status=RateDecisionStatus.ALLOWED if allowed else RateDecisionStatus.LIMITED,
            allowed=allowed,
            remaining=remaining,
            limit=self._max,
            retry_after_ms=retry_after_ms,
            window_ms=self._window_ms,
        )

    async def record(self, principal_id: str) -> None:
        """Record request (for separate check/record flow).

        Args:
            principal_id: Principal making request
        """
        now_ms = int(time.time() * 1000)
        window = self._windows[principal_id]
        window.append(now_ms)

    async def check_and_record(self, principal_id: str) -> RateDecision:
        """Check and record atomically.

        Args:
            principal_id: Principal making request

        Returns:
            Rate decision (recorded if allowed)
        """
        now_ms = int(time.time() * 1000)
        window = self._windows[principal_id]

        # Prune expired entries
        cutoff_ms = now_ms - self._window_ms
        while window and window[0] < cutoff_ms:
            window.popleft()

        current_count = len(window)
        allowed = current_count < self._max

        if allowed:
            window.append(now_ms)
            current_count += 1

        remaining = max(0, self._max - current_count)

        # Calculate retry_after if limited
        retry_after_ms: int | None = None
        if not allowed and window:
            oldest = window[0]
            retry_after_ms = max(0, oldest + self._window_ms - now_ms)

        return RateDecision(
            status=RateDecisionStatus.ALLOWED if allowed else RateDecisionStatus.LIMITED,
            allowed=allowed,
            remaining=remaining,
            limit=self._max,
            retry_after_ms=retry_after_ms,
            window_ms=self._window_ms,
        )

    def clear(self, principal_id: str) -> None:
        """Clear rate limit state for principal.

        Args:
            principal_id: Principal to clear
        """
        self._windows.pop(principal_id, None)

    def clear_all(self) -> None:
        """Clear all rate limit state."""
        self._windows.clear()

    @property
    def max_per_window(self) -> int:
        """Maximum requests per window."""
        return self._max

    @property
    def window_ms(self) -> int:
        """Window duration in milliseconds."""
        return self._window_ms
