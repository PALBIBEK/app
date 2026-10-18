"""
Temporal client and worker configuration.

Provides production-ready client and worker setup with:
- Connection pooling and retry
- TLS/mTLS support for Temporal Cloud
- Graceful shutdown handling
- Interceptors for observability
- Namespace isolation

Connection Modes:
    1. Local development: localhost:7233, no auth
    2. Self-hosted: Custom host, optional mTLS
    3. Temporal Cloud: *.tmprl.cloud, API key or mTLS

Worker Scaling:
    - One worker per process
    - Horizontal scaling via Kubernetes replicas
    - Task queue partitioning for high throughput
    - Sticky execution for session affinity

Example:
    ```python
    # Development
    client = await create_temporal_client()

    # Production with mTLS
    client = await create_temporal_client(
        TemporalConfig(
            host="myns.abc123.tmprl.cloud:7233",
            namespace="production",
            tls_cert_path="/etc/temporal/client.pem",
            tls_key_path="/etc/temporal/client.key",
        )
    )

    # Create worker
    worker = create_worker(
        client,
        WorkerConfig(
            task_queue="agent-sessions",
            max_concurrent_workflow_tasks=100,
            max_concurrent_activities=50,
        ),
        workflows=[AgentSessionWorkflow, AgentTurnWorkflow],
        activities=[call_llm, execute_tool, checkpoint],
    )

    async with worker:
        await worker.run()
    ```
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

# Conditional import for optional dependency
try:
    from temporalio.client import Client, TLSConfig
    from temporalio.worker import Worker
    from temporalio.runtime import Runtime, TelemetryConfig, PrometheusConfig
    from temporalio.service import ServiceClient
    from temporalio.converter import DataConverter

    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False
    Client = Any  # type: ignore
    Worker = Any  # type: ignore


def _check_temporal_installed() -> None:
    """Raise ImportError if temporalio is not installed."""
    if not HAS_TEMPORAL:
        raise ImportError(
            "temporalio is required for Temporal integration. "
            "Install with: pip install 'shared[temporal]' or pip install temporalio"
        )


@dataclass(frozen=True)
class TemporalConfig:
    """
    Configuration for Temporal client connection.

    Supports three connection modes:
    1. Local: No TLS, default localhost:7233
    2. Self-hosted: Optional mTLS with cert/key files
    3. Cloud: API key authentication or mTLS

    Attributes:
        host: Temporal server address (host:port).
        namespace: Temporal namespace (isolation boundary).
        tls_cert_path: Path to client certificate (PEM).
        tls_key_path: Path to client private key (PEM).
        tls_ca_path: Path to CA certificate (PEM) for verification.
        api_key: API key for Temporal Cloud authentication.
        identity: Worker identity for debugging.
        data_converter: Custom data converter (default: JSON).
        rpc_timeout_seconds: Timeout for RPC calls.
        keep_alive_interval_seconds: gRPC keepalive interval.

    Example:
        ```python
        # Local development
        config = TemporalConfig()

        # Temporal Cloud with API key
        config = TemporalConfig(
            host="myns.abc123.tmprl.cloud:7233",
            namespace="production.abc123",
            api_key="your-api-key",
        )

        # Self-hosted with mTLS
        config = TemporalConfig(
            host="temporal.internal:7233",
            namespace="agents",
            tls_cert_path="/certs/client.pem",
            tls_key_path="/certs/client.key",
            tls_ca_path="/certs/ca.pem",
        )
        ```
    """

    host: str = "localhost:7233"
    namespace: str = "default"
    tls_cert_path: str | None = None
    tls_key_path: str | None = None
    tls_ca_path: str | None = None
    api_key: str | None = None
    identity: str | None = None
    data_converter: Any = None  # DataConverter
    rpc_timeout_seconds: float = 30.0
    keep_alive_interval_seconds: float = 30.0

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.tls_cert_path and not self.tls_key_path:
            raise ValueError("tls_key_path required when tls_cert_path is set")
        if self.tls_key_path and not self.tls_cert_path:
            raise ValueError("tls_cert_path required when tls_key_path is set")

    @property
    def uses_tls(self) -> bool:
        """Check if TLS is configured."""
        return bool(self.tls_cert_path) or bool(self.api_key) or "tmprl.cloud" in self.host

    @property
    def is_cloud(self) -> bool:
        """Check if connecting to Temporal Cloud."""
        return "tmprl.cloud" in self.host

    def build_tls_config(self) -> TLSConfig | None:
        """
        Build TLS configuration for Temporal client.

        Returns:
            TLSConfig if TLS is needed, None otherwise.
        """
        _check_temporal_installed()

        if not self.uses_tls:
            return None

        # Load certificate and key if provided
        client_cert: bytes | None = None
        client_key: bytes | None = None
        server_root_ca: bytes | None = None

        if self.tls_cert_path:
            client_cert = Path(self.tls_cert_path).read_bytes()
        if self.tls_key_path:
            client_key = Path(self.tls_key_path).read_bytes()
        if self.tls_ca_path:
            server_root_ca = Path(self.tls_ca_path).read_bytes()

        return TLSConfig(
            client_cert=client_cert,
            client_private_key=client_key,
            server_root_ca_cert=server_root_ca,
        )


# Type alias for the actual Temporal client
TemporalClient = Client


async def create_temporal_client(
    config: TemporalConfig | None = None,
    *,
    host: str | None = None,
    namespace: str | None = None,
    api_key: str | None = None,
) -> TemporalClient:
    """
    Create a connected Temporal client.

    Supports both config object and convenience kwargs.

    Args:
        config: Full configuration object.
        host: Override host (convenience).
        namespace: Override namespace (convenience).
        api_key: Override API key (convenience).

    Returns:
        Connected Temporal Client.

    Raises:
        ImportError: If temporalio is not installed.
        RuntimeError: If connection fails.

    Example:
        ```python
        # Development (defaults)
        client = await create_temporal_client()

        # Production with config
        client = await create_temporal_client(
            TemporalConfig(
                host="temporal.internal:7233",
                namespace="agents",
            )
        )

        # Quick override
        client = await create_temporal_client(
            namespace="testing",
        )
        ```
    """
    _check_temporal_installed()

    # Build effective config
    if config is None:
        config = TemporalConfig()

    effective_host = host or config.host
    effective_namespace = namespace or config.namespace
    effective_api_key = api_key or config.api_key

    logger.info(
        f"Connecting to Temporal: host={effective_host}, "
        f"namespace={effective_namespace}, "
        f"tls={config.uses_tls}"
    )

    # Build connection kwargs
    connect_kwargs: dict[str, Any] = {
        "target_host": effective_host,
        "namespace": effective_namespace,
    }

    # TLS configuration
    tls_config = config.build_tls_config()
    if tls_config:
        connect_kwargs["tls"] = tls_config

    # API key for Temporal Cloud
    if effective_api_key:
        connect_kwargs["api_key"] = effective_api_key

    # Identity for debugging
    if config.identity:
        connect_kwargs["identity"] = config.identity

    # Custom data converter
    if config.data_converter:
        connect_kwargs["data_converter"] = config.data_converter

    try:
        client = await Client.connect(**connect_kwargs)
        logger.info(f"Connected to Temporal namespace: {effective_namespace}")
        return client
    except Exception as e:
        logger.error(f"Failed to connect to Temporal: {e}")
        raise RuntimeError(f"Temporal connection failed: {e}") from e


@dataclass(frozen=True)
class WorkerConfig:
    """
    Configuration for Temporal worker.

    Tuning Guidelines for 1000 concurrent users:
        - max_concurrent_workflow_tasks: 100-200 (CPU-bound)
        - max_concurrent_activities: 50-100 (I/O-bound)
        - max_cached_workflows: 1000+ (memory for sticky execution)
        - Scale horizontally with Kubernetes replicas

    Attributes:
        task_queue: Task queue name for routing.
        max_concurrent_workflow_tasks: Max concurrent workflow tasks.
        max_concurrent_activities: Max concurrent activity executions.
        max_concurrent_local_activities: Max local activities.
        max_cached_workflows: Max workflows in sticky cache.
        enable_session_worker: Enable session-based execution.
        graceful_shutdown_timeout_seconds: Time to wait during shutdown.
        build_id: Build ID for versioning (worker versioning).

    Example:
        ```python
        config = WorkerConfig(
            task_queue="agent-sessions",
            max_concurrent_workflow_tasks=100,
            max_concurrent_activities=50,
            max_cached_workflows=1000,
        )
        ```
    """

    task_queue: str = "agent-sessions"
    max_concurrent_workflow_tasks: int = 100
    max_concurrent_activities: int = 50
    max_concurrent_local_activities: int = 50
    max_cached_workflows: int = 1000
    enable_session_worker: bool = False
    graceful_shutdown_timeout_seconds: float = 30.0
    build_id: str | None = None

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.max_concurrent_workflow_tasks < 1:
            raise ValueError("max_concurrent_workflow_tasks must be >= 1")
        if self.max_concurrent_activities < 1:
            raise ValueError("max_concurrent_activities must be >= 1")
        if not self.task_queue:
            raise ValueError("task_queue is required")


def create_worker(
    client: TemporalClient,
    config: WorkerConfig,
    *,
    workflows: Sequence[type] | None = None,
    activities: Sequence[Callable[..., Any]] | None = None,
    interceptors: Sequence[Any] | None = None,
) -> Worker:
    """
    Create a Temporal worker with the given configuration.

    The worker handles both workflow and activity execution.
    Use async context manager for lifecycle management.

    Args:
        client: Connected Temporal client.
        config: Worker configuration.
        workflows: Workflow classes to register.
        activities: Activity functions to register.
        interceptors: Worker interceptors (tracing, logging).

    Returns:
        Configured Worker (not yet running).

    Example:
        ```python
        worker = create_worker(
            client,
            WorkerConfig(task_queue="agent-sessions"),
            workflows=[AgentSessionWorkflow, AgentTurnWorkflow],
            activities=[call_llm, execute_tool, checkpoint],
        )

        # Run with lifecycle management
        async with worker:
            await asyncio.Future()  # Run forever
        ```
    """
    _check_temporal_installed()

    logger.info(
        f"Creating worker: queue={config.task_queue}, "
        f"workflows={config.max_concurrent_workflow_tasks}, "
        f"activities={config.max_concurrent_activities}"
    )

    worker_kwargs: dict[str, Any] = {
        "client": client,
        "task_queue": config.task_queue,
        "max_concurrent_workflow_tasks": config.max_concurrent_workflow_tasks,
        "max_concurrent_activities": config.max_concurrent_activities,
        "max_concurrent_local_activities": config.max_concurrent_local_activities,
        "max_cached_workflows": config.max_cached_workflows,
        "graceful_shutdown_timeout": asyncio.timedelta(
            seconds=config.graceful_shutdown_timeout_seconds
        ),
    }

    if workflows:
        worker_kwargs["workflows"] = list(workflows)

    if activities:
        worker_kwargs["activities"] = list(activities)

    if interceptors:
        worker_kwargs["interceptors"] = list(interceptors)

    if config.build_id:
        worker_kwargs["build_id"] = config.build_id
        worker_kwargs["use_worker_versioning"] = True

    return Worker(**worker_kwargs)


class WorkerManager:
    """
    Manages multiple workers with graceful shutdown.

    Useful for running multiple task queues in a single process
    or for gradual worker replacement during deployments.

    Example:
        ```python
        manager = WorkerManager()

        # Add workers
        manager.add_worker(session_worker)
        manager.add_worker(activity_worker)

        # Run all workers
        try:
            await manager.run()
        finally:
            await manager.shutdown()
        ```
    """

    def __init__(self) -> None:
        """Initialize worker manager."""
        self._workers: list[Worker] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._shutdown_event = asyncio.Event()

    def add_worker(self, worker: Worker) -> None:
        """
        Add a worker to be managed.

        Args:
            worker: Worker instance to add.
        """
        self._workers.append(worker)

    async def run(self) -> None:
        """
        Run all workers until shutdown is requested.

        Raises:
            Exception: If any worker fails.
        """
        if not self._workers:
            logger.warning("No workers to run")
            return

        logger.info(f"Starting {len(self._workers)} workers")

        # Start all workers
        self._tasks = [
            asyncio.create_task(worker.run())
            for worker in self._workers
        ]

        # Wait for shutdown or any worker failure
        try:
            done, pending = await asyncio.wait(
                self._tasks + [asyncio.create_task(self._shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Check for worker failures
            for task in done:
                if task.exception() and not self._shutdown_event.is_set():
                    raise task.exception()  # type: ignore

        finally:
            await self.shutdown()

    async def shutdown(self, timeout: float = 30.0) -> None:
        """
        Gracefully shutdown all workers.

        Args:
            timeout: Maximum time to wait for shutdown.
        """
        logger.info("Shutting down workers")
        self._shutdown_event.set()

        # Cancel all worker tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Wait for cancellation
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=timeout)

        logger.info("Workers shutdown complete")

    def request_shutdown(self) -> None:
        """Request graceful shutdown (can be called from signal handler)."""
        self._shutdown_event.set()
