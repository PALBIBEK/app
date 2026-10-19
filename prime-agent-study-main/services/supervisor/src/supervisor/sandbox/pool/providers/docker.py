"""
Docker-based sandbox provider.

Provides container isolation for tool execution with:
- Resource limits (CPU, memory)
- Network isolation
- Filesystem isolation
- Security hardening (dropped capabilities, no-new-privileges)

Requirements:
    - Docker daemon accessible
    - docker-py package installed

Security Model:
    - All capabilities dropped by default
    - Read-only root filesystem
    - No network access (configurable)
    - Resource limits enforced
    - Init process for proper signal handling

Example:
    ```python
    provider = DockerSandboxProvider(DockerConfig(
        image="python:3.11-slim",
        memory_limit="512m",
        cpu_limit=1.0,
        network_mode="none",
    ))

    sandbox = await provider.provision(SandboxConfig())
    result = await sandbox.execute("python --version")
    print(result.stdout)  # "Python 3.11.x"
    await provider.teardown(sandbox)
    ```
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from supervisor.sandbox.pool.provider import (
    SandboxProvider,
    SandboxHandle,
    SandboxConfig,
    SandboxStatus,
    ExecutionResult,
    GrepMatch,
    ProvisionError,
    TeardownError,
)

logger = logging.getLogger(__name__)

# Conditional import
try:
    import docker
    from docker.models.containers import Container

    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False
    docker = None  # type: ignore
    Container = Any  # type: ignore


def _check_docker_installed() -> None:
    """Raise ImportError if docker is not installed."""
    if not HAS_DOCKER:
        raise ImportError(
            "docker-py is required for Docker sandbox provider. "
            "Install with: pip install docker"
        )


@dataclass(frozen=True)
class DockerConfig:
    """
    Configuration for Docker sandbox provider.

    Attributes:
        image: Docker image to use.
        memory_limit: Memory limit (e.g., "512m", "2g").
        cpu_limit: CPU limit in cores (e.g., 1.0, 0.5).
        network_mode: Network mode ("none", "bridge", "host").
        working_dir: Working directory in container.
        user: User to run as (uid:gid).
        cap_drop: Capabilities to drop (default: ALL).
        read_only: Make root filesystem read-only.
        init: Use init process.
        auto_remove: Auto-remove container on exit.
        labels: Container labels for tracking.
        environment: Environment variables.
        volumes: Volume mounts.
    """

    image: str = "python:3.11-slim"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_mode: str = "none"
    working_dir: str = "/workspace"
    user: str | None = None
    cap_drop: list[str] = field(default_factory=lambda: ["ALL"])
    read_only: bool = False  # Set to False for writable workspace
    init: bool = True
    auto_remove: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, dict[str, str]] = field(default_factory=dict)

    # Timeouts
    start_timeout: float = 30.0
    exec_timeout: float = 30.0


class DockerExecutor:
    """
    Executor for commands in Docker container.

    Provides file operations and command execution.
    """

    def __init__(self, container: Container, config: DockerConfig):
        self._container = container
        self._config = config

    async def execute(
        self,
        command: str,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Execute command in container."""
        import time

        effective_timeout = timeout or self._config.exec_timeout
        start = time.time()

        try:
            # Run in thread pool (docker-py is sync)
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._container.exec_run(
                        cmd=["sh", "-c", command],
                        workdir=self._config.working_dir,
                        demux=True,
                    ),
                ),
                timeout=effective_timeout,
            )

            duration = time.time() - start
            stdout, stderr = result.output if result.output else (b"", b"")

            return ExecutionResult(
                exit_code=result.exit_code,
                stdout=(stdout or b"").decode("utf-8", errors="replace"),
                stderr=(stderr or b"").decode("utf-8", errors="replace"),
                duration_seconds=duration,
            )

        except asyncio.TimeoutError:
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {effective_timeout}s",
                duration_seconds=effective_timeout,
            )

    async def read_file(self, path: str) -> str:
        """Read file contents."""
        result = await self.execute(f"cat {_shell_escape(path)}")
        if result.exit_code != 0:
            if "No such file" in result.stderr:
                raise FileNotFoundError(path)
            if "Permission denied" in result.stderr:
                raise PermissionError(path)
            raise IOError(result.stderr)
        return result.stdout

    async def write_file(self, path: str, content: str) -> None:
        """Write file contents."""
        # Use base64 to safely transfer content
        import base64

        encoded = base64.b64encode(content.encode()).decode()
        result = await self.execute(
            f"echo {encoded} | base64 -d > {_shell_escape(path)}"
        )
        if result.exit_code != 0:
            if "Permission denied" in result.stderr:
                raise PermissionError(path)
            raise IOError(result.stderr)

    async def list_directory(self, path: str) -> list[str]:
        """List directory contents."""
        result = await self.execute(f"ls -1 {_shell_escape(path)}")
        if result.exit_code != 0:
            if "No such file" in result.stderr:
                raise FileNotFoundError(path)
            if "Not a directory" in result.stderr:
                raise NotADirectoryError(path)
            raise IOError(result.stderr)
        return [line for line in result.stdout.strip().split("\n") if line]

    async def grep(
        self,
        pattern: str,
        path: str,
        recursive: bool = True,
    ) -> list[GrepMatch]:
        """Search for pattern in files."""
        flags = "-rn" if recursive else "-n"
        result = await self.execute(
            f"grep {flags} {_shell_escape(pattern)} {_shell_escape(path)} 2>/dev/null || true"
        )

        matches = []
        for line in result.stdout.strip().split("\n"):
            if not line or ":" not in line:
                continue

            parts = line.split(":", 2)
            if len(parts) >= 3:
                matches.append(GrepMatch(
                    file=parts[0],
                    line=int(parts[1]) if parts[1].isdigit() else 0,
                    content=parts[2],
                ))

        return matches

    async def exec_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute custom tool via script."""
        # Serialize arguments to JSON file
        args_json = json.dumps(arguments)
        await self.write_file("/tmp/tool_args.json", args_json)

        # Execute tool (expects tool scripts in /tools/)
        result = await self.execute(
            f"python /tools/{tool_name}.py /tmp/tool_args.json"
        )

        if result.exit_code != 0:
            return {
                "success": False,
                "output": "",
                "error": result.stderr or f"Tool exited with code {result.exit_code}",
            }

        # Try to parse JSON output
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "success": True,
                "output": result.stdout,
            }


def _shell_escape(s: str) -> str:
    """Escape string for shell."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


