"""
Circuit Breaker pattern for fault-tolerant distributed systems.

The circuit breaker prevents cascading failures by failing fast when a
downstream service is unhealthy. It has three states:

- CLOSED: Normal operation. Requests pass through. Failures are counted.
- OPEN: Service is unhealthy. Requests fail immediately without attempting.
- HALF_OPEN: Testing recovery. Limited requests pass through to test health.

State Transitions:
    CLOSED -> OPEN: When failure_threshold consecutive failures occur
    OPEN -> HALF_OPEN: After recovery_timeout seconds
    HALF_OPEN -> CLOSED: When half_open_max_calls succeed
    HALF_OPEN -> OPEN: When any call fails

Usage:
    # As decorator
    @circuit_breaker(CircuitBreakerConfig(failure_threshold=5))
    async def call_external_api():
        ...

    # As context manager
    breaker = CircuitBreaker(config)
    async with breaker:
        await call_external_api()

    # Manual control
    breaker = CircuitBreaker(config)
    if breaker.allow_request():
        try:
            result = await call_external_api()
            breaker.record_success()
        except Exception as e:
            breaker.record_failure()
            raise

Design Notes:
- Thread-safe via asyncio.Lock for state transitions
- Configurable failure threshold, recovery timeout, and half-open calls
- Supports both decorator and context manager patterns
- Emits structured logs for observability
- Can be extended with callbacks for state change notifications
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, tracking failures
    OPEN = "open"  # Failing fast, rejecting requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitOpenError(Exception):
    """Raised when circuit is open and request is rejected."""

    def __init__(
        self,
        circuit_name: str,
        time_until_retry: float,
        failure_count: int,
    ) -> None:
        self.circuit_name = circuit_name
        self.time_until_retry = time_until_retry
        self.failure_count = failure_count
        super().__init__(
            f"Circuit '{circuit_name}' is OPEN. "
            f"Retry in {time_until_retry:.1f}s. "
            f"Failures: {failure_count}"
        )


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """
    Configuration for circuit breaker behavior.

    Attributes:
        failure_threshold: Number of consecutive failures before opening circuit.
        recovery_timeout: Seconds to wait before transitioning to half-open.
        half_open_max_calls: Number of successful calls needed to close circuit.
        excluded_exceptions: Exceptions that don't count as failures.
        name: Name for logging and metrics.

    Example:
        ```python
        config = CircuitBreakerConfig(
            failure_threshold=5,
            recovery_timeout=30.0,
            half_open_max_calls=3,
            excluded_exceptions=(ValidationError,),
            name="external-api",
        )
        ```
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3
    excluded_exceptions: tuple[type[Exception], ...] = ()
    name: str = "default"

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.recovery_timeout < 0:
            raise ValueError("recovery_timeout must be >= 0")
        if self.half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")


