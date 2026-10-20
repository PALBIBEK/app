"""
Retry decorator with exponential backoff and jitter.

Provides configurable retry logic for transient failures in distributed systems.
Uses exponential backoff with optional jitter to avoid thundering herd problems.

Backoff Formula:
    delay = min(base_delay * (exponential_base ** attempt), max_delay)
    if jitter:
        delay *= random.uniform(0.5, 1.5)  # Full jitter

Usage:
    # As decorator with defaults
    @retry()
    async def fetch_data():
        ...

    # With custom config
    @retry(RetryConfig(max_attempts=5, base_delay=0.5))
    async def call_api():
        ...

    # With keyword arguments
    @retry(max_attempts=3, retryable_exceptions=(ConnectionError, TimeoutError))
    async def connect():
        ...

    # Manual retry
    retrier = Retrier(config)
    result = await retrier.execute(risky_operation, arg1, arg2)

Design Notes:
- Async-native: All retry logic is async
- Configurable: Exception types, delays, attempts
- Observable: Emits logs with attempt number and delay
- Composable: Works with circuit breaker decorator
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MaxRetriesExceededError(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(
        self,
        attempts: int,
        last_exception: Exception,
        operation_name: str = "operation",
    ) -> None:
        self.attempts = attempts
        self.last_exception = last_exception
        self.operation_name = operation_name
        super().__init__(
            f"{operation_name} failed after {attempts} attempts. "
            f"Last error: {last_exception}"
        )


@dataclass(frozen=True)
class RetryConfig:
    """
    Configuration for retry behavior.

    Attributes:
        max_attempts: Maximum number of attempts (including initial).
        base_delay: Base delay in seconds between retries.
        max_delay: Maximum delay cap in seconds.
        exponential_base: Base for exponential backoff (2.0 = double each time).
        jitter: Whether to add randomness to delays.
        jitter_range: Range for jitter as (min_factor, max_factor).
        retryable_exceptions: Exception types that trigger retry.
        non_retryable_exceptions: Exception types that never retry.
        on_retry: Optional callback called before each retry.

    Example:
        ```python
        config = RetryConfig(
            max_attempts=5,
            base_delay=1.0,
            max_delay=60.0,
            exponential_base=2.0,
            jitter=True,
            retryable_exceptions=(ConnectionError, TimeoutError),
        )
        ```
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    jitter_range: tuple[float, float] = (0.5, 1.5)
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)
    non_retryable_exceptions: tuple[type[Exception], ...] = ()

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be >= base_delay")
        if self.exponential_base < 1:
            raise ValueError("exponential_base must be >= 1")
        if len(self.jitter_range) != 2 or self.jitter_range[0] > self.jitter_range[1]:
            raise ValueError("jitter_range must be (min, max) with min <= max")

    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for a given attempt number.

        Args:
            attempt: Zero-based attempt number.

        Returns:
            Delay in seconds.
        """
        # Exponential backoff
        delay = self.base_delay * (self.exponential_base**attempt)

        # Cap at max_delay
        delay = min(delay, self.max_delay)

        # Add jitter
        if self.jitter:
            jitter_factor = random.uniform(*self.jitter_range)
            delay *= jitter_factor

        return delay

    def should_retry(self, exception: Exception) -> bool:
        """
        Check if an exception should trigger a retry.

        Args:
            exception: The exception to check.

        Returns:
            True if should retry, False otherwise.
        """
        # Never retry these
        if isinstance(exception, self.non_retryable_exceptions):
            return False

        # Retry if in retryable list
        return isinstance(exception, self.retryable_exceptions)


@dataclass
class RetryState:
    """Tracks state across retry attempts."""

    attempt: int = 0
    total_delay: float = 0.0
    exceptions: list[Exception] = field(default_factory=list)

    def record_attempt(self, exception: Exception, delay: float) -> None:
        """Record a failed attempt."""
        self.attempt += 1
        self.total_delay += delay
        self.exceptions.append(exception)


@dataclass
class Retrier:
    """
    Retry executor with configurable behavior.

    Example:
        ```python
        retrier = Retrier(RetryConfig(max_attempts=3))

        # Execute with retry
        result = await retrier.execute(fetch_data, url)

        # Get last state for debugging
        print(f"Total attempts: {retrier.last_state.attempt}")
        ```
    """

    config: RetryConfig = field(default_factory=RetryConfig)
    _last_state: RetryState | None = field(default=None, init=False)

    @property
    def last_state(self) -> RetryState | None:
        """Get state from last execution."""
        return self._last_state

    async def execute(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        operation_name: str | None = None,
        **kwargs: Any,
    ) -> T:
        """
        Execute a function with retry logic.

        Args:
            func: Async function to execute.
            *args: Positional arguments for func.
            operation_name: Name for logging (defaults to func name).
            **kwargs: Keyword arguments for func.

        Returns:
            Result of successful func execution.

        Raises:
            MaxRetriesExceededError: If all attempts fail.
            Exception: Non-retryable exceptions are raised immediately.
        """
        name = operation_name or getattr(func, "__name__", "operation")
        state = RetryState()
        self._last_state = state

        last_exception: Exception | None = None

        for attempt in range(self.config.max_attempts):
            try:
                if attempt > 0:
                    logger.debug(f"{name}: attempt {attempt + 1}/{self.config.max_attempts}")

                result = await func(*args, **kwargs)

                if attempt > 0:
                    logger.info(
                        f"{name}: succeeded on attempt {attempt + 1} "
                        f"after {state.total_delay:.2f}s total delay"
                    )

                return result

            except Exception as e:
                last_exception = e

                # Check if we should retry
                if not self.config.should_retry(e):
                    logger.debug(f"{name}: non-retryable exception {type(e).__name__}")
                    raise

                # Check if we have attempts remaining
                if attempt + 1 >= self.config.max_attempts:
                    logger.warning(
                        f"{name}: all {self.config.max_attempts} attempts failed"
                    )
                    break

                # Calculate delay
                delay = self.config.calculate_delay(attempt)
                state.record_attempt(e, delay)

                logger.warning(
                    f"{name}: attempt {attempt + 1} failed with {type(e).__name__}: {e}. "
                    f"Retrying in {delay:.2f}s"
                )

                await asyncio.sleep(delay)

        # All attempts exhausted
        raise MaxRetriesExceededError(
            attempts=self.config.max_attempts,
            last_exception=last_exception,  # type: ignore
            operation_name=name,
        )


def retry(
    config: RetryConfig | None = None,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    non_retryable_exceptions: tuple[type[Exception], ...] = (),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator factory for retry with exponential backoff.

    Can be used with a config object or keyword arguments.

    Args:
        config: RetryConfig instance (takes precedence).
        max_attempts: Maximum attempts including initial (if no config).
        base_delay: Base delay in seconds (if no config).
        max_delay: Maximum delay cap (if no config).
        exponential_base: Backoff multiplier (if no config).
        jitter: Whether to add randomness (if no config).
        retryable_exceptions: Exceptions that trigger retry.
        non_retryable_exceptions: Exceptions that never retry.

    Returns:
        Decorator that wraps async function with retry logic.

    Example:
        ```python
        # With defaults
        @retry()
        async def fetch():
            ...

        # With config
        @retry(RetryConfig(max_attempts=5))
        async def fetch():
            ...

        # With keyword arguments
        @retry(max_attempts=5, base_delay=2.0)
        async def fetch():
            ...

        # Specific exceptions
        @retry(retryable_exceptions=(ConnectionError, TimeoutError))
        async def connect():
            ...

        # Never retry validation errors
        @retry(non_retryable_exceptions=(ValidationError,))
        async def process():
            ...
        ```
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        # Build config
        if config is not None:
            cfg = config
        else:
            cfg = RetryConfig(
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                exponential_base=exponential_base,
                jitter=jitter,
                retryable_exceptions=retryable_exceptions,
                non_retryable_exceptions=non_retryable_exceptions,
            )

        # Create retrier instance
        retrier = Retrier(config=cfg)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await retrier.execute(func, *args, **kwargs)

        # Expose retrier for inspection/testing
        wrapper.retrier = retrier  # type: ignore

        return wrapper

    return decorator


def retry_with_timeout(
    timeout: float,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator combining retry with per-attempt timeout.

    Args:
        timeout: Timeout in seconds for each attempt.
        config: RetryConfig instance.
        **kwargs: Arguments passed to retry().

    Returns:
        Decorator that wraps async function with retry and timeout.

    Example:
        ```python
        @retry_with_timeout(timeout=30.0, max_attempts=3)
        async def slow_api_call():
            ...
        ```
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper_with_timeout(*args: Any, **kw: Any) -> T:
            return await asyncio.wait_for(func(*args, **kw), timeout=timeout)

        # Apply retry to the timeout-wrapped function
        return retry(config, **kwargs)(wrapper_with_timeout)

    return decorator


# Predefined configs for common scenarios
RETRY_FAST = RetryConfig(
    max_attempts=3,
    base_delay=0.1,
    max_delay=1.0,
    exponential_base=2.0,
)

RETRY_STANDARD = RetryConfig(
    max_attempts=3,
    base_delay=1.0,
    max_delay=30.0,
    exponential_base=2.0,
)

RETRY_AGGRESSIVE = RetryConfig(
    max_attempts=5,
    base_delay=0.5,
    max_delay=60.0,
    exponential_base=2.0,
)

RETRY_PATIENT = RetryConfig(
    max_attempts=10,
    base_delay=2.0,
    max_delay=300.0,
    exponential_base=1.5,
)
