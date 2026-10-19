# Production-Grade Platform Rewrite Plan

**Approach**: Rewrite one file at a time with senior engineer patterns.

## Overview

Rewrite the prime-agent-study platform using senior engineer patterns to support 1000 concurrent users with Temporal-based durable workflows and pluggable sandbox providers.

**Key Requirements:**
- 1000 concurrent users
- Temporal full workflow orchestration (agent turns as Workflows, tools as Activities)
- Pluggable sandbox providers (E2B, Docker, Firecracker, local)
- Kubernetes (prod) + Docker Compose (dev)
- Advanced Python patterns (decorators, protocols, DI, context managers)

**Existing Foundation:**
- D1-D14 deployment stages documented
- 41-phase long-running sessions plan
- QM architecture patterns studied
- Week 1 implementation (SessionStore, SubprocessSandbox, leases)

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  API Layer (FastAPI)                                            │
│  - Session API, Health/Ready, SSE Streaming                     │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│  Temporal Workflows                                             │
│  - AgentSessionWorkflow (per session, long-lived)               │
│  - AgentTurnWorkflow (per turn, child workflow)                 │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│  Temporal Activities                                            │
│  - LLMActivity, ToolActivity, ApprovalActivity, CheckpointActivity │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│  Seams Layer (Protocols)                                        │
│  - ToolProvider, ModelGateway, SandboxProvider, MemoryProvider  │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│  Infrastructure                                                  │
│  - PostgreSQL, Redis (cache), Object Storage, Sandbox Pools     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Rewrite Order (One File at a Time)

### Phase 1: Shared Infrastructure

| # | File | Purpose | Status |
|---|------|---------|--------|
| 1 | `services/shared/src/shared/di/container.py` | DI container | ✅ |
| 2 | `services/shared/src/shared/resilience/circuit_breaker.py` | Circuit breaker decorator | ⬜ |
| 3 | `services/shared/src/shared/resilience/retry.py` | Retry decorator with backoff | ⬜ |
| 4 | `services/shared/src/shared/db/pool.py` | Connection pool config | ⬜ |
| 5 | `services/shared/src/shared/observability/tracing.py` | OpenTelemetry decorators | ⬜ |
| 6 | `services/shared/src/shared/rate_limit/redis.py` | Distributed rate limiter | ⬜ |

### Phase 2: Temporal Integration

| # | File | Purpose | Status |
|---|------|---------|--------|
| 7 | `services/supervisor/src/supervisor/temporal/client.py` | Temporal client setup | ⬜ |
| 8 | `services/supervisor/src/supervisor/temporal/workflows/session.py` | Long-lived session workflow | ⬜ |
| 9 | `services/supervisor/src/supervisor/temporal/workflows/turn.py` | Per-turn child workflow | ⬜ |
| 10 | `services/supervisor/src/supervisor/temporal/activities/llm.py` | LLM call activity | ⬜ |
| 11 | `services/supervisor/src/supervisor/temporal/activities/tool.py` | Tool execution activity | ⬜ |
| 12 | `services/supervisor/src/supervisor/temporal/activities/checkpoint.py` | Checkpoint activity | ⬜ |
| 13 | `services/supervisor/src/supervisor/temporal/activities/approval.py` | HITL approval activity | ⬜ |

### Phase 3: Sandbox Pool & Providers

| # | File | Purpose | Status |
|---|------|---------|--------|
| 14 | `services/supervisor/src/supervisor/sandbox/pool.py` | Pre-warming pool manager | ⬜ |
| 15 | `services/supervisor/src/supervisor/sandbox/providers/base.py` | Provider protocol | ⬜ |
| 16 | `services/supervisor/src/supervisor/sandbox/providers/docker.py` | Docker provider | ⬜ |
| 17 | `services/supervisor/src/supervisor/sandbox/providers/e2b.py` | E2B cloud provider | ⬜ |
| 18 | `services/supervisor/src/supervisor/sandbox/providers/firecracker.py` | Firecracker MicroVM | ⬜ |
| 19 | `services/supervisor/src/supervisor/sandbox/providers/local.py` | Local subprocess | ⬜ |

### Phase 4: Seams Layer Enhancement

