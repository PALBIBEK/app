"""Local process sandbox - development provider without container isolation.

This provider creates sandboxes as local directories with process execution.
Useful for development and testing. NOT suitable for production multi-tenant
deployments - use Docker or E2B for actual isolation.

Features:
    - Fast sandbox creation (just mkdir)
    - Direct process execution via asyncio
    - Optional workspace sync via WorkspaceHydrator (Track B)
    - Automatic cleanup on destroy

Security:
    - Environment is sanitized via env.py
    - No network isolation
    - No resource limits
    - Processes run as current user

Usage:
    provider = LocalProcessProvider(base_dir=Path("/tmp/sandboxes"))
    sandbox = await provider.create()

    result = await sandbox.exec(["python", "-c", "print('hello')"])
    print(result.stdout_text)  # "hello\\n"

    await sandbox.destroy()
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from supervisor.sandbox.base import (
    ByteReader,
    ByteWriter,
    ExecResult,
    ProcessHandle,
    Sandbox,
    SandboxGone,
    SandboxPathEscape,
    SandboxInfo,
    SandboxProvider,
    SandboxStatus,
    SandboxTimeout,
    SnapshotUnsupported,
    WorkspaceSyncUnsupported,
)
from supervisor.sandbox.status import (
    ComputerStatus,
    MachineState,
)
from supervisor.sandbox.security.env import sanitize_sandbox_env

if TYPE_CHECKING:
    from supervisor.seams.services import WorkspaceTreeIO
    from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator
    from supervisor.seams.services.workspace_store import WorkspaceSyncReport


class AsyncProcessHandle(ProcessHandle):
    """ProcessHandle implementation wrapping asyncio.subprocess.Process."""

    __slots__ = ("_proc",)

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc

    @property
    def stdin(self) -> ByteWriter:
        assert self._proc.stdin is not None, "stdin not opened"
        return self._proc.stdin  # type: ignore[return-value]

    @property
    def stdout(self) -> ByteReader:
        assert self._proc.stdout is not None, "stdout not opened"
        return self._proc.stdout  # type: ignore[return-value]

    @property
    def stderr(self) -> ByteReader:
        assert self._proc.stderr is not None, "stderr not opened"
        return self._proc.stderr  # type: ignore[return-value]

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    def returncode(self) -> int | None:
        return self._proc.returncode

    async def wait(self) -> int:
        return await self._proc.wait()

    async def terminate(self) -> None:
        self._proc.terminate()

    async def kill(self) -> None:
        self._proc.kill()


class LocalProcessSandbox(Sandbox):
    """Sandbox backed by a local directory with asyncio process execution.

    Each sandbox gets its own directory under the provider's base_dir.
    Processes run directly via asyncio.create_subprocess_exec.

    Workspace sync is supported via an optional WorkspaceHydrator.
    """

    __slots__ = ("_root", "_hydrator", "_info", "_destroyed")

    def __init__(
        self,
        root: Path,
        sandbox_id: str,
        *,
        hydrator: WorkspaceHydrator | None = None,
    ) -> None:
        self._root = root
        self._hydrator = hydrator
        self._destroyed = False
        self._info = SandboxInfo(
            id=sandbox_id,
            provider="local_process",
            status=SandboxStatus.READY,
            image=None,
            workdir="/workspace",
            supports_snapshot=False,
            supports_workspace_sync=hydrator is not None,
            labels={"host_root": str(root)},
        )

    @property
    def info(self) -> SandboxInfo:
        return self._info

    @property
    def root(self) -> Path:
        """Host filesystem path for this sandbox."""
        return self._root

    # -------------------------------------------------------------------------
    # Process Execution
    # -------------------------------------------------------------------------

    async def spawn(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessHandle:
        self._check_alive()

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd or str(self._root),
            env=sanitize_sandbox_env(env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return AsyncProcessHandle(proc)

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        self._check_alive()

        start_ms = int(time.monotonic() * 1000)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd or str(self._root),
            env=sanitize_sandbox_env(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise SandboxTimeout(
                operation=f"exec {cmd[0] if cmd else 'command'}",
                timeout_seconds=timeout or 0,
                sandbox_id=self.id,
            )

        duration_ms = int(time.monotonic() * 1000) - start_ms

        return ExecResult(
            exit_code=proc.returncode or 0,
            stdout=stdout or b"",
            stderr=stderr or b"",
            duration_ms=duration_ms,
        )

    # -------------------------------------------------------------------------
    # File Operations
    # -------------------------------------------------------------------------

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to sandbox root, stripping /workspace prefix."""
        clean = path.strip().replace("\\", "/")

        # Strip /workspace prefix if present
        if clean == "/workspace":
            clean = ""
        elif clean.startswith("/workspace/"):
            clean = clean[len("/workspace/"):]

        # Resolve and validate
        resolved = (self._root / clean).resolve()

        # Security: ensure path is within sandbox
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise SandboxPathEscape(path, provider="local_process") from None

        return resolved

    async def read_file(self, path: str) -> bytes:
        self._check_alive()
        resolved = self._resolve_path(path)

        def _read() -> bytes:
            return resolved.read_bytes()

        return await asyncio.to_thread(_read)

    async def write_file(self, path: str, data: bytes) -> None:
        self._check_alive()
        resolved = self._resolve_path(path)

        def _write() -> None:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(data)

        await asyncio.to_thread(_write)

    async def exists(self, path: str) -> bool:
        self._check_alive()
        try:
            resolved = self._resolve_path(path)
            return await asyncio.to_thread(resolved.exists)
        except ValueError:
            return False

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def pause(self) -> str:
        raise SnapshotUnsupported("local_process")

    async def destroy(self) -> None:
        if self._destroyed:
            return

        self._destroyed = True
        self._info = self._info.with_status(SandboxStatus.DESTROYED)

        if self._root.exists():
            await asyncio.to_thread(shutil.rmtree, self._root, ignore_errors=True)

    async def is_alive(self) -> bool:
        return not self._destroyed and self._root.exists()

    def _check_alive(self) -> None:
        """Raise if sandbox is destroyed."""
        if self._destroyed:
            raise SandboxGone(self.id, reason="sandbox was destroyed")

    # -------------------------------------------------------------------------
    # Workspace Sync (Track B)
    # -------------------------------------------------------------------------

    def as_tree_io(self) -> WorkspaceTreeIO:
        """The sandbox directory as the scope workspace's file tree (hydrate / sync target)."""
        from supervisor.sandbox.workspace_tree import LocalFsTreeIO

        return LocalFsTreeIO(self._root)

    async def hydrate_workspace(self, scope_id: str) -> WorkspaceSyncReport:
        if self._hydrator is None:
            raise WorkspaceSyncUnsupported("local_process")
        return await self._hydrator.hydrate(scope_id, self.as_tree_io())

    async def sync_workspace(self, scope_id: str) -> WorkspaceSyncReport:
        if self._hydrator is None:
            raise WorkspaceSyncUnsupported("local_process")
        return await self._hydrator.sync(scope_id, self.as_tree_io())

    # -------------------------------------------------------------------------
    # Computer Status (basic)
    # -------------------------------------------------------------------------

    async def computer_status(self) -> ComputerStatus:
        """Get status of the sandbox.

        For local_process, this just checks if the directory exists.
        """
        alive = await self.is_alive()

        return ComputerStatus(
            scope_id=self.id,
            machine_state=MachineState.RUNNING if alive else MachineState.STOPPED,
            guest_responsive=alive,
        )


