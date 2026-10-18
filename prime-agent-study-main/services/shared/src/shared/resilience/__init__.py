"""
Resilience patterns for fault-tolerant distributed systems.

Provides:
- Circuit Breaker: Fail-fast when downstream is unhealthy
- Retry: Automatic retry with exponential backoff
- Timeout: Configurable timeouts with cancellation
- Bulkhead: Limit concurrent operations (future)

Usage:
    from shared.resilience import (
        CircuitBreaker,
        CircuitBreakerConfig,
        circuit_breaker,
        retry,
        RetryConfig,
    )

    # As decorator
    @circuit_breaker(CircuitBreakerConfig(failure_threshold=5))
    @retry(RetryConfig(max_attempts=3))
    async def call_external_api():
        ...

    # As context manager
    breaker = CircuitBreaker(config)
    async with breaker:
        await call_external_api()
"""

from shared.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    CircuitOpenError,
    circuit_breaker,
)
from shared.resilience.retry import (
    Retrier,
    RetryConfig,
    RetryState,
    MaxRetriesExceededError,
    retry,
    retry_with_timeout,
    RETRY_FAST,
    RETRY_STANDARD,
    RETRY_AGGRESSIVE,
    RETRY_PATIENT,
)

__all__ = [
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "CircuitOpenError",
    "circuit_breaker",
    # Retry
    "Retrier",
    "RetryConfig",
    "RetryState",
    "MaxRetriesExceededError",
    "retry",
    "retry_with_timeout",
    "RETRY_FAST",
    "RETRY_STANDARD",
    "RETRY_AGGRESSIVE",
    "RETRY_PATIENT",
]
