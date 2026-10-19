"""
Sandbox pool management with pre-warming and health checks.

Provides efficient sandbox lifecycle management for high-throughput
agent tool execution with:

- Pre-warming: Maintain pool of ready sandboxes for instant acquisition
- Health checks: Periodic validation of sandbox health
- Graceful drain: Controlled shutdown during deployments
- Provider abstraction: Pluggable sandbox backends (Docker, E2B, local)

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │  SandboxPool                                                 │
    │  - Maintains warm pool of ready sandboxes                    │
    │  - acquire() / release() with context manager               │
    │  - Background health check and pre-warming tasks            │
    └─────────────────────────────────────────────────────────────┘
                              │
    ┌─────────────────────────────────────────────────────────────┐
    │  SandboxProvider (Protocol)                                  │
    │  - provision(): Create new sandbox                           │
    │  - teardown(): Destroy sandbox                               │
    │  - health_check(): Validate sandbox is healthy               │
    └─────────────────────────────────────────────────────────────┘
                              │
    ┌──────────────┬──────────────┬──────────────┬───────────────┐
    │ DockerProvider│ E2BProvider  │ FirecrackerP │ LocalProcess  │
    └──────────────┴──────────────┴──────────────┴───────────────┘

Scaling for 1000 Users:
    - min_warm: 20-50 (instant acquisition for burst)
    - max_total: 100-200 per worker (with multiple workers)
    - idle_timeout: 300s (recycle idle sandboxes)
    - health_interval: 30s (detect unhealthy sandboxes)

Usage:
    ```python
    # Create pool
    pool = SandboxPool(
        provider=DockerSandboxProvider(config),
        config=PoolConfig(min_warm=20, max_total=100),
    )

    # Start pool (background tasks)
    await pool.start()

    # Use sandbox
    async with pool.acquire(session_id) as sandbox:
        result = await sandbox.execute("ls -la")

    # Graceful shutdown
    await pool.drain(timeout=60.0)
    ```
"""

from supervisor.sandbox.pool.manager import (
    SandboxPool,
    PoolConfig,
    PoolMetrics,
    PoolDrainingError,
    PoolExhaustedError,
)
from supervisor.sandbox.pool.provider import (
    SandboxProvider,
    SandboxHandle,
    SandboxConfig,
    SandboxStatus,
)

__all__ = [
    # Pool
    "SandboxPool",
    "PoolConfig",
    "PoolMetrics",
    "PoolDrainingError",
    "PoolExhaustedError",
    # Provider
    "SandboxProvider",
    "SandboxHandle",
    "SandboxConfig",
    "SandboxStatus",
]
