# Production Resilience Gaps - Agent Platform Supervisor

**Date:** 2026-08-28
**Context:** Analysis of production resilience patterns missing from the supervisor service that could cause outages at scale.

**Assessment:** The architecture is **production-GRADE** (excellent design quality), but the implementation is not yet **production-READY** (missing resilience patterns).

---

## Executive Summary

The supervisor codebase demonstrates excellent architectural patterns:
- Contract-based component separation
- Pluggable runtimes and sandboxes
- Event sourcing / journal for crash recovery
- Multi-tenant isolation with quotas
- Policy enforcement (budgets, guards, approvals)
- Basic observability (OTLP, Jaeger)

However, it's missing critical **resilience patterns** that 88% of AI agent projects fail on before reaching production.

---

## Critical Gaps (Will Cause Outages)

### 1. No Circuit Breaker Pattern

**Severity:** CRITICAL

**Industry Context:**
> "Circuit breaker pattern monitors call failure rates and, when they exceed a threshold, stops making calls to the failing dependency for a defined period, preventing cascading failure."
> — [AI Agent Circuit Breakers - DEV Community](https://dev.to/waxell/ai-agent-circuit-breakers-the-reliability-pattern-production-teams-are-missing-5bpg)

> "In multi-agent systems, an error from one agent propagates to the next, compounding along the chain. A 5% error rate per individual agent produces roughly a 23% failure rate across a five-agent chain."
> — [Why 88% of AI Agents Fail](https://www.digitalapplied.com/blog/88-percent-ai-agents-never-reach-production-failure-framework)

**What Code Has:**
- No circuit breaker implementation
- No failure rate tracking per dependency
- No automatic "open circuit" on repeated failures

**Impact:**
- Model gateway goes down → ALL requests fail
- Sandbox provider degrades → system hammers it endlessly
- Cascading failures across multi-agent chains

**Required Implementation:**

```python
from enum import Enum
from dataclasses import dataclass
from collections import deque
import time
import asyncio

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # Failures before opening
    success_threshold: int = 3          # Successes to close
    timeout_seconds: float = 30.0       # Time before half-open
    window_seconds: float = 60.0        # Sliding window for failures

class CircuitBreaker:
    def __init__(self, name: str, config: CircuitBreakerConfig):
        self.name = name
        self.config = config
        self.state = CircuitState.CLOSED
        self.failures: deque[float] = deque()
        self.successes_in_half_open = 0
        self.last_failure_time: float | None = None

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: allow limited requests
        return True

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.successes_in_half_open += 1
            if self.successes_in_half_open >= self.config.success_threshold:
                self._reset()

    def record_failure(self) -> None:
        now = time.monotonic()
        self.failures.append(now)
        self._prune_old_failures(now)

        if self.state == CircuitState.HALF_OPEN:
            self._trip()
        elif len(self.failures) >= self.config.failure_threshold:
            self._trip()

    def _trip(self) -> None:
        self.state = CircuitState.OPEN
        self.last_failure_time = time.monotonic()
        self.successes_in_half_open = 0

    def _reset(self) -> None:
        self.state = CircuitState.CLOSED
        self.failures.clear()
        self.successes_in_half_open = 0

    def _should_attempt_reset(self) -> bool:
        if self.last_failure_time is None:
            return True
        return time.monotonic() - self.last_failure_time >= self.config.timeout_seconds

    def _prune_old_failures(self, now: float) -> None:
        cutoff = now - self.config.window_seconds
        while self.failures and self.failures[0] < cutoff:
            self.failures.popleft()


# Usage in SessionManager
class SessionManager:
    def __init__(self, ...):
        self._circuit_breakers = {
            "model_gateway": CircuitBreaker("model_gateway", CircuitBreakerConfig()),
            "sandbox_provider": CircuitBreaker("sandbox_provider", CircuitBreakerConfig()),
        }

    async def _call_with_circuit_breaker(
        self,
        breaker_name: str,
        coro,
    ):
        breaker = self._circuit_breakers[breaker_name]
        if not breaker.allow_request():
            raise CircuitOpenError(f"{breaker_name} circuit is open")
        try:
            result = await coro
            breaker.record_success()
            return result
        except Exception as e:
            breaker.record_failure()
            raise
```

**Files to Modify:**
- `supervisor/policy/circuit_breaker.py` (new)
- `supervisor/session/manager.py` (integrate)
- `supervisor/seams/providers/http_gateway.py` (wrap calls)

---

### 2. In-Process Rate Limiter (Breaks at Scale)

**Severity:** CRITICAL

**Current Implementation** (`supervisor/policy/rate_limit.py`):
```python
class TenantRateLimiter:
    def __init__(self, *, max_per_minute: int | None = None) -> None:
        self._max = max_per_minute
        self._windows: dict[str, deque[float]] = defaultdict(deque)  # IN-MEMORY!
```

**Problem:**
- Multiple supervisor instances = no shared state
- Each instance has its own counter
- 10 instances × 100 req/min limit = 1000 req/min actual
- Horizontal scaling completely breaks rate limiting

**Required Implementation:**

```python
import redis.asyncio as redis
from datetime import datetime

class DistributedRateLimiter:
    """Redis-backed sliding window rate limiter."""

    def __init__(
        self,
        redis_client: redis.Redis,
        max_per_minute: int,
        key_prefix: str = "ratelimit",
    ):
        self._redis = redis_client
        self._max = max_per_minute
        self._prefix = key_prefix
        self._window_seconds = 60

    async def allow(self, tenant_id: str) -> bool:
        """Check and record request. Returns True if allowed."""
        key = f"{self._prefix}:{tenant_id}"
        now = datetime.utcnow().timestamp()
        window_start = now - self._window_seconds

        pipe = self._redis.pipeline()
        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Count current window
        pipe.zcard(key)
        # Add this request
        pipe.zadd(key, {str(now): now})
        # Set expiry
        pipe.expire(key, self._window_seconds + 1)

        results = await pipe.execute()
        current_count = results[1]

        if current_count >= self._max:
            # Remove the request we just added
            await self._redis.zrem(key, str(now))
            return False
        return True

    async def get_remaining(self, tenant_id: str) -> int:
        """Get remaining requests in current window."""
        key = f"{self._prefix}:{tenant_id}"
        now = datetime.utcnow().timestamp()
        window_start = now - self._window_seconds

        await self._redis.zremrangebyscore(key, 0, window_start)
        count = await self._redis.zcard(key)
        return max(0, self._max - count)
```

**Files to Modify:**
- `supervisor/policy/rate_limit.py` (replace or extend)
- `supervisor/bootstrap.py` (add Redis connection)
- `supervisor/config.py` (add Redis URL config)

---

### 3. No Exponential Backoff with Jitter

**Severity:** CRITICAL

**Industry Context:**
> "Exponential backoff involves doubling the wait after each failure (100ms then 200 then 400, with a ceiling). Without jitter, every client that failed at the same instant retries at the same instant, recreating the original spike."
> — [AWS Architecture Blog](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)

**What Code Has:**
- `rpc_timeout` in HarnessRuntime but no retry
- `retryable` field in ErrorEvent but not acted upon
- No backoff implementation anywhere

**Impact:**
- Rate limit (429) causes immediate retry storm
- Overloaded dependencies never recover
- Thundering herd on service restart

**Required Implementation:**

```python
import asyncio
import random
from dataclasses import dataclass
from typing import TypeVar, Callable, Awaitable
from functools import wraps

T = TypeVar("T")

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_ms: float = 100
    max_delay_ms: float = 10000
    jitter_factor: float = 0.5  # 0-1, randomizes delay
    retryable_exceptions: tuple = (Exception,)
    retryable_status_codes: tuple = (429, 502, 503, 504)

def calculate_delay(attempt: int, policy: RetryPolicy) -> float:
    """Calculate delay with exponential backoff and jitter."""
    # Exponential: 100, 200, 400, 800...
    delay = policy.base_delay_ms * (2 ** attempt)
    delay = min(delay, policy.max_delay_ms)

    # Add jitter: randomize between [delay * (1-jitter), delay * (1+jitter)]
    jitter_range = delay * policy.jitter_factor
    delay = delay + random.uniform(-jitter_range, jitter_range)

    return max(0, delay) / 1000  # Convert to seconds

async def retry_with_backoff(
    coro_factory: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
) -> T:
    """Execute coroutine with retry and exponential backoff."""
    last_exception = None

    for attempt in range(policy.max_attempts):
        try:
            return await coro_factory()
        except policy.retryable_exceptions as e:
            last_exception = e

            if attempt < policy.max_attempts - 1:
                delay = calculate_delay(attempt, policy)
                await asyncio.sleep(delay)

    raise last_exception

def with_retry(policy: RetryPolicy):
    """Decorator for async functions with retry."""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await retry_with_backoff(
                lambda: func(*args, **kwargs),
                policy,
            )
        return wrapper
    return decorator


# Usage
class HttpModelGatewayClient:
    def __init__(self, ...):
        self._retry_policy = RetryPolicy(
            max_attempts=3,
            base_delay_ms=100,
            max_delay_ms=5000,
            retryable_status_codes=(429, 502, 503, 504),
        )

    @with_retry(RetryPolicy())
    async def complete(self, messages, ...):
        # HTTP call here
        pass
```

**Files to Modify:**
- `supervisor/policy/retry.py` (new)
- `supervisor/seams/providers/http_gateway.py` (add retry)
- `supervisor/agent/runtimes/harness/runtime.py` (add retry to RPC)

---

## High Severity Gaps (Will Cause Problems)

### 4. No Dead Letter Queue (DLQ)

**Severity:** HIGH

**Industry Context:**
> "When an agent cannot process an item after exhausting all retries, the item should be routed to a dead letter queue for human review, and the pipeline should continue processing other items."
> — [Designing Fault-Tolerant AI Agent Pipelines](https://mightybot.ai/blog/fault-tolerant-ai-agent-pipelines/)

**What Code Has:**
- Failed events go to `ErrorEvent` in journal
- No separate queue for human review
- No retry-with-backoff for transient failures

**Impact:**
- Transient failures (network blip) become permanent
- No automatic retry for recoverable errors
- Ops team can't easily see/reprocess failures

**Required Implementation:**

```python
# New table for DLQ
class DeadLetterRow(Base):
    __tablename__ = "dead_letters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    operation: Mapped[str] = mapped_column(String(128))  # e.g., "tool_invoke"
    payload: Mapped[dict] = mapped_column(JSON)
    error_class: Mapped[str] = mapped_column(String(128))
    error_detail: Mapped[str] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, reprocessing, resolved, abandoned
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_retry_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class DeadLetterQueue:
    def __init__(self, db: async_sessionmaker):
        self._db = db

    async def enqueue(
        self,
        session_id: str,
        tenant_id: str,
        operation: str,
        payload: dict,
        error: Exception,
        max_retries: int = 3,
    ) -> str:
        dlq_id = uuid4().hex
        async with self._db() as db:
            row = DeadLetterRow(
                id=dlq_id,
                session_id=session_id,
                tenant_id=tenant_id,
                operation=operation,
                payload=payload,
                error_class=type(error).__name__,
                error_detail=str(error),
                max_retries=max_retries,
            )
            db.add(row)
            await db.commit()
        return dlq_id

    async def list_pending(self, tenant_id: str) -> list[DeadLetterRow]:
        async with self._db() as db:
            result = await db.execute(
                select(DeadLetterRow)
                .where(DeadLetterRow.tenant_id == tenant_id)
                .where(DeadLetterRow.status == "pending")
                .order_by(DeadLetterRow.created_at.desc())
            )
            return list(result.scalars().all())

    async def retry(self, dlq_id: str) -> bool:
        """Mark for retry. Returns False if max retries exceeded."""
        async with self._db() as db:
            row = await db.get(DeadLetterRow, dlq_id)
            if row is None:
                return False
            if row.retry_count >= row.max_retries:
                row.status = "abandoned"
                await db.commit()
                return False
            row.retry_count += 1
            row.status = "reprocessing"
            row.last_retry_at = datetime.utcnow()
            await db.commit()
            return True

    async def resolve(self, dlq_id: str) -> None:
        async with self._db() as db:
            row = await db.get(DeadLetterRow, dlq_id)
            if row:
                row.status = "resolved"
                row.resolved_at = datetime.utcnow()
                await db.commit()
```

**Files to Modify:**
- `supervisor/db/models.py` (add DeadLetterRow)
- `supervisor/policy/dlq.py` (new)
- `supervisor/api/dlq.py` (new API endpoints)
- `migrations/` (new migration)

---

### 5. No Message Queue for Async Processing

**Severity:** HIGH

**What Code Has:**
- `asyncio.Queue` for live events (in-memory only)
- No Celery, RabbitMQ, Kafka, SQS

**Problem:**
- Long-running agents block HTTP workers
- No job persistence if process crashes mid-work
- No distributed task execution
- Scaling requires running more API processes (expensive)

**Recommendation:**
Consider [Temporal](https://temporal.io) for workflow orchestration, as it's specifically designed for:
- Durable execution with automatic retry
- Long-running workflows
- Visibility into running/failed workflows
- Language-agnostic (Python SDK available)

---

### 6. No Idempotency Keys for External Tool Calls

**Severity:** HIGH

**Industry Context:**
> "The tool call is often not idempotent—a GET /patients/:id is safe to retry; a POST /orders is not. An agent runtime that retries every call the same way creates duplicate writes."
> — [Idempotency and Retry Semantics](https://bhavishyapandit9.substack.com/p/idempotency-and-retry-semantics-for)

**What Code Has:**
- `tool_call_id` tracking (good!)
- `ToolOutbox` with begin/complete pattern (good!)
- `tool_class` (pure/idempotent/transactional) for recovery

**What's Missing:**
- No idempotency key passed to external APIs
- No deduplication before making external call
- Recovery semantics != prevention of duplicates

**Required Implementation:**

```python
class IdempotentToolInvoker:
    def __init__(self, db: async_sessionmaker, cache_ttl_seconds: int = 3600):
        self._db = db
        self._cache_ttl = cache_ttl_seconds

    async def invoke_once(
        self,
        tool_call_id: str,
        tool_name: str,
        invoker: Callable[[], Awaitable[ToolResult]],
    ) -> ToolResult:
        """Invoke tool exactly once, returning cached result on retry."""

        # Check if already completed
        cached = await self._get_cached_result(tool_call_id)
        if cached is not None:
            return cached

        # Try to acquire lock (prevent concurrent duplicate)
        if not await self._try_lock(tool_call_id):
            # Another process is executing, wait for result
            return await self._wait_for_result(tool_call_id)

        try:
            result = await invoker()
            await self._cache_result(tool_call_id, result)
            return result
        finally:
            await self._release_lock(tool_call_id)
```

---

## Medium Severity Gaps (Operational Pain)

### 7. Limited Observability Metrics

**What's Present:**
- OTLP tracing
- Jaeger support
- Basic Prometheus endpoint

**What's Missing:**
- Token cost per tenant (for billing)
- Agent latency percentiles (p50, p95, p99)
- Tool call success/failure rates
- Sandbox startup time histograms
- Model gateway error rates by type
- Budget burn rate with alerting

**Recommendation:** Add structured metrics:

```python
from prometheus_client import Counter, Histogram, Gauge

# Counters
tool_calls_total = Counter(
    "supervisor_tool_calls_total",
    "Total tool invocations",
    ["tenant_id", "tool_name", "status"]  # status: success/error
)

# Histograms
tool_latency = Histogram(
    "supervisor_tool_latency_seconds",
    "Tool execution latency",
    ["tool_name"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60]
)

agent_turn_latency = Histogram(
    "supervisor_agent_turn_latency_seconds",
    "Agent turn latency",
    ["harness_version"],
    buckets=[1, 5, 10, 30, 60, 120, 300]
)

# Gauges
budget_remaining = Gauge(
    "supervisor_budget_remaining_dollars",
    "Remaining budget per session",
    ["session_id", "tenant_id"]
)
```

---

### 8. No Cost Alerting / Budget Hard Stops

**What Code Has:**
- `BudgetTracker` with `max_cost_per_session`
- `BudgetExceeded` exception

**What's Missing:**
- 80% warning alert → ticket
- 95% critical alert → page
- Per-tenant daily/monthly limits
- Organization-wide spending ceiling
- Hard circuit-breaker on non-essential routes

**Required:** Integrate with alerting system (PagerDuty, Opsgenie, Slack).

---

### 9. Secrets in Environment Variables

**What Code Has:**
- `GATEWAY_TOKEN`, `MODEL_GATEWAY_TOKEN` in env

**Problem:**
- Secrets can leak to logs on crash
- No rotation without redeploy
- No audit trail for secret access
- Container inspection exposes secrets

**Recommendation:**
- HashiCorp Vault integration
- AWS Secrets Manager / GCP Secret Manager
- Kubernetes external-secrets operator

---

## Low Severity Gaps (Nice to Have)

### 10. Distributed Tracing Context Propagation

Unclear if W3C Trace Context propagates to:
- Model gateway HTTP calls
- Sandbox subprocess stdin/stdout
- Subagent spawns across network
- External tool invocations

### 11. Graceful Degradation for Non-Critical Paths

Some providers have "degrade never fail run" (good!), but core paths fail hard:
- Budget tracking fails → session fails
- Guard check fails → session fails
- Journal write fails → session fails

### 12. Chaos/Load Testing Harness

No evidence of:
- Chaos testing (kill pod mid-request)
- Load testing framework
- Failure injection
- Recovery integration tests

---

## Implementation Priority

| Priority | Gap | Effort | Impact |
|----------|-----|--------|--------|
| P0 | Circuit Breaker | 1 week | Prevents cascading outages |
| P0 | Distributed Rate Limiter | 3 days | Required for horizontal scaling |
| P0 | Retry with Backoff | 3 days | Prevents thundering herd |
| P1 | Dead Letter Queue | 1 week | Makes failures recoverable |
| P1 | Idempotency Keys | 3 days | Prevents duplicate side effects |
| P1 | Cost Alerting | 2 days | Prevents surprise bills |
| P2 | Enhanced Metrics | 1 week | Improves debugging |
| P2 | Message Queue | 2 weeks | Enables true async |
| P3 | Secrets Management | 1 week | Security hardening |
| P3 | Chaos Testing | 2 weeks | Confidence in recovery |

**Total Estimate:** 4-6 weeks for team of 2-3 engineers

---

## References

- [Why 88% of AI Agents Fail Production](https://www.digitalapplied.com/blog/88-percent-ai-agents-never-reach-production-failure-framework)
- [AI Agent Circuit Breakers - DEV Community](https://dev.to/waxell/ai-agent-circuit-breakers-the-reliability-pattern-production-teams-are-missing-5bpg)
- [Resilience Circuit Breakers for Agentic AI](https://medium.com/@michael.hannecke/resilience-circuit-breakers-for-agentic-ai-cc7075101486)
- [Designing Fault-Tolerant AI Agent Pipelines](https://mightybot.ai/blog/fault-tolerant-ai-agent-pipelines/)
- [Idempotency and Retry Semantics for AI Agent Tools](https://bhavishyapandit9.substack.com/p/idempotency-and-retry-semantics-for)
- [How to Scale AI Agents Without Breaking Production](https://rtslabs.com/how-to-scale-ai-agents)
- [Agent Observability Audit: 60-Point Checklist](https://www.digitalapplied.com/blog/agent-observability-audit-60-point-checklist-2026)
- [Agentic AI Systems: Production Readiness Checklist](https://www.itpathsolutions.com/agentic-ai-systems-checklist-senior-engineers)
- [AWS Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
- [OpenHands Software Agent SDK Paper](https://arxiv.org/pdf/2511.03690)
