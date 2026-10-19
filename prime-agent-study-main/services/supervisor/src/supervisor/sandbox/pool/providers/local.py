"""
Local process sandbox provider for development.

Provides minimal isolation via subprocess execution. NOT suitable for
production use - only for local development and testing.

Security Model:
    - NO isolation - runs as current user
    - NO resource limits
    - Full filesystem access (within working directory)
    - Full network access

Use Cases:
    - Local development without Docker
    - CI/CD testing
    - Quick prototyping

Example:
    ```python
    provider = LocalProcessProvider(LocalConfig(
        working_dir="/tmp/sandbox",
    ))

    sandbox = await provider.provision(SandboxConfig())
    result = await sandbox.execute("echo hello")
    print(result.stdout)  # "hello"
    await provider.teardown(sandbox)
    ```
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from supervisor.sandbox.pool.provider import (
    ExecutionResult,
    GrepMatch,
    ProvisionError,
    SandboxConfig,
    SandboxHandle,
    SandboxStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalConfig:
    """
    Configuration for local process provider.

    Attributes:
        base_dir: Base directory for sandboxes (default: temp).
        shell: Shell to use for execution.
        timeout: Default command timeout.
        cleanup_on_teardown: Remove sandbox directory on teardown.
    """

    base_dir: str | None = None  # Uses tempdir if None
    shell: str = "/bin/sh"
    timeout: float = 30.0
    cleanup_on_teardown: bool = True


class LocalExecutor:
    """Executor for local subprocess commands."""

    def __init__(self, working_dir: Path, config: LocalConfig):
        self._working_dir = working_dir
        self._config = config

    async def execute(
        self,
        command: str,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Execute command via subprocess."""
        import time

        effective_timeout = timeout or self._config.timeout
        start = time.time()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self._working_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            duration = time.time() - start

            return ExecutionResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                duration_seconds=duration,
            )

        except TimeoutError:
            # Kill process on timeout
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {effective_timeout}s",
                duration_seconds=effective_timeout,
            )

    async def read_file(self, path: str) -> str:
        """Read file contents."""
        full_path = self._resolve_path(path)

        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                full_path.read_text,
            )
        except FileNotFoundError:
            raise FileNotFoundError(path)
        except PermissionError:
            raise PermissionError(path)

    async def write_file(self, path: str, content: str) -> None:
        """Write file contents."""
        full_path = self._resolve_path(path)

        # Ensure parent directory exists
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: full_path.parent.mkdir(parents=True, exist_ok=True),
        )

        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: full_path.write_text(content),
            )
        except PermissionError:
            raise PermissionError(path)

    async def list_directory(self, path: str) -> list[str]:
        """List directory contents."""
        full_path = self._resolve_path(path)

        try:
            entries = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: list(full_path.iterdir()),
            )
            return [e.name for e in entries]
        except FileNotFoundError:
            raise FileNotFoundError(path)
        except NotADirectoryError:
            raise NotADirectoryError(path)

    async def grep(
        self,
        pattern: str,
        path: str,
        recursive: bool = True,
    ) -> list[GrepMatch]:
        """Search for pattern in files."""
        import re

        full_path = self._resolve_path(path)
        matches = []

        def search_file(file_path: Path) -> list[GrepMatch]:
            """Search single file."""
            file_matches = []
            try:
                content = file_path.read_text()
                for i, line in enumerate(content.splitlines(), 1):
                    if re.search(pattern, line):
                        file_matches.append(GrepMatch(
                            file=str(file_path.relative_to(self._working_dir)),
                            line=i,
                            content=line,
                        ))
            except Exception:
                pass
            return file_matches

        def search_dir(dir_path: Path) -> list[GrepMatch]:
            """Search directory recursively."""
            all_matches = []
            try:
                for entry in dir_path.iterdir():
                    if entry.is_file():
                        all_matches.extend(search_file(entry))
                    elif entry.is_dir() and recursive:
                        all_matches.extend(search_dir(entry))
            except Exception:
                pass
            return all_matches

        if full_path.is_file():
            matches = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: search_file(full_path),
            )
        elif full_path.is_dir():
            matches = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: search_dir(full_path),
            )

        return matches[:100]  # Limit results

    async def exec_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute custom tool."""
        # Write args to temp file
        args_file = self._working_dir / ".tool_args.json"
        await self.write_file(str(args_file), json.dumps(arguments))

        # Try to execute tool script
        tool_script = self._working_dir / "tools" / f"{tool_name}.py"
        if not tool_script.exists():
            return {
                "success": False,
                "error": f"Tool not found: {tool_name}",
            }

        result = await self.execute(f"python {tool_script} {args_file}")

        if result.exit_code != 0:
            return {
                "success": False,
                "error": result.stderr,
            }

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "success": True,
                "output": result.stdout,
            }

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to working directory."""
        if os.path.isabs(path):
            return Path(path)
        return self._working_dir / path


