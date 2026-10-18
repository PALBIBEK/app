"""
Sandbox provider protocol and base types.

Defines the interface that all sandbox providers must implement,
enabling pluggable sandbox backends (Docker, E2B, Firecracker, local).

Provider Responsibilities:
    1. provision(): Create isolated sandbox environment
    2. teardown(): Clean up sandbox resources
    3. health_check(): Verify sandbox is operational
    4. reset(): (Optional) Reset sandbox to clean state

Sandbox Lifecycle:
    1. provision() → SandboxHandle (ready to use)
    2. Use for tool execution
    3. reset() → Reuse (if supported)
    4. teardown() → Cleanup

Example Provider:
    ```python
    class DockerSandboxProvider:
        async def provision(self, config: SandboxConfig) -> SandboxHandle:
            container = await self._client.containers.run(
                image=config.image,
                detach=True,
                mem_limit=config.memory_limit,
            )
            return SandboxHandle(
                id=container.id,
                provider="docker",
                handle=container,
            )

        async def teardown(self, handle: SandboxHandle) -> None:
            await handle.handle.remove(force=True)

        async def health_check(self, handle: SandboxHandle) -> bool:
            try:
                await handle.handle.exec_run("echo ok")
                return True
            except:
                return False
    ```
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class SandboxStatus(str, Enum):
    """Sandbox lifecycle states."""

    PROVISIONING = "provisioning"
    READY = "ready"
    IN_USE = "in_use"
    UNHEALTHY = "unhealthy"
    DRAINING = "draining"
    TERMINATED = "terminated"


@dataclass
class SandboxConfig:
    """
    Configuration for sandbox provisioning.

    Attributes:
        image: Container/VM image to use.
        memory_limit: Memory limit (e.g., "512m", "2g").
        cpu_limit: CPU limit (cores or millicores).
        timeout_seconds: Maximum sandbox lifetime.
        network_enabled: Whether to enable networking.
        env_vars: Environment variables to set.
        volumes: Volume mounts (host:container).
        labels: Metadata labels for tracking.
    """

    image: str = "python:3.11-slim"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout_seconds: float = 3600.0  # 1 hour max
    network_enabled: bool = False  # Isolated by default
    env_vars: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    # Security settings
    capabilities_drop: list[str] = field(
        default_factory=lambda: ["ALL"]
    )
    read_only_root: bool = True
    no_new_privileges: bool = True


@dataclass
class SandboxHandle:
    """
    Handle to a provisioned sandbox.

    Holds the sandbox ID and provider-specific handle for operations.
    """

    id: str
    provider: str
    handle: Any = None  # Provider-specific handle
    status: SandboxStatus = SandboxStatus.READY
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = None
    session_id: str | None = None
    config: SandboxConfig | None = None

    # Execution interface (implemented by provider)
    _executor: Any = None

    async def execute(self, command: str, timeout: float = 30.0) -> ExecutionResult:
        """Execute command in sandbox."""
        if self._executor is None:
            raise RuntimeError("Sandbox executor not configured")
        return await self._executor.execute(command, timeout=timeout)

    async def read_file(self, path: str) -> str:
        """Read file from sandbox."""
        if self._executor is None:
            raise RuntimeError("Sandbox executor not configured")
        return await self._executor.read_file(path)

    async def write_file(self, path: str, content: str) -> None:
        """Write file to sandbox."""
        if self._executor is None:
            raise RuntimeError("Sandbox executor not configured")
        await self._executor.write_file(path, content)

    async def list_directory(self, path: str) -> list[str]:
        """List directory contents."""
        if self._executor is None:
            raise RuntimeError("Sandbox executor not configured")
        return await self._executor.list_directory(path)

    async def grep(
        self,
        pattern: str,
        path: str,
        recursive: bool = True,
    ) -> list[GrepMatch]:
        """Search files for pattern."""
        if self._executor is None:
            raise RuntimeError("Sandbox executor not configured")
        return await self._executor.grep(pattern, path, recursive=recursive)

    async def exec_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute custom tool."""
        if self._executor is None:
            raise RuntimeError("Sandbox executor not configured")
        return await self._executor.exec_tool(tool_name, arguments)


@dataclass
class ExecutionResult:
    """Result of command execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0


@dataclass
class GrepMatch:
    """Single grep match."""

    file: str
    line: int
    content: str


@runtime_checkable
class SandboxProvider(Protocol):
    """
    Protocol for sandbox providers.

    Implement this protocol to add a new sandbox backend
    (Docker, E2B, Firecracker, Kubernetes, etc.).
    """

    @property
    def name(self) -> str:
        """Provider name for logging/metrics."""
        ...

    @abstractmethod
    async def provision(self, config: SandboxConfig) -> SandboxHandle:
        """
        Provision a new sandbox.

        Creates an isolated execution environment.

        Args:
            config: Sandbox configuration.

        Returns:
            Handle to the provisioned sandbox.

        Raises:
            ProvisionError: If provisioning fails.
        """
        ...

    @abstractmethod
    async def teardown(self, handle: SandboxHandle) -> None:
        """
        Teardown a sandbox.

        Releases all resources associated with the sandbox.
        Should be idempotent (safe to call multiple times).

        Args:
            handle: Sandbox to teardown.
        """
        ...

    @abstractmethod
    async def health_check(self, handle: SandboxHandle) -> bool:
        """
        Check if sandbox is healthy.

        Quick check to verify sandbox is operational.
        Should complete within a few seconds.

        Args:
            handle: Sandbox to check.

        Returns:
            True if healthy, False otherwise.
        """
        ...

    async def reset(self, handle: SandboxHandle) -> bool:
        """
        Reset sandbox to clean state (optional).

        Allows sandbox reuse without full teardown/provision.
        Returns False if reset is not supported.

        Args:
            handle: Sandbox to reset.

        Returns:
            True if reset succeeded, False if not supported.
        """
        return False

    async def get_metrics(self, handle: SandboxHandle) -> dict[str, Any]:
        """
        Get sandbox resource metrics (optional).

        Returns CPU, memory, network usage if available.

        Args:
            handle: Sandbox to get metrics for.

        Returns:
            Dict of metrics (empty if not supported).
        """
        return {}


class ProvisionError(Exception):
    """Error during sandbox provisioning."""

    def __init__(self, message: str, provider: str, retryable: bool = True):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class TeardownError(Exception):
    """Error during sandbox teardown."""

    def __init__(self, message: str, sandbox_id: str):
        super().__init__(message)
        self.sandbox_id = sandbox_id


class HealthCheckError(Exception):
    """Error during health check."""

    def __init__(self, message: str, sandbox_id: str):
        super().__init__(message)
        self.sandbox_id = sandbox_id
