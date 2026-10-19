"""
Sandbox pool manager with pre-warming and health checks.

The SandboxPool manages a pool of sandboxes for efficient reuse:
- Pre-warming: Maintains minimum warm sandboxes for instant acquisition
- Health checks: Periodic validation removes unhealthy sandboxes
- Graceful drain: Controlled shutdown during deployments
- Metrics: Exposes pool state for monitoring

Thread Safety:
    All pool operations are protected by asyncio.Lock for concurrent access.
    The pool can be safely used from multiple async tasks.

Memory Management:
    Sandboxes are automatically recycled after idle_timeout to prevent
    resource leaks and ensure fresh state for new sessions.

Example:
    ```python
    # Configure pool for production
    pool = SandboxPool(
        provider=DockerSandboxProvider(),
        config=PoolConfig(
            min_warm=20,
            max_total=100,
            idle_timeout=300,
            health_interval=30,
        ),
    )

    # Start pool (spawns background tasks)
    await pool.start()

    # Use sandbox with context manager
    async with pool.acquire("session-123") as sandbox:
        result = await sandbox.execute("python script.py")
        print(result.stdout)

    # Graceful shutdown
    await pool.drain(timeout=60)
    ```
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from supervisor.sandbox.pool.provider import (
    SandboxProvider,
    SandboxHandle,
    SandboxConfig,
    SandboxStatus,
    ProvisionError,
)

logger = logging.getLogger(__name__)


class PoolDrainingError(Exception):
    """Raised when pool is draining and cannot accept new requests."""

    def __init__(self, message: str = "Pool is draining for deployment"):
        super().__init__(message)


class PoolExhaustedError(Exception):
    """Raised when pool is exhausted and cannot provision more sandboxes."""

    def __init__(self, max_total: int, timeout: float):
        super().__init__(
            f"Pool exhausted: max_total={max_total}, waited {timeout}s"
        )
        self.max_total = max_total
        self.timeout = timeout


@dataclass(frozen=True)
class PoolConfig:
    """
    Pool configuration.

    Attributes:
        min_warm: Minimum pre-warmed sandboxes to maintain.
        max_total: Maximum total sandboxes (warm + in-use).
        max_per_session: Maximum sandboxes per session.
        idle_timeout: Seconds before recycling idle sandbox.
        health_interval: Seconds between health checks.
        provision_timeout: Seconds to wait for provisioning.
        acquire_timeout: Seconds to wait for available sandbox.
        warmup_batch_size: Sandboxes to provision in parallel during warmup.
        default_sandbox_config: Default config for new sandboxes.

    Production Recommendations (1000 users):
        min_warm: 20-50 (handles burst traffic)
        max_total: 100-200 per worker (scale workers horizontally)
        idle_timeout: 300 (5 minutes)
        health_interval: 30 (30 seconds)
    """

    min_warm: int = 5
    max_total: int = 50
    max_per_session: int = 3
    idle_timeout: float = 300.0  # 5 minutes
    health_interval: float = 30.0  # 30 seconds
    provision_timeout: float = 60.0  # 1 minute
    acquire_timeout: float = 30.0  # 30 seconds
    warmup_batch_size: int = 5
    default_sandbox_config: SandboxConfig = field(default_factory=SandboxConfig)

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.min_warm < 0:
            raise ValueError("min_warm must be >= 0")
        if self.max_total < self.min_warm:
            raise ValueError("max_total must be >= min_warm")
        if self.idle_timeout < 0:
            raise ValueError("idle_timeout must be >= 0")


@dataclass
class PoolMetrics:
    """Pool metrics for monitoring."""

    total: int = 0
    warm: int = 0
    in_use: int = 0
    unhealthy: int = 0
    provisions_total: int = 0
    provisions_failed: int = 0
    teardowns_total: int = 0
    health_checks_total: int = 0
    health_checks_failed: int = 0
    acquires_total: int = 0
    acquires_waited: int = 0
    acquires_timeout: int = 0


@dataclass
class SandboxPool:
    """
    Pre-warming sandbox pool with health checks.

    Maintains a pool of ready sandboxes for instant acquisition,
    with background tasks for pre-warming and health monitoring.
    """

    provider: SandboxProvider
    config: PoolConfig = field(default_factory=PoolConfig)

    # Internal state
    _available: asyncio.Queue[SandboxHandle] = field(init=False)
    _in_use: dict[str, list[SandboxHandle]] = field(default_factory=dict)
    _all_sandboxes: dict[str, SandboxHandle] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _draining: bool = field(default=False, init=False)
    _started: bool = field(default=False, init=False)
    _metrics: PoolMetrics = field(default_factory=PoolMetrics)

    # Background tasks
    _warmup_task: asyncio.Task[None] | None = field(default=None, init=False)
    _health_task: asyncio.Task[None] | None = field(default=None, init=False)
    _reaper_task: asyncio.Task[None] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Initialize queue."""
        self._available = asyncio.Queue()

    @property
    def metrics(self) -> PoolMetrics:
        """Get current pool metrics."""
        return self._metrics

    @property
    def is_draining(self) -> bool:
        """Check if pool is draining."""
        return self._draining

    async def start(self) -> None:
        """
        Start the pool.

        Initializes pre-warmed sandboxes and starts background tasks.
        """
        if self._started:
            logger.warning("Pool already started")
            return

        logger.info(
            f"Starting sandbox pool: provider={self.provider.name}, "
            f"min_warm={self.config.min_warm}, max_total={self.config.max_total}"
        )

        self._started = True
        self._draining = False

        # Pre-warm sandboxes
        await self._warmup_to_minimum()

        # Start background tasks
        self._warmup_task = asyncio.create_task(
            self._warmup_loop(),
            name="sandbox-pool-warmup",
        )
        self._health_task = asyncio.create_task(
            self._health_check_loop(),
            name="sandbox-pool-health",
        )
        self._reaper_task = asyncio.create_task(
            self._idle_reaper_loop(),
            name="sandbox-pool-reaper",
        )

        logger.info(f"Pool started with {self._available.qsize()} warm sandboxes")

    async def stop(self) -> None:
        """
        Stop the pool.

        Cancels background tasks but does not teardown sandboxes.
        Use drain() for graceful shutdown.
        """
        logger.info("Stopping sandbox pool")
        self._started = False

        # Cancel background tasks
        for task in [self._warmup_task, self._health_task, self._reaper_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("Pool stopped")

    async def drain(self, timeout: float = 60.0) -> None:
        """
        Gracefully drain the pool for deployment.

        Stops accepting new requests and waits for in-use sandboxes
        to be released before tearing them down.

        Args:
            timeout: Maximum time to wait for in-use sandboxes.
        """
        logger.info(f"Draining pool with timeout={timeout}s")
        self._draining = True

        # Wait for in-use sandboxes to be released
        deadline = time.monotonic() + timeout
        while self._in_use and time.monotonic() < deadline:
            in_use_count = sum(len(v) for v in self._in_use.values())
            logger.info(f"Waiting for {in_use_count} in-use sandboxes")
            await asyncio.sleep(1)

        # Stop background tasks
        await self.stop()

        # Teardown all sandboxes
        async with self._lock:
            teardown_tasks = []
            for sandbox in list(self._all_sandboxes.values()):
                teardown_tasks.append(self._teardown_sandbox(sandbox))

            if teardown_tasks:
                await asyncio.gather(*teardown_tasks, return_exceptions=True)

            self._all_sandboxes.clear()

            # Clear available queue
            while not self._available.empty():
                try:
                    self._available.get_nowait()
                except asyncio.QueueEmpty:
                    break

        logger.info("Pool drained")

    @asynccontextmanager
    async def acquire(
        self,
        session_id: str,
        config: SandboxConfig | None = None,
    ) -> AsyncIterator[SandboxHandle]:
        """
        Acquire a sandbox from the pool.

        Context manager that automatically releases sandbox on exit.

        Args:
            session_id: Session requesting the sandbox.
            config: Optional custom config (uses default if None).

        Yields:
            SandboxHandle ready for use.

        Raises:
            PoolDrainingError: If pool is draining.
            PoolExhaustedError: If pool exhausted and timeout.
        """
        if self._draining:
            raise PoolDrainingError()

        self._metrics.acquires_total += 1
        sandbox = await self._acquire_sandbox(session_id, config)

        try:
            yield sandbox
        finally:
            await self._release_sandbox(session_id, sandbox)

    async def _acquire_sandbox(
        self,
        session_id: str,
        config: SandboxConfig | None,
    ) -> SandboxHandle:
        """Acquire sandbox from pool or provision new one."""
        effective_config = config or self.config.default_sandbox_config

        # Check per-session limit
        async with self._lock:
            session_sandboxes = self._in_use.get(session_id, [])
            if len(session_sandboxes) >= self.config.max_per_session:
                raise PoolExhaustedError(
                    self.config.max_per_session,
                    0,
                )

        # Try to get from available queue
        try:
            sandbox = self._available.get_nowait()
            logger.debug(f"Acquired warm sandbox: {sandbox.id}")
        except asyncio.QueueEmpty:
            # Check if we can provision more
            async with self._lock:
                total = len(self._all_sandboxes)
                if total >= self.config.max_total:
                    # Wait for one to become available
                    self._metrics.acquires_waited += 1
                    try:
                        sandbox = await asyncio.wait_for(
                            self._available.get(),
                            timeout=self.config.acquire_timeout,
                        )
                    except asyncio.TimeoutError:
                        self._metrics.acquires_timeout += 1
                        raise PoolExhaustedError(
                            self.config.max_total,
                            self.config.acquire_timeout,
                        )
                else:
                    # Provision new sandbox
                    sandbox = await self._provision_sandbox(effective_config)

        # Mark as in-use
        async with self._lock:
            sandbox.status = SandboxStatus.IN_USE
            sandbox.session_id = session_id
            sandbox.last_used_at = None  # Clear for tracking

            if session_id not in self._in_use:
                self._in_use[session_id] = []
            self._in_use[session_id].append(sandbox)
            self._update_metrics()

        return sandbox

    async def _release_sandbox(
        self,
        session_id: str,
        sandbox: SandboxHandle,
    ) -> None:
        """Release sandbox back to pool or teardown if unhealthy."""
        async with self._lock:
            # Remove from in-use
            if session_id in self._in_use:
                try:
                    self._in_use[session_id].remove(sandbox)
                    if not self._in_use[session_id]:
                        del self._in_use[session_id]
                except ValueError:
                    pass  # Already removed

        # Health check before returning to pool
        try:
            is_healthy = await asyncio.wait_for(
                self.provider.health_check(sandbox),
                timeout=5.0,
            )
        except Exception:
            is_healthy = False

        if is_healthy and not self._draining:
            # Try to reset sandbox
            reset_ok = await self.provider.reset(sandbox)

            sandbox.status = SandboxStatus.READY
            sandbox.session_id = None
            sandbox.last_used_at = asyncio.get_event_loop().time()  # type: ignore

            await self._available.put(sandbox)
            logger.debug(f"Released sandbox to pool: {sandbox.id}, reset={reset_ok}")
        else:
            # Teardown unhealthy sandbox
            logger.warning(f"Sandbox unhealthy, tearing down: {sandbox.id}")
            await self._teardown_sandbox(sandbox)

        async with self._lock:
            self._update_metrics()

    async def _provision_sandbox(self, config: SandboxConfig) -> SandboxHandle:
        """Provision a new sandbox."""
        self._metrics.provisions_total += 1

        try:
            sandbox = await asyncio.wait_for(
                self.provider.provision(config),
                timeout=self.config.provision_timeout,
            )

            async with self._lock:
                self._all_sandboxes[sandbox.id] = sandbox
                self._update_metrics()

            logger.info(f"Provisioned sandbox: {sandbox.id}")
            return sandbox

        except Exception as e:
            self._metrics.provisions_failed += 1
            logger.error(f"Failed to provision sandbox: {e}")
            raise ProvisionError(str(e), self.provider.name)

    async def _teardown_sandbox(self, sandbox: SandboxHandle) -> None:
        """Teardown a sandbox."""
        self._metrics.teardowns_total += 1

        try:
            sandbox.status = SandboxStatus.TERMINATED
            await self.provider.teardown(sandbox)

            async with self._lock:
                self._all_sandboxes.pop(sandbox.id, None)
                self._update_metrics()

            logger.debug(f"Tore down sandbox: {sandbox.id}")

        except Exception as e:
            logger.error(f"Failed to teardown sandbox {sandbox.id}: {e}")

    async def _warmup_to_minimum(self) -> None:
        """Pre-warm sandboxes to minimum level."""
        current = self._available.qsize()
        needed = self.config.min_warm - current

        if needed <= 0:
            return

        logger.info(f"Pre-warming {needed} sandboxes")

        # Provision in batches
        for i in range(0, needed, self.config.warmup_batch_size):
            batch_size = min(self.config.warmup_batch_size, needed - i)
            tasks = [
                self._provision_sandbox(self.config.default_sandbox_config)
                for _ in range(batch_size)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, SandboxHandle):
                    await self._available.put(result)
                else:
                    logger.warning(f"Warmup provisioning failed: {result}")

    async def _warmup_loop(self) -> None:
        """Background task to maintain warm pool."""
        while self._started and not self._draining:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds

                current_warm = self._available.qsize()
                if current_warm < self.config.min_warm:
                    needed = self.config.min_warm - current_warm
                    logger.debug(f"Warming {needed} sandboxes")

                    for _ in range(min(needed, self.config.warmup_batch_size)):
                        async with self._lock:
                            total = len(self._all_sandboxes)
                            if total >= self.config.max_total:
                                break

                        try:
                            sandbox = await self._provision_sandbox(
                                self.config.default_sandbox_config
                            )
                            await self._available.put(sandbox)
                        except Exception as e:
                            logger.warning(f"Warmup failed: {e}")
                            break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Warmup loop error: {e}")

    async def _health_check_loop(self) -> None:
        """Background task for periodic health checks."""
        while self._started and not self._draining:
            try:
                await asyncio.sleep(self.config.health_interval)

                # Get sandboxes to check (copy to avoid lock during check)
                async with self._lock:
                    to_check = list(self._all_sandboxes.values())

                for sandbox in to_check:
                    if sandbox.status != SandboxStatus.READY:
                        continue  # Skip in-use sandboxes

                    self._metrics.health_checks_total += 1

                    try:
                        is_healthy = await asyncio.wait_for(
                            self.provider.health_check(sandbox),
                            timeout=10.0,
                        )

                        if not is_healthy:
                            self._metrics.health_checks_failed += 1
                            logger.warning(
                                f"Health check failed for {sandbox.id}, marking unhealthy"
                            )
                            sandbox.status = SandboxStatus.UNHEALTHY

                    except Exception as e:
                        self._metrics.health_checks_failed += 1
                        logger.warning(
                            f"Health check error for {sandbox.id}: {e}"
                        )
                        sandbox.status = SandboxStatus.UNHEALTHY

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check loop error: {e}")

    async def _idle_reaper_loop(self) -> None:
        """Background task to reap idle sandboxes."""
        while self._started and not self._draining:
            try:
                await asyncio.sleep(60)  # Check every minute

                now = time.monotonic()

                # Find idle sandboxes to reap
                to_reap = []
                temp_sandboxes = []

                # Drain available queue to check
                while not self._available.empty():
                    try:
                        sandbox = self._available.get_nowait()

                        if sandbox.status == SandboxStatus.UNHEALTHY:
                            to_reap.append(sandbox)
                        elif sandbox.last_used_at:
                            idle_time = now - sandbox.last_used_at
                            if idle_time > self.config.idle_timeout:
                                # Check if we're above minimum
                                current_warm = len(temp_sandboxes)
                                if current_warm >= self.config.min_warm:
                                    to_reap.append(sandbox)
                                else:
                                    temp_sandboxes.append(sandbox)
                            else:
                                temp_sandboxes.append(sandbox)
                        else:
                            temp_sandboxes.append(sandbox)

                    except asyncio.QueueEmpty:
                        break

                # Put healthy sandboxes back
                for sandbox in temp_sandboxes:
                    await self._available.put(sandbox)

                # Teardown reaped sandboxes
                for sandbox in to_reap:
                    logger.info(
                        f"Reaping idle/unhealthy sandbox: {sandbox.id}"
                    )
                    await self._teardown_sandbox(sandbox)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Idle reaper loop error: {e}")

    def _update_metrics(self) -> None:
        """Update pool metrics (call with lock held)."""
        self._metrics.total = len(self._all_sandboxes)
        self._metrics.in_use = sum(len(v) for v in self._in_use.values())
        self._metrics.warm = self._available.qsize()
        self._metrics.unhealthy = sum(
            1 for s in self._all_sandboxes.values()
            if s.status == SandboxStatus.UNHEALTHY
        )

    def get_stats(self) -> dict[str, Any]:
        """Get pool statistics for monitoring."""
        return {
            "provider": self.provider.name,
            "config": {
                "min_warm": self.config.min_warm,
                "max_total": self.config.max_total,
                "idle_timeout": self.config.idle_timeout,
            },
            "state": {
                "started": self._started,
                "draining": self._draining,
            },
            "metrics": {
                "total": self._metrics.total,
                "warm": self._metrics.warm,
                "in_use": self._metrics.in_use,
                "unhealthy": self._metrics.unhealthy,
                "provisions_total": self._metrics.provisions_total,
                "provisions_failed": self._metrics.provisions_failed,
                "teardowns_total": self._metrics.teardowns_total,
                "health_checks_total": self._metrics.health_checks_total,
                "health_checks_failed": self._metrics.health_checks_failed,
                "acquires_total": self._metrics.acquires_total,
                "acquires_waited": self._metrics.acquires_waited,
                "acquires_timeout": self._metrics.acquires_timeout,
            },
        }