@dataclass
class LocalProcessProvider:
    """
    Local subprocess sandbox provider.

    Creates isolated directories for tool execution.
    NO real isolation - for development only.
    """

    config: LocalConfig = field(default_factory=LocalConfig)
    _sandboxes: dict[str, Path] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Provider name."""
        return "local"

    async def provision(self, config: SandboxConfig) -> SandboxHandle:
        """
        Provision a new local sandbox.

        Creates a temporary directory for the sandbox.
        """
        sandbox_id = f"local-{uuid.uuid4().hex[:12]}"

        logger.info(f"Provisioning local sandbox: {sandbox_id}")

        try:
            # Create sandbox directory
            if self.config.base_dir:
                base = Path(self.config.base_dir)
                base.mkdir(parents=True, exist_ok=True)
                sandbox_dir = base / sandbox_id
                sandbox_dir.mkdir()
            else:
                sandbox_dir = Path(tempfile.mkdtemp(prefix="sandbox-"))

            # Create workspace
            workspace = sandbox_dir / "workspace"
            workspace.mkdir()

            # Store for cleanup
            self._sandboxes[sandbox_id] = sandbox_dir

            # Create executor
            executor = LocalExecutor(workspace, self.config)

            handle = SandboxHandle(
                id=sandbox_id,
                provider=self.name,
                handle=sandbox_dir,
                status=SandboxStatus.READY,
                config=config,
            )
            handle._executor = executor

            logger.info(f"Provisioned local sandbox: {sandbox_id} at {sandbox_dir}")
            return handle

        except Exception as e:
            raise ProvisionError(str(e), self.name) from e

    async def teardown(self, handle: SandboxHandle) -> None:
        """Teardown local sandbox."""
        sandbox_dir = self._sandboxes.pop(handle.id, None)

        if sandbox_dir and self.config.cleanup_on_teardown:
            logger.info(f"Tearing down local sandbox: {handle.id}")

            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: shutil.rmtree(sandbox_dir, ignore_errors=True),
                )
            except Exception as e:
                logger.warning(f"Cleanup warning for {handle.id}: {e}")

    async def health_check(self, handle: SandboxHandle) -> bool:
        """Check if sandbox is healthy."""
        sandbox_dir = self._sandboxes.get(handle.id)
        if not sandbox_dir:
            return False

        # Check directory exists and is writable
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: sandbox_dir.exists() and os.access(sandbox_dir, os.W_OK),
            )
        except Exception:
            return False

    async def reset(self, handle: SandboxHandle) -> bool:
        """Reset sandbox to clean state."""
        if handle._executor is None:
            return False

        executor: LocalExecutor = handle._executor
        workspace = executor._working_dir

        try:
            # Clean workspace
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: [
                    shutil.rmtree(workspace, ignore_errors=True),
                    workspace.mkdir(exist_ok=True),
                ],
            )
            return True
        except Exception as e:
            logger.warning(f"Reset failed for {handle.id}: {e}")
            return False

    async def get_metrics(self, handle: SandboxHandle) -> dict[str, Any]:
        """Get sandbox metrics (disk usage)."""
        sandbox_dir = self._sandboxes.get(handle.id)
        if not sandbox_dir:
            return {}

        try:

            def get_size(path: Path) -> int:
                total = 0
                for entry in path.rglob("*"):
                    if entry.is_file():
                        total += entry.stat().st_size
                return total

            size = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: get_size(sandbox_dir),
            )

            return {
                "disk_usage_bytes": size,
            }
        except Exception:
            return {}