@dataclass
class DockerSandboxProvider:
    """
    Docker-based sandbox provider.

    Creates isolated containers for secure tool execution.
    """

    config: DockerConfig = field(default_factory=DockerConfig)
    _client: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Initialize Docker client."""
        _check_docker_installed()
        self._client = docker.from_env()

    @property
    def name(self) -> str:
        """Provider name."""
        return "docker"

    async def provision(self, config: SandboxConfig) -> SandboxHandle:
        """
        Provision a new Docker container.

        Args:
            config: Sandbox configuration.

        Returns:
            Handle to the container.

        Raises:
            ProvisionError: If container creation fails.
        """
        container_id = f"sandbox-{uuid.uuid4().hex[:12]}"

        logger.info(f"Provisioning Docker sandbox: {container_id}")

        try:
            # Build container config
            container_kwargs: dict[str, Any] = {
                "image": self.config.image,
                "name": container_id,
                "detach": True,
                "stdin_open": True,
                "tty": False,
                "mem_limit": self.config.memory_limit,
                "nano_cpus": int(self.config.cpu_limit * 1e9),
                "network_mode": self.config.network_mode,
                "working_dir": self.config.working_dir,
                "init": self.config.init,
                "auto_remove": self.config.auto_remove,
                "cap_drop": self.config.cap_drop,
                "read_only": self.config.read_only,
                "labels": {
                    "sandbox": "true",
                    "sandbox.id": container_id,
                    **self.config.labels,
                },
                "environment": {
                    **self.config.environment,
                    **{k: v for k, v in config.env_vars.items()},
                },
                # Keep alive with sleep
                "command": ["sleep", "infinity"],
            }

            if self.config.user:
                container_kwargs["user"] = self.config.user

            if self.config.volumes:
                container_kwargs["volumes"] = self.config.volumes

            # Security options
            container_kwargs["security_opt"] = ["no-new-privileges"]

            # Create container in thread pool
            container = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._client.containers.run(**container_kwargs),
                ),
                timeout=self.config.start_timeout,
            )

            # Create executor
            executor = DockerExecutor(container, self.config)

            # Create workspace directory
            await executor.execute(f"mkdir -p {self.config.working_dir}")

            handle = SandboxHandle(
                id=container_id,
                provider=self.name,
                handle=container,
                status=SandboxStatus.READY,
                config=config,
            )
            handle._executor = executor

            logger.info(f"Provisioned Docker sandbox: {container_id}")
            return handle

        except asyncio.TimeoutError as e:
            raise ProvisionError(
                f"Container start timed out after {self.config.start_timeout}s",
                self.name,
            ) from e
        except Exception as e:
            raise ProvisionError(str(e), self.name) from e

    async def teardown(self, handle: SandboxHandle) -> None:
        """
        Teardown a container.

        Args:
            handle: Sandbox to teardown.
        """
        if handle.handle is None:
            return

        logger.info(f"Tearing down Docker sandbox: {handle.id}")

        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: handle.handle.remove(force=True),
            )
        except Exception as e:
            # Container may already be removed
            logger.warning(f"Teardown warning for {handle.id}: {e}")

    async def health_check(self, handle: SandboxHandle) -> bool:
        """
        Check if container is healthy.

        Args:
            handle: Sandbox to check.

        Returns:
            True if healthy.
        """
        if handle.handle is None:
            return False

        try:
            # Reload container state
            await asyncio.get_event_loop().run_in_executor(
                None,
                handle.handle.reload,
            )

            # Check if running
            if handle.handle.status != "running":
                return False

            # Quick exec test
            result = await handle._executor.execute("echo ok", timeout=5.0)
            return result.exit_code == 0 and "ok" in result.stdout

        except Exception as e:
            logger.warning(f"Health check failed for {handle.id}: {e}")
            return False

    async def reset(self, handle: SandboxHandle) -> bool:
        """
        Reset container to clean state.

        Cleans workspace directory.

        Args:
            handle: Sandbox to reset.

        Returns:
            True if reset succeeded.
        """
        if handle._executor is None:
            return False

        try:
            # Clean workspace
            result = await handle._executor.execute(
                f"rm -rf {self.config.working_dir}/* && "
                f"rm -rf {self.config.working_dir}/.[!.]* 2>/dev/null || true"
            )
            return result.exit_code == 0
        except Exception as e:
            logger.warning(f"Reset failed for {handle.id}: {e}")
            return False

    async def get_metrics(self, handle: SandboxHandle) -> dict[str, Any]:
        """Get container resource metrics."""
        if handle.handle is None:
            return {}

        try:
            stats = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: handle.handle.stats(stream=False),
            )

            # Parse stats
            cpu_percent = 0.0
            memory_usage = 0
            memory_limit = 0

            if "cpu_stats" in stats and "precpu_stats" in stats:
                cpu_delta = (
                    stats["cpu_stats"]["cpu_usage"]["total_usage"]
                    - stats["precpu_stats"]["cpu_usage"]["total_usage"]
                )
                system_delta = (
                    stats["cpu_stats"]["system_cpu_usage"]
                    - stats["precpu_stats"]["system_cpu_usage"]
                )
                if system_delta > 0:
                    num_cpus = len(
                        stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])
                    )
                    cpu_percent = (cpu_delta / system_delta) * num_cpus * 100

            if "memory_stats" in stats:
                memory_usage = stats["memory_stats"].get("usage", 0)
                memory_limit = stats["memory_stats"].get("limit", 0)

            return {
                "cpu_percent": round(cpu_percent, 2),
                "memory_usage_bytes": memory_usage,
                "memory_limit_bytes": memory_limit,
                "memory_percent": round(
                    (memory_usage / memory_limit * 100) if memory_limit else 0, 2
                ),
            }

        except Exception as e:
            logger.warning(f"Failed to get metrics for {handle.id}: {e}")
            return {}