class LocalProcessProvider(SandboxProvider):
    """Provider for local process sandboxes.

    Creates sandboxes as directories under base_dir. Each sandbox gets
    a UUID-named subdirectory that is cleaned up on destroy.

    Thread-safe for concurrent create() calls.
    """

    __slots__ = ("_base", "_hydrator", "_sandboxes")

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        hydrator: WorkspaceHydrator | None = None,
    ) -> None:
        self._base = base_dir or Path(tempfile.gettempdir()) / "yal-local-sandboxes"
        self._base.mkdir(parents=True, exist_ok=True)
        self._hydrator = hydrator
        self._sandboxes: dict[str, LocalProcessSandbox] = {}

    @property
    def name(self) -> str:
        return "local_process"

    async def create(
        self,
        *,
        image: str | None = None,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> Sandbox:
        # Image/env/workdir are ignored for local_process
        sandbox_id = uuid4().hex
        root = self._base / sandbox_id
        root.mkdir(parents=True, exist_ok=True)

        sandbox = LocalProcessSandbox(
            root,
            sandbox_id,
            hydrator=self._hydrator,
        )
        self._sandboxes[sandbox_id] = sandbox
        return sandbox

    async def resume(self, snapshot_id: str) -> Sandbox:
        raise SandboxGone(
            sandbox_id=snapshot_id,
            reason="local_process does not support snapshots",
        )

    async def get(self, sandbox_id: str) -> Sandbox | None:
        return self._sandboxes.get(sandbox_id)

    async def close(self) -> None:
        # Clean up all sandboxes
        for sandbox in list(self._sandboxes.values()):
            await sandbox.destroy()
        self._sandboxes.clear()

    async def health_check(self) -> bool:
        # Check if base directory is writable
        try:
            test_path = self._base / ".health_check"
            test_path.touch()
            test_path.unlink()
            return True
        except OSError:
            return False