@dataclass
class CircuitBreaker:
    """
    Circuit breaker implementation with three states.

    Thread-safe for concurrent async operations via asyncio.Lock.

    Example:
        ```python
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))

        # As context manager
        async with breaker:
            result = await risky_operation()

        # Check state
        if breaker.state == CircuitState.OPEN:
            logger.warning("Circuit is open, using fallback")
        ```
    """

    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)

    # Internal state (mutable)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    # Callbacks for state change notifications (optional)
    _on_state_change: Callable[[CircuitState, CircuitState], None] | None = field(
        default=None, init=False
    )

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    @property
    def failure_count(self) -> int:
        """Current consecutive failure count."""
        return self._failure_count

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self._state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing fast)."""
        return self._state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self._state == CircuitState.HALF_OPEN

    def set_state_change_callback(
        self,
        callback: Callable[[CircuitState, CircuitState], None],
    ) -> None:
        """
        Set callback for state change notifications.

        Args:
            callback: Function called with (old_state, new_state) on transitions.
        """
        self._on_state_change = callback

    async def _transition_to(self, new_state: CircuitState) -> None:
        """
        Transition to a new state with logging.

        Args:
            new_state: The target state.
        """
        old_state = self._state
        if old_state == new_state:
            return

        self._state = new_state

        logger.info(
            f"Circuit '{self.config.name}' state change: "
            f"{old_state.value} -> {new_state.value}"
        )

        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state)
            except Exception as e:
                logger.error(f"Error in state change callback: {e}")

    def allow_request(self) -> bool:
        """
        Check if a request should be allowed.

        Returns:
            True if request should proceed, False if circuit is open.

        Note:
            This is a synchronous check. For thread-safety in state transitions,
            use the async context manager or call() method instead.
        """
        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.config.recovery_timeout:
                # Will transition to half-open on next async operation
                return True
            return False

        # HALF_OPEN: Allow limited requests
        return True

    def time_until_retry(self) -> float:
        """
        Get seconds until circuit might allow requests.

        Returns:
            Seconds until recovery_timeout elapses, or 0 if not OPEN.
        """
        if self._state != CircuitState.OPEN:
            return 0.0

        elapsed = time.monotonic() - self._last_failure_time
        remaining = self.config.recovery_timeout - elapsed
        return max(0.0, remaining)

    async def record_success(self) -> None:
        """
        Record a successful operation.

        In HALF_OPEN state, increments success counter.
        When success counter reaches half_open_max_calls, transitions to CLOSED.
        In CLOSED state, resets failure counter.
        """
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                logger.debug(
                    f"Circuit '{self.config.name}' half-open success "
                    f"{self._success_count}/{self.config.half_open_max_calls}"
                )

                if self._success_count >= self.config.half_open_max_calls:
                    await self._transition_to(CircuitState.CLOSED)
                    self._failure_count = 0
                    self._success_count = 0

            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    async def record_failure(self, exception: Exception | None = None) -> None:
        """
        Record a failed operation.

        Args:
            exception: The exception that caused the failure (for logging).

        In CLOSED state, increments failure counter.
        When failure counter reaches failure_threshold, transitions to OPEN.
        In HALF_OPEN state, immediately transitions back to OPEN.
        """
        async with self._lock:
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                logger.warning(
                    f"Circuit '{self.config.name}' half-open test failed, "
                    f"reopening: {exception}"
                )
                await self._transition_to(CircuitState.OPEN)
                self._success_count = 0

            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                logger.debug(
                    f"Circuit '{self.config.name}' failure "
                    f"{self._failure_count}/{self.config.failure_threshold}: {exception}"
                )

                if self._failure_count >= self.config.failure_threshold:
                    logger.warning(
                        f"Circuit '{self.config.name}' opening after "
                        f"{self._failure_count} failures"
                    )
                    await self._transition_to(CircuitState.OPEN)

    async def _check_state_transition(self) -> None:
        """Check and perform any pending state transitions."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.config.recovery_timeout:
                    logger.info(
                        f"Circuit '{self.config.name}' recovery timeout elapsed, "
                        f"transitioning to half-open"
                    )
                    await self._transition_to(CircuitState.HALF_OPEN)
                    self._success_count = 0

    async def __aenter__(self) -> CircuitBreaker:
        """
        Enter circuit breaker context.

        Raises:
            CircuitOpenError: If circuit is open and not ready to test.
        """
        await self._check_state_transition()

        if self._state == CircuitState.OPEN:
            raise CircuitOpenError(
                circuit_name=self.config.name,
                time_until_retry=self.time_until_retry(),
                failure_count=self._failure_count,
            )

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        """
        Exit circuit breaker context.

        Records success or failure based on exception.
        Does not suppress exceptions.
        """
        if exc_type is None:
            await self.record_success()
        elif exc_type is not None and not issubclass(
            exc_type, self.config.excluded_exceptions
        ):
            await self.record_failure(exc_val)  # type: ignore
        # Don't suppress exceptions
        return False

    async def call(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Execute a function with circuit breaker protection.

        Args:
            func: Async function to execute.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            Result of func.

        Raises:
            CircuitOpenError: If circuit is open.
            Exception: Any exception from func (after recording failure).

        Example:
            ```python
            breaker = CircuitBreaker(config)
            result = await breaker.call(fetch_data, url="https://api.example.com")
            ```
        """
        async with self:
            return await func(*args, **kwargs)

    def reset(self) -> None:
        """
        Reset circuit breaker to initial closed state.

        Useful for testing or manual intervention.
        """
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        logger.info(f"Circuit '{self.config.name}' manually reset to CLOSED")

    def get_metrics(self) -> dict[str, Any]:
        """
        Get circuit breaker metrics for monitoring.

        Returns:
            Dict with state, failure_count, success_count, last_failure_time.
        """
        return {
            "name": self.config.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": self._last_failure_time,
            "time_until_retry": self.time_until_retry(),
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
                "half_open_max_calls": self.config.half_open_max_calls,
            },
        }


def circuit_breaker(
    config: CircuitBreakerConfig | None = None,
    *,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    half_open_max_calls: int = 3,
    excluded_exceptions: tuple[type[Exception], ...] = (),
    name: str | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator factory for circuit breaker pattern.

    Can be used with a config object or keyword arguments.

    Args:
        config: CircuitBreakerConfig instance (takes precedence).
        failure_threshold: Number of failures before opening (if no config).
        recovery_timeout: Seconds before testing recovery (if no config).
        half_open_max_calls: Successes needed to close (if no config).
        excluded_exceptions: Exceptions that don't count as failures.
        name: Circuit name for logging (defaults to function name).

    Returns:
        Decorator that wraps async function with circuit breaker.

    Example:
        ```python
        # With config object
        @circuit_breaker(CircuitBreakerConfig(failure_threshold=3))
        async def call_api():
            ...

        # With keyword arguments
        @circuit_breaker(failure_threshold=5, recovery_timeout=60.0)
        async def call_api():
            ...

        # Named circuit
        @circuit_breaker(name="payment-gateway", failure_threshold=3)
        async def process_payment():
            ...
        ```
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        # Build config
        if config is not None:
            cfg = config
        else:
            cfg = CircuitBreakerConfig(
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                half_open_max_calls=half_open_max_calls,
                excluded_exceptions=excluded_exceptions,
                name=name or func.__name__,
            )

        # Create breaker instance for this function
        breaker = CircuitBreaker(config=cfg)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await breaker.call(func, *args, **kwargs)

        # Expose breaker for inspection/testing
        wrapper.circuit_breaker = breaker  # type: ignore

        return wrapper

    return decorator


# Convenience alias
cb = circuit_breaker