| # | File | Purpose | Status |
|---|------|---------|--------|
| 20 | `services/supervisor/src/supervisor/seams/observability.py` | Traced seams wrapper | ⬜ |
| 21 | `services/supervisor/src/supervisor/seams/resilient.py` | Circuit breaker wrappers | ⬜ |
| 22 | `services/supervisor/src/supervisor/seams/factory.py` | Enhanced factory | ⬜ |

### Phase 5: Memory Service Rewrite

| # | File | Purpose | Status |
|---|------|---------|--------|
| 23 | `services/memory/src/memory_service/db.py` | Pool config, pre-ping | ⬜ |
| 24 | `services/memory/src/memory_service/service.py` | Add tracing | ⬜ |
| 25 | `services/memory/src/memory_service/embed.py` | Circuit breaker | ⬜ |
| 26 | `services/memory/src/memory_service/rate_limit.py` | Redis backend | ⬜ |
| 27 | `services/memory/src/memory_service/app.py` | Enhanced FastAPI app | ⬜ |

### Phase 6: Model Gateway Rewrite

| # | File | Purpose | Status |
|---|------|---------|--------|
| 28 | `services/model-gateway/src/model_gateway/providers/base.py` | Async provider protocol | ⬜ |
| 29 | `services/model-gateway/src/model_gateway/providers/openai_compat.py` | Async HTTP client | ⬜ |
| 30 | `services/model-gateway/src/model_gateway/providers/openrouter.py` | OpenRouter provider | ⬜ |
| 31 | `services/model-gateway/src/model_gateway/virtual_keys.py` | PostgreSQL persistence | ⬜ |
| 32 | `services/model-gateway/src/model_gateway/metering.py` | Enhanced metering | ⬜ |
| 33 | `services/model-gateway/src/model_gateway/app.py` | Circuit breakers, tracing | ⬜ |

### Phase 7: Supervisor Rewrite

| # | File | Purpose | Status |
|---|------|---------|--------|
| 34 | `services/supervisor/src/supervisor/agent/runtimes/githi/runtime.py` | Temporal integration | ⬜ |
| 35 | `services/supervisor/src/supervisor/agent/runtimes/githi/loop.py` | Enhanced agent loop | ⬜ |
| 36 | `services/supervisor/src/supervisor/session/manager.py` | Pool-based sandboxes | ⬜ |
| 37 | `services/supervisor/src/supervisor/app.py` | Enhanced FastAPI app | ⬜ |

---

## File Templates

### 1. DI Container (`services/shared/src/shared/di/container.py`)

