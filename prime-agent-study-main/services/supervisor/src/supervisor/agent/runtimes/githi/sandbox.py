"""
Sandbox interface for isolated tool execution.

Provides filesystem and process isolation for agent tools.
Start simple (subprocess + temp dir), upgrade later (Docker, Firecracker).

Pattern: Following QM's Sandbox interface
Classification: execution isolation
"""

from __future__ import annotations

import typing as t
import os
import tempfile
import shutil
from dataclasses import dataclass
from pathlib import Path
import asyncio
from abc import ABC, abstractmethod


@dataclass(frozen=True)
class SandboxHandle:
    """
    Handle to provisioned sandbox.

    Attributes:
        root_dir: Sandbox root directory
        env: Environment variables for sandbox
        metadata: Optional sandbox-specific metadata
    """
    root_dir: str
    env: dict[str, str]
    metadata: dict[str, t.Any] | None = None


@dataclass(frozen=True)
class SandboxLayer:
    """
    Filesystem layer for sandbox mounting.

    Pattern: QM's layered filesystem (org base + user overlay)

    Attributes:
        scope_id: Which scope owns this layer
        mount_path: Where to mount ("" = root, "global" = /global)
        mode: Read-only or read-write
    """
    scope_id: str
    mount_path: str
    mode: t.Literal["ro", "rw"]


@dataclass(frozen=True)
class ProvisionOptions:
    """Options for sandbox provisioning"""
    env: dict[str, str] | None = None
    egress: str | None = None  # Network policy (future)
    egress_token: str | None = None  # Egress auth token (future)
    scratch: dict[str, str] | None = None  # Scratch space config (future)


@dataclass(frozen=True)
class ExecOptions:
    """Options for command execution"""
    timeout: int | None = None  # Timeout in seconds
    env: dict[str, str] | None = None  # Additional environment variables
    cwd: str | None = None  # Working directory (relative to root_dir)


