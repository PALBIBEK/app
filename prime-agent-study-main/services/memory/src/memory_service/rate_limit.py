"""In-process token-bucket rate limits for Memory mutating routes (MVP production bar)."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from threading import Lock


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class TokenBucketLimiter:
    """Simple per-key token bucket (thread-safe, single-process)."""

    def __init__(
        self,
        *,
        rate_per_second: float | None = None,
        burst: int | None = None,
    ) -> None:
        self._rate = (
            rate_per_second
            if rate_per_second is not None
            else _env_float("MEMORY_RATE_LIMIT_PER_SECOND", 10.0)
        )
        self._burst = (
            burst
            if burst is not None
            else _env_int("MEMORY_RATE_LIMIT_BURST", 30)
        )
        self._tokens: dict[str, float] = defaultdict(lambda: float(self._burst))
        self._updated: dict[str, float] = {}
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return self._rate > 0 and self._burst > 0

    def allow(self, key: str) -> bool:
        if not self.enabled:
            return True
        now = time.monotonic()
        with self._lock:
            last = self._updated.get(key, now)
            tokens = self._tokens.get(key, float(self._burst))
            elapsed = max(0.0, now - last)
            tokens = min(float(self._burst), tokens + elapsed * self._rate)
            if tokens < 1.0:
                self._tokens[key] = tokens
                self._updated[key] = now
                return False
            self._tokens[key] = tokens - 1.0
            self._updated[key] = now
            return True


_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_mutating_method(method: str) -> bool:
    return method.upper() in _MUTATING


def rate_limit_key(*, tenant_id: str | None, client_ip: str | None) -> str:
    tenant = (tenant_id or "").strip()
    if tenant:
        return f"tenant:{tenant}"
    ip = (client_ip or "").strip() or "unknown"
    return f"ip:{ip}"