```python
"""
Lightweight dependency injection container with scoped lifetimes.

Patterns:
- Singleton: One instance per container lifetime
- Scoped: One instance per scope (e.g., per request, per session)
- Transient: New instance every time

Usage:
    container = Container()
    container.register_singleton(Database, lambda: PostgresDatabase(url))
    container.register_scoped(SessionStore, lambda c: SessionStore(c.resolve(Database)))

    async with container.scope("session-123") as scoped:
        store = scoped.resolve(SessionStore)  # Gets scoped instance
"""
from __future__ import annotations
from typing import Protocol, TypeVar, Callable, Any
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import asyncio

T = TypeVar("T")


class Disposable(Protocol):
    """Protocol for resources that need cleanup."""
    async def dispose(self) -> None: ...


@dataclass
class Registration:
    """Registration entry for a dependency."""
    factory: Callable[..., Any]
    lifetime: str  # "singleton" | "scoped" | "transient"


@dataclass
class Container:
    """
    Lightweight DI container.

    Thread-safe for singleton resolution.
    Scope-safe for scoped resolution.
    """
    _registrations: dict[type, Registration] = field(default_factory=dict)
    _singletons: dict[type, Any] = field(default_factory=dict)
    _singleton_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def register_singleton(self, interface: type[T], factory: Callable[[Container], T]) -> None:
        """Register a singleton - one instance per container lifetime."""
        self._registrations[interface] = Registration(factory=factory, lifetime="singleton")

    def register_scoped(self, interface: type[T], factory: Callable[[Container], T]) -> None:
        """Register scoped - one instance per scope (request/session)."""
        self._registrations[interface] = Registration(factory=factory, lifetime="scoped")

    def register_transient(self, interface: type[T], factory: Callable[[Container], T]) -> None:
        """Register transient - new instance every resolution."""
        self._registrations[interface] = Registration(factory=factory, lifetime="transient")

    async def resolve(self, interface: type[T]) -> T:
        """Resolve a dependency. For scoped, use within a scope context."""
        reg = self._registrations.get(interface)
        if reg is None:
            raise KeyError(f"No registration for {interface.__name__}")

        if reg.lifetime == "singleton":
            async with self._singleton_lock:
                if interface not in self._singletons:
                    self._singletons[interface] = reg.factory(self)
                return self._singletons[interface]

        if reg.lifetime == "transient":
            return reg.factory(self)

        raise RuntimeError(f"Scoped resolution requires scope context for {interface.__name__}")

    @asynccontextmanager
    async def scope(self, scope_id: str):
        """Create a scoped container for request/session lifetime."""
        scoped = ScopedContainer(parent=self, scope_id=scope_id)
        try:
            yield scoped
        finally:
            await scoped.dispose()


@dataclass
class ScopedContainer:
    """Scoped container that inherits from parent."""
    parent: Container
    scope_id: str
    _instances: dict[type, Any] = field(default_factory=dict)
    _disposables: list[Disposable] = field(default_factory=list)

    async def resolve(self, interface: type[T]) -> T:
        """Resolve within scope - scoped instances are cached."""
        reg = self.parent._registrations.get(interface)
        if reg is None:
            raise KeyError(f"No registration for {interface.__name__}")

        if reg.lifetime == "singleton":
            return await self.parent.resolve(interface)

        if reg.lifetime == "scoped":
            if interface not in self._instances:
                instance = reg.factory(self)
                self._instances[interface] = instance
                if isinstance(instance, Disposable):
                    self._disposables.append(instance)
            return self._instances[interface]

        # Transient
        return reg.factory(self)

    async def dispose(self) -> None:
        """Dispose all scoped instances."""
        for disposable in reversed(self._disposables):
            try:
                await disposable.dispose()
            except Exception:
                pass  # Log but don't fail
```

### 2. Circuit Breaker (`services/shared/src/shared/resilience/circuit_breaker.py`)

```python
"""
Circuit breaker pattern for fault tolerance.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Failing, requests rejected immediately
- HALF_OPEN: Testing recovery, limited requests allowed

Usage:
    @circuit_breaker(CircuitBreakerConfig(failure_threshold=5))
    async def call_external_api():
        ...

    # Or as context manager
    breaker = CircuitBreaker(config)
    async with breaker:
        await call_external_api()
"""
from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Any
import asyncio
import time


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when circuit is open and request is rejected."""
    pass


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5          # Failures before opening
    recovery_timeout: float = 30.0      # Seconds before half-open
    half_open_max_calls: int = 3        # Test calls in half-open
    success_threshold: int = 2          # Successes to close from half-open


@dataclass
class CircuitBreaker:
    """
    Circuit breaker implementation.

    Thread-safe with asyncio.Lock.
    """
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    async def __aenter__(self) -> CircuitBreaker:
        await self._before_call()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            await self._on_success()
        else:
            await self._on_failure()
        return False  # Don't suppress exceptions

    async def _before_call(self) -> None:
        """Check if call is allowed."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if recovery timeout passed
                if time.time() - self._last_failure_time >= self.config.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._success_count = 0
                else:
                    raise CircuitOpenError(
                        f"Circuit is open. Retry after {self.config.recovery_timeout}s"
                    )

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitOpenError("Circuit is half-open, max test calls reached")
                self._half_open_calls += 1

    async def _on_success(self) -> None:
        """Record successful call."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0  # Reset on success

    async def _on_failure(self) -> None:
        """Record failed call."""
        async with self._lock:
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                self._state = CircuitState.OPEN
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.config.failure_threshold:
                    self._state = CircuitState.OPEN


def circuit_breaker(config: CircuitBreakerConfig | None = None):
    """
    Decorator to add circuit breaker to async functions.

    Usage:
        @circuit_breaker(CircuitBreakerConfig(failure_threshold=3))
        async def risky_operation():
            ...
    """
    _config = config or CircuitBreakerConfig()
    _breaker = CircuitBreaker(_config)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with _breaker:
                return await func(*args, **kwargs)

        # Expose breaker for inspection
        wrapper.breaker = _breaker
        return wrapper

    return decorator
```