@dataclass(frozen=True)
class ExecResult:
    """Result of command execution"""
    stdout: str
    stderr: str
    returncode: int

    @property
    def success(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class TeardownOptions:
    """Options for sandbox teardown"""
    keep_warm: bool = False  # Keep sandbox warm for reuse
    destroy: bool = False  # Forcefully destroy sandbox


class SandboxError(Exception):
    """Base exception for sandbox errors"""
    pass


class Sandbox(ABC):
    """
    Abstract sandbox interface.

    Pattern: Protocol-based abstraction (swap implementations)
    - SubprocessSandbox: Simple temp dir + subprocess (Phase 1)
    - DockerSandbox: Docker containers (Phase 2)
    - FirecrackerSandbox: VM-level isolation (Phase 3)
    """

    @abstractmethod
    async def provision(
        self,
        layers: list[SandboxLayer],
        options: ProvisionOptions
    ) -> SandboxHandle:
        """
        Provision new sandbox with layered filesystem.

        Args:
            layers: Filesystem layers to mount
            options: Provisioning options

        Returns:
            Handle to provisioned sandbox

        Raises:
            SandboxError: If provisioning fails
        """
        pass

    @abstractmethod
    async def exec(
        self,
        handle: SandboxHandle,
        command: str,
        options: ExecOptions | None = None
    ) -> ExecResult:
        """
        Execute command in sandbox.

        Args:
            handle: Sandbox handle
            command: Command to execute
            options: Execution options

        Returns:
            Execution result

        Raises:
            SandboxError: If execution fails
        """
        pass

    @abstractmethod
    async def read_file(
        self,
        handle: SandboxHandle,
        path: str
    ) -> str:
        """
        Read file from sandbox.

        Args:
            handle: Sandbox handle
            path: File path (relative to root_dir)

        Returns:
            File contents

        Raises:
            SandboxError: If read fails or file not found
        """
        pass

    @abstractmethod
    async def write_file(
        self,
        handle: SandboxHandle,
        path: str,
        content: str
    ) -> None:
        """
        Write file to sandbox.

        Args:
            handle: Sandbox handle
            path: File path (relative to root_dir)
            content: File contents

        Raises:
            SandboxError: If write fails
        """
        pass

    @abstractmethod
    async def list_dir(
        self,
        handle: SandboxHandle,
        path: str
    ) -> list[str]:
        """
        List directory contents.

        Args:
            handle: Sandbox handle
            path: Directory path (relative to root_dir)

        Returns:
            List of paths relative to root_dir

        Raises:
            SandboxError: If listing fails or directory not found
        """
        pass

    @abstractmethod
    async def remove_dir(
        self,
        handle: SandboxHandle,
        path: str
    ) -> None:
        """
        Remove directory and contents.

        Args:
            handle: Sandbox handle
            path: Directory path (relative to root_dir)

        Raises:
            SandboxError: If removal fails
        """
        pass

    @abstractmethod
    async def teardown(
        self,
        handle: SandboxHandle,
        options: TeardownOptions | None = None
    ) -> None:
        """
        Teardown sandbox.

        Args:
            handle: Sandbox handle
            options: Teardown options

        Raises:
            SandboxError: If teardown fails
        """
        pass


class SubprocessSandbox(Sandbox):
    """
    Simple subprocess-based sandbox (Phase 1).

    Pattern: Temp directory + subprocess execution
    - Creates isolated temp directory per sandbox
    - Runs commands in subprocess with working directory
    - No network isolation (future: Docker)
    - No process isolation (future: Docker/Firecracker)

    Good for:
    - Development and testing
    - Phase 1 implementation
    - Basic filesystem isolation

    Upgrade path:
    - Phase 2: DockerSandbox (container isolation)
    - Phase 3: FirecrackerSandbox (VM isolation)
    """

    def __init__(self, *, base_dir: str | None = None):
        """
        Initialize subprocess sandbox.

        Args:
            base_dir: Optional base directory for sandboxes
                     (default: system temp dir)
        """
        self.base_dir = base_dir
        self._active_sandboxes: set[str] = set()

    async def provision(
        self,
        layers: list[SandboxLayer],
        options: ProvisionOptions
    ) -> SandboxHandle:
        """
        Provision sandbox by creating temp directory.

        Current implementation: Single temp directory
        Future: Layer mounting (symlinks, overlayfs, etc.)

        Args:
            layers: Filesystem layers (future: mount these)
            options: Provisioning options

        Returns:
            Sandbox handle

        Example:
            handle = await sandbox.provision(
                layers=[{"scope_id": "user:alice", "mount_path": "", "mode": "rw"}],
                options=ProvisionOptions(env={"PATH": "/usr/bin"})
            )
        """
        # Create temp directory
        root_dir = tempfile.mkdtemp(
            prefix="githi-sandbox-",
            dir=self.base_dir
        )

        # Build environment
        env = {
            "HOME": root_dir,
            "PWD": root_dir,
            **(options.env or {})
        }

        # Track active sandbox
        self._active_sandboxes.add(root_dir)

        # TODO: Implement layer mounting
        # For now: Just create single directory
        # Future: Mount org layer (RO) + user layer (RW)

        return SandboxHandle(
            root_dir=root_dir,
            env=env,
            metadata={"layers": layers}
        )

    async def exec(
        self,
        handle: SandboxHandle,
        command: str,
        options: ExecOptions | None = None
    ) -> ExecResult:
        """
        Execute command in sandbox via subprocess.

        Args:
            handle: Sandbox handle
            command: Shell command to execute
            options: Execution options

        Returns:
            Execution result

        Example:
            result = await sandbox.exec(
                handle,
                "echo 'Hello World' > test.txt",
                options=ExecOptions(timeout=30)
            )
        """
        options = options or ExecOptions()

        # Determine working directory
        cwd = handle.root_dir
        if options.cwd:
            cwd = os.path.join(handle.root_dir, options.cwd)
            cwd = os.path.normpath(cwd)

        # Build environment
        env = {
            **handle.env,
            **(options.env or {})
        }

        # Execute command
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Wait with optional timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=options.timeout
                )
            except asyncio.TimeoutError:
                # Kill process on timeout
                process.kill()
                await process.wait()
                raise SandboxError(f"Command timed out after {options.timeout}s")

            return ExecResult(
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                returncode=process.returncode or 0
            )

        except Exception as e:
            if isinstance(e, SandboxError):
                raise
            raise SandboxError(f"Command execution failed: {e}") from e

    async def read_file(
        self,
        handle: SandboxHandle,
        path: str
    ) -> str:
        """
        Read file from sandbox.

        Args:
            handle: Sandbox handle
            path: File path (relative to root_dir)

        Returns:
            File contents

        Example:
            content = await sandbox.read_file(handle, "output.txt")
        """
        # Resolve path
        full_path = os.path.join(handle.root_dir, path)
        full_path = os.path.normpath(full_path)

        # Security check: Ensure path is within sandbox
        if not full_path.startswith(handle.root_dir):
            raise SandboxError(f"Path {path} is outside sandbox")

        # Read file
        try:
            async with asyncio.Lock():  # Simple file locking
                with open(full_path, "r", encoding="utf-8") as f:
                    return f.read()
        except FileNotFoundError:
            raise SandboxError(f"File not found: {path}")
        except Exception as e:
            raise SandboxError(f"Failed to read file {path}: {e}") from e

    async def write_file(
        self,
        handle: SandboxHandle,
        path: str,
        content: str
    ) -> None:
        """
        Write file to sandbox.

        Args:
            handle: Sandbox handle
            path: File path (relative to root_dir)
            content: File contents

        Example:
            await sandbox.write_file(handle, "config.json", json.dumps(config))
        """
        # Resolve path
        full_path = os.path.join(handle.root_dir, path)
        full_path = os.path.normpath(full_path)

        # Security check: Ensure path is within sandbox
        if not full_path.startswith(handle.root_dir):
            raise SandboxError(f"Path {path} is outside sandbox")

        # Create parent directories
        parent_dir = os.path.dirname(full_path)
        if parent_dir and not os.path.exists(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except Exception as e:
                raise SandboxError(f"Failed to create directory {parent_dir}: {e}") from e

        # Write file
        try:
            async with asyncio.Lock():  # Simple file locking
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception as e:
            raise SandboxError(f"Failed to write file {path}: {e}") from e

    async def list_dir(
        self,
        handle: SandboxHandle,
        path: str
    ) -> list[str]:
        """
        List directory contents.

        Args:
            handle: Sandbox handle
            path: Directory path (relative to root_dir)

        Returns:
            List of paths relative to the specified directory

        Example:
            files = await sandbox.list_dir(handle, "workspace")
        """
        # Resolve path
        full_path = os.path.join(handle.root_dir, path)
        full_path = os.path.normpath(full_path)

        # Security check: Ensure path is within sandbox
        if not full_path.startswith(handle.root_dir):
            raise SandboxError(f"Path {path} is outside sandbox")

        # List directory
        try:
            entries = os.listdir(full_path)
            # Return paths relative to the directory
            return [os.path.join(path, entry) for entry in entries]
        except FileNotFoundError:
            raise SandboxError(f"Directory not found: {path}")
        except Exception as e:
            raise SandboxError(f"Failed to list directory {path}: {e}") from e

    async def remove_dir(
        self,
        handle: SandboxHandle,
        path: str
    ) -> None:
        """
        Remove directory and all contents.

        Args:
            handle: Sandbox handle
            path: Directory path (relative to root_dir)

        Example:
            await sandbox.remove_dir(handle, "temp")
        """
        # Resolve path
        full_path = os.path.join(handle.root_dir, path)
        full_path = os.path.normpath(full_path)

        # Security check: Ensure path is within sandbox
        if not full_path.startswith(handle.root_dir):
            raise SandboxError(f"Path {path} is outside sandbox")

        # Don't allow removing root directory
        if full_path == handle.root_dir:
            raise SandboxError("Cannot remove sandbox root directory")

        # Remove directory
        try:
            if os.path.exists(full_path):
                shutil.rmtree(full_path)
        except Exception as e:
            raise SandboxError(f"Failed to remove directory {path}: {e}") from e

    async def teardown(
        self,
        handle: SandboxHandle,
        options: TeardownOptions | None = None
    ) -> None:
        """
        Teardown sandbox by removing temp directory.

        Args:
            handle: Sandbox handle
            options: Teardown options

        Example:
            await sandbox.teardown(handle)
        """
        options = options or TeardownOptions()

        # Remove from active sandboxes
        self._active_sandboxes.discard(handle.root_dir)

        # If keep_warm, don't delete directory
        if options.keep_warm:
            return

        # Remove sandbox directory
        try:
            if os.path.exists(handle.root_dir):
                shutil.rmtree(handle.root_dir, ignore_errors=not options.destroy)
        except Exception as e:
            if options.destroy:
                raise SandboxError(f"Failed to destroy sandbox: {e}") from e
            # Swallow errors if not destroying