### 3. Retry Decorator (`services/shared/src/shared/resilience/retry.py`)

```python
"""
Retry decorator with exponential backoff and jitter.

Features:
- Configurable max attempts
- Exponential backoff with optional jitter
- Retryable exception filtering
- Async-native

Usage:
    @retry(RetryConfig(max_attempts=3, base_delay=1.0))
    async def flaky_operation():
        ...
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Any, Type
import asyncio
import random
import logging

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,)


def retry(config: RetryConfig | None = None):
    """
    Decorator for retry with exponential backoff.

    Usage:
        @retry(RetryConfig(max_attempts=5))
        async def unreliable_api_call():
            ...
    """
    _config = config or RetryConfig()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None

            for attempt in range(_config.max_attempts):
                try:
                    return await func(*args, **kwargs)
                except _config.retryable_exceptions as e:
                    last_exception = e

                    if attempt == _config.max_attempts - 1:
                        logger.warning(
                            f"Retry exhausted for {func.__name__} after {_config.max_attempts} attempts"
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(
                        _config.base_delay * (_config.exponential_base ** attempt),
                        _config.max_delay
                    )

                    # Add jitter (0.5 to 1.5 multiplier)
                    if _config.jitter:
                        delay *= 0.5 + random.random()

                    logger.info(
                        f"Retry {attempt + 1}/{_config.max_attempts} for {func.__name__}, "
                        f"waiting {delay:.2f}s: {e}"
                    )

                    await asyncio.sleep(delay)

            # Should not reach here, but just in case
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


def retry_sync(config: RetryConfig | None = None):
    """Synchronous version of retry decorator."""
    _config = config or RetryConfig()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import time

            for attempt in range(_config.max_attempts):
                try:
                    return func(*args, **kwargs)
                except _config.retryable_exceptions as e:
                    if attempt == _config.max_attempts - 1:
                        raise

                    delay = min(
                        _config.base_delay * (_config.exponential_base ** attempt),
                        _config.max_delay
                    )
                    if _config.jitter:
                        delay *= 0.5 + random.random()

                    time.sleep(delay)

        return wrapper

    return decorator
```

### 4. Connection Pool Config (`services/shared/src/shared/db/pool.py`)

```python
"""
Production-grade database connection pooling.

Features:
- Configurable pool sizes
- Pre-ping for stale connection detection
- Connection recycling
- Timeout handling

Usage:
    engine = create_production_engine(
        "postgresql+asyncpg://...",
        PoolConfig(min_size=5, max_size=20)
    )
"""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.pool import QueuePool, NullPool


@dataclass
class PoolConfig:
    """Configuration for connection pool."""
    min_size: int = 5               # Minimum connections to maintain
    max_size: int = 20              # Maximum connections
    max_overflow: int = 10          # Extra connections beyond max_size
    pool_timeout: float = 30.0      # Wait timeout for connection
    pool_recycle: int = 3600        # Recycle connections after 1 hour
    pool_pre_ping: bool = True      # Test connections before use
    echo: bool = False              # Log SQL statements


def create_production_engine(
    database_url: str,
    config: PoolConfig | None = None,
) -> AsyncEngine:
    """
    Create a production-ready async database engine.

    Uses QueuePool for connection management with:
    - Pre-ping to detect stale connections
    - Connection recycling to prevent issues with long-lived connections
    - Overflow handling for burst traffic
    """
    _config = config or PoolConfig()

    # Determine pool class based on URL
    if ":memory:" in database_url or "sqlite" in database_url:
        # SQLite doesn't support pooling well
        from sqlalchemy.pool import StaticPool
        return create_async_engine(
            database_url,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
            echo=_config.echo,
        )

    return create_async_engine(
        database_url,
        poolclass=QueuePool,
        pool_size=_config.min_size,
        max_overflow=_config.max_overflow,
        pool_timeout=_config.pool_timeout,
        pool_recycle=_config.pool_recycle,
        pool_pre_ping=_config.pool_pre_ping,
        echo=_config.echo,
    )


def create_test_engine(database_url: str) -> AsyncEngine:
    """Create engine for testing with NullPool (no pooling)."""
    return create_async_engine(
        database_url,
        poolclass=NullPool,
        echo=False,
    )
```

---

## Temporal Workflow Templates

### Session Workflow (`services/supervisor/src/supervisor/temporal/workflows/session.py`)

```python
"""
Long-lived session workflow.

Survives:
- Worker crashes
- Deployments
- Human approval waits (days/weeks)

Usage:
    # Start session
    handle = await client.start_workflow(
        AgentSessionWorkflow.run,
        args=[session_id, config],
        id=f"session-{session_id}",
        task_queue="agent-workers",
    )

    # Send message
    await handle.signal(AgentSessionWorkflow.send_message, "Hello")

    # Get status
    status = await handle.query(AgentSessionWorkflow.get_status)
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from supervisor.temporal.activities import (
        initialize_session,
        persist_turn,
        cleanup_session,
    )


@dataclass
class SessionConfig:
    agent_id: str
    model: str
    scope_id: str
    max_turns: int = 1000
    turn_timeout: timedelta = timedelta(minutes=10)


@workflow.defn
class AgentSessionWorkflow:
    """
    Long-lived workflow for agent session.

    One workflow per session. Handles multiple turns via signals.
    Uses child workflows for individual turns to limit history growth.
    """

    def __init__(self) -> None:
        self._messages: list[str] = []
        self._status = "active"
        self._turn_count = 0
        self._pending_approval: str | None = None

    @workflow.run
    async def run(self, session_id: str, config: SessionConfig) -> dict:
        """Main workflow execution."""

        # Initialize session
        await workflow.execute_activity(
            initialize_session,
            args=[session_id, config],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Main loop - wait for messages or stop signal
        while self._status == "active" and self._turn_count < config.max_turns:
            # Wait for new message or status change
            await workflow.wait_condition(
                lambda: len(self._messages) > 0 or self._status != "active"
            )

            if not self._messages:
                continue

            message = self._messages.pop(0)
            self._turn_count += 1

            # Execute turn as child workflow
            try:
                result = await workflow.execute_child_workflow(
                    "AgentTurnWorkflow",
                    args=[session_id, message, self._turn_count],
                    id=f"{session_id}-turn-{self._turn_count}",
                    task_queue="agent-workers",
                    execution_timeout=config.turn_timeout,
                )

                # Handle approval if needed
                if result.get("needs_approval"):
                    self._pending_approval = result["approval_id"]
                    await workflow.wait_condition(
                        lambda: self._pending_approval is None
                    )

            except Exception as e:
                workflow.logger.error(f"Turn failed: {e}")
                # Continue - don't crash session on turn failure

        # Cleanup
        await workflow.execute_activity(
            cleanup_session,
            args=[session_id],
            start_to_close_timeout=timedelta(seconds=30),
        )

        return {
            "status": "completed",
            "session_id": session_id,
            "total_turns": self._turn_count,
        }

    @workflow.signal
    async def send_message(self, message: str) -> None:
        """Signal to send a new message to the session."""
        self._messages.append(message)

    @workflow.signal
    async def approve(self, approval_id: str, decision: str) -> None:
        """Signal to resolve a pending approval."""
        if self._pending_approval == approval_id:
            self._pending_approval = None
            # Decision handling would be passed to next turn

    @workflow.signal
    async def stop(self) -> None:
        """Signal to stop the session."""
        self._status = "stopped"

    @workflow.query
    def get_status(self) -> dict:
        """Query current session status."""
        return {
            "status": self._status,
            "turn_count": self._turn_count,
            "pending_messages": len(self._messages),
            "pending_approval": self._pending_approval,
        }
```

---

## Next Steps

To begin the rewrite, tell me which file you want to start with:

1. **File 1**: `services/shared/src/shared/di/container.py` - DI Container
2. **File 2**: `services/shared/src/shared/resilience/circuit_breaker.py` - Circuit Breaker
3. **File 3**: `services/shared/src/shared/resilience/retry.py` - Retry Decorator
4. **Any other file** from the list above

I'll rewrite one file at a time with full production-grade implementation.
