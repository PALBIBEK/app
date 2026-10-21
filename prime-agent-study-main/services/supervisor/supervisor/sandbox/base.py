"""Core sandbox abstractions for isolated code execution.

This module defines the foundational contracts for sandbox isolation:
- Sandbox: An isolated environment for running processes and holding files
- SandboxProvider: Factory for creating sandboxes (one per backend)
- ProcessHandle: A running process with binary stdio

Design Principles:
    1. Binary stdio only - JSONL uses LF as delimiter, text mode breaks on U+2028/U+2029
    2. Snapshot as best-effort - Not all providers support pause/resume
    3. Workspace sync optional - Track B feature, not all providers implement it
    4. Provider-agnostic - Same interface for local, Docker, E2B, Firecracker

Thread Safety:
    Sandbox instances are NOT thread-safe. Use one sandbox per asyncio task.
    SandboxProvider instances ARE thread-safe for concurrent create() calls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, Protocol, Self, runtime_checkable

if TYPE_CHECKING:
    from supervisor.sandbox.backup import BackupArea, BackupResult
    from supervisor.sandbox.layers import LayerManifest, MaterializationResult
    from supervisor.sandbox.staging import StagedBlob, StageOutResult
    from supervisor.seams.services.workspace_store import WorkspaceSyncReport


# =============================================================================
# Errors
# =============================================================================


class SandboxError(Exception):
    """Base exception for all sandbox-related failures.

    Attributes:
        sandbox_id: ID of the affected sandbox, if known.
        provider: Name of the provider, if known.
        retryable: Whether the operation can be retried.
    """

    def __init__(
        self,
        message: str,
        *,
        sandbox_id: str | None = None,
        provider: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.sandbox_id = sandbox_id
        self.provider = provider
        self.retryable = retryable


class SandboxPathEscape(SandboxError, ValueError):
    """A path resolves outside the sandbox's root (``..``, an absolute path, or a symlink).

    A refusal, not a failure: tools report it as ``PATH_ESCAPE``.
    """

    def __init__(self, path: str, **kw: Any) -> None:
        super().__init__(f"path escapes sandbox: {path}", **kw)
        self.path = path


class SandboxGone(SandboxError):
    """The sandbox no longer exists (reaped, crashed, or destroyed).

    Recovery: Create a new sandbox or rebuild from journal.
    """

    def __init__(self, sandbox_id: str, reason: str = "unknown") -> None:
        super().__init__(
            f"sandbox {sandbox_id} is gone: {reason}",
            sandbox_id=sandbox_id,
            retryable=False,
        )
        self.reason = reason


class SandboxTimeout(SandboxError):
    """Operation timed out.

    Recovery: Retry with longer timeout or check sandbox health.
    """

    def __init__(
        self,
        operation: str,
        timeout_seconds: float,
        sandbox_id: str | None = None,
    ) -> None:
        super().__init__(
            f"{operation} timed out after {timeout_seconds}s",
            sandbox_id=sandbox_id,
            retryable=True,
        )
        self.operation = operation
        self.timeout_seconds = timeout_seconds


class SnapshotUnsupported(SandboxError):
    """Provider cannot pause/resume; caller must do a cold rebuild.

    Recovery: Use journal replay + git checkout instead of snapshot.
    """

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"{provider} does not support snapshots",
            provider=provider,
            retryable=False,
        )


class WorkspaceSyncUnsupported(SandboxError):
    """Provider cannot hydrate/sync durable workspace (Track B WP-M0+).

    Recovery: Use a provider that supports workspace sync, or skip sync.
    """

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"{provider} does not support workspace sync",
            provider=provider,
            retryable=False,
        )


class BlobStagingUnsupported(SandboxError):
    """Provider does not support blob staging (stage_in/stage_out).

    Recovery: Use a provider that supports blob staging.
    """

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"{provider} does not support blob staging",
            provider=provider,
            retryable=False,
        )


class ComputerBackupUnsupported(SandboxError):
    """Provider does not support computer backup.

    Recovery: Use a provider that supports backup.
    """

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"{provider} does not support computer backup",
            provider=provider,
            retryable=False,
        )


class ROLayersUnsupported(SandboxError):
    """Provider does not support read-only layer materialization.

    Recovery: Use a provider that supports RO layers.
    """

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"{provider} does not support read-only layers",
            provider=provider,
            retryable=False,
        )


# =============================================================================
# Process Handle Protocol
# =============================================================================


@runtime_checkable
class ByteReader(Protocol):
    """Async binary reader protocol."""

    async def read(self, n: int) -> bytes:
        """Read up to n bytes. Returns empty bytes at EOF."""
        ...


@runtime_checkable
class ByteWriter(Protocol):
    """Async binary writer protocol with drain support."""

    def write(self, data: bytes) -> None:
        """Buffer data for writing. Call drain() to flush."""
        ...

    async def drain(self) -> None:
        """Flush buffered data to the underlying transport."""
        ...


class ProcessHandle(ABC):
    """A running process inside a sandbox with binary stdio.

    Lifecycle:
        1. Created via Sandbox.spawn()
        2. Communicate via stdin/stdout/stderr
        3. Terminate via terminate() or kill()
        4. Check exit via returncode() or wait()

    Important:
        Always drain stderr on a separate task - a full stderr pipe deadlocks the child.
    """

    __slots__ = ()

    @property
    @abstractmethod
    def stdin(self) -> ByteWriter:
        """Binary stdin writer. Buffer with write(), flush with drain()."""
        ...

    @property
    @abstractmethod
    def stdout(self) -> ByteReader:
        """Binary stdout reader. Read with read(n)."""
        ...

    @property
    @abstractmethod
    def stderr(self) -> ByteReader:
        """Binary stderr reader. Must be drained concurrently to avoid deadlock."""
        ...

    @property
    @abstractmethod
    def pid(self) -> int | None:
        """Provider-local PID, or None for remote sandboxes."""
        ...

    @abstractmethod
    def returncode(self) -> int | None:
        """Exit code, or None while still running. Never blocks."""
        ...

    @abstractmethod
    async def wait(self) -> int:
        """Block until exit. Returns the exit code."""
        ...

    @abstractmethod
    async def terminate(self) -> None:
        """Polite shutdown (SIGTERM or equivalent)."""
        ...

    @abstractmethod
    async def kill(self) -> None:
        """Forced shutdown (SIGKILL or equivalent)."""
        ...



# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Result of a short-lived command execution.

    Attributes:
        exit_code: Process exit code (0 = success).
        stdout: Captured stdout bytes.
        stderr: Captured stderr bytes.
        duration_ms: Execution duration in milliseconds.
    """

    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_ms: int = 0

    @property
    def success(self) -> bool:
        """True if exit_code is 0."""
        return self.exit_code == 0

    @property
    def stdout_text(self) -> str:
        """Decode stdout as UTF-8, replacing errors."""
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        """Decode stderr as UTF-8, replacing errors."""
        return self.stderr.decode("utf-8", errors="replace")


class SandboxStatus(StrEnum):
    """Sandbox lifecycle states."""

    CREATING = auto()
    READY = auto()
    IN_USE = auto()
    PAUSED = auto()
    UNHEALTHY = auto()
    DESTROYING = auto()
    DESTROYED = auto()


@dataclass(slots=True)
class SandboxInfo:
    """Metadata about a sandbox instance.

    Attributes:
        id: Unique sandbox identifier.
        provider: Provider name (e.g., "local_process", "docker", "e2b").
        status: Current lifecycle status.
        image: Container/VM image digest, if applicable.
        workdir: Logical working directory inside sandbox.
        supports_snapshot: Whether pause/resume is available.
        supports_workspace_sync: Whether hydrate/sync is available.
        labels: Arbitrary key-value metadata.
    """

    id: str
    provider: str
    status: SandboxStatus = SandboxStatus.READY
    image: str | None = None
    workdir: str = "/workspace"
    supports_snapshot: bool = False
    supports_workspace_sync: bool = False
    labels: dict[str, str] = field(default_factory=dict)

    def with_status(self, status: SandboxStatus) -> Self:
        """Return a copy with updated status."""
        return type(self)(
            id=self.id,
            provider=self.provider,
            status=status,
            image=self.image,
            workdir=self.workdir,
            supports_snapshot=self.supports_snapshot,
            supports_workspace_sync=self.supports_workspace_sync,
            labels=dict(self.labels),
        )


# =============================================================================
# Sandbox ABC
# =============================================================================


class Sandbox(ABC):
    """An isolated environment that can run processes and hold files.

    Responsibilities:
        - Process execution (spawn for long-lived, exec for short commands)
        - File I/O (read, write, exists)
        - Lifecycle management (pause, destroy, health check)
        - Workspace sync (optional, Track B)

    Usage:
        sandbox = await provider.create(image="python:3.11")
        try:
            result = await sandbox.exec(["python", "-c", "print('hello')"])
            assert result.success
        finally:
            await sandbox.destroy()
    """

    __slots__ = ()

    @property
    @abstractmethod
    def info(self) -> SandboxInfo:
        """Get sandbox metadata."""
        ...

    @property
    def id(self) -> str:
        """Shortcut for info.id."""
        return self.info.id

    # -------------------------------------------------------------------------
    # Process Execution
    # -------------------------------------------------------------------------

    @abstractmethod
    async def spawn(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessHandle:
        """Start a long-lived process with binary stdio.

        Args:
            cmd: Command and arguments (e.g., ["python", "script.py"]).
            cwd: Working directory inside sandbox. Defaults to workdir.
            env: Additional environment variables. Sanitized by provider.

        Returns:
            ProcessHandle for communicating with the process.

        Note:
            Caller owns the handle and must drain stderr on a separate task.
        """
        ...

    @abstractmethod
    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a command to completion and collect output.

        Args:
            cmd: Command and arguments.
            cwd: Working directory inside sandbox.
            env: Additional environment variables.
            timeout: Maximum execution time in seconds.

        Returns:
            ExecResult with exit code, stdout, and stderr.

        Raises:
            SandboxTimeout: If execution exceeds timeout.

        Note:
            For short commands only. Use spawn() for long-lived processes.
        """
        ...

    # -------------------------------------------------------------------------
    # File Operations
    # -------------------------------------------------------------------------

    @abstractmethod
    async def read_file(self, path: str) -> bytes:
        """Read file contents.

        Args:
            path: Absolute or workspace-relative path.

        Returns:
            File contents as bytes.

        Raises:
            FileNotFoundError: If file doesn't exist.
        """
        ...

    @abstractmethod
    async def write_file(self, path: str, data: bytes) -> None:
        """Write file contents.

        Args:
            path: Absolute or workspace-relative path.
            data: Contents to write.

        Note:
            Creates parent directories if needed.
        """
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if path exists.

        Args:
            path: Absolute or workspace-relative path.

        Returns:
            True if path exists (file or directory).
        """
        ...

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    @abstractmethod
    async def pause(self) -> str:
        """Freeze the sandbox and return a snapshot ID.

        Returns:
            Snapshot ID for resume().

        Raises:
            SnapshotUnsupported: If provider cannot snapshot.

        Note:
            Callers must treat pause as best-effort and fall back to
            git + journal replay for durability.
        """
        ...

    @abstractmethod
    async def destroy(self) -> None:
        """Tear down the sandbox and release resources.

        Note:
            Idempotent - destroying a dead sandbox is not an error.
        """
        ...

    @abstractmethod
    async def is_alive(self) -> bool:
        """Check if sandbox is still operational.

        Returns:
            True if sandbox can execute commands.
        """
        ...

    async def logs(self, *, limit_bytes: int = 256_000) -> bytes | None:
        """What the provider recorded about this sandbox (its state, its output), last bytes first kept.

        Read when the sandbox is lost, before it is destroyed, so that finding
        out why needs no shell in a container holding user data. ``None``: the
        provider keeps nothing. Never raises for a dead sandbox.
        """
        return None

    # -------------------------------------------------------------------------
    # Workspace Sync (Track B - Optional)
    # -------------------------------------------------------------------------

    async def hydrate_workspace(self, scope_id: str) -> WorkspaceSyncReport:
        """Fill /workspace from durable source of truth.

        Args:
            scope_id: Workspace scope identifier.

        Returns:
            Report of files synced.

        Raises:
            WorkspaceSyncUnsupported: If provider doesn't support this.
        """
        raise WorkspaceSyncUnsupported(self.info.provider)

    async def sync_workspace(self, scope_id: str) -> WorkspaceSyncReport:
        """Flush /workspace mutations to durable source of truth.

        Args:
            scope_id: Workspace scope identifier.

        Returns:
            Report of files synced.

        Raises:
            WorkspaceSyncUnsupported: If provider doesn't support this.
        """
        raise WorkspaceSyncUnsupported(self.info.provider)

    # -------------------------------------------------------------------------
    # Blob Staging (Optional)
    # -------------------------------------------------------------------------

    async def stage_in(
        self,
        blob_id: str,
        dest_path: str,
        *,
        timeout: float | None = None,
    ) -> StagedBlob:
        """Download a blob into the sandbox.

        Uses atomic temp file swap to prevent partial downloads.

        Args:
            blob_id: ID of the blob to download.
            dest_path: Absolute destination path in sandbox.
            timeout: Operation timeout in seconds.

        Returns:
            StagedBlob with details of staged file.

        Raises:
            BlobStagingUnsupported: If provider doesn't support this.
        """
        raise BlobStagingUnsupported(self.info.provider)

    async def stage_out(
        self,
        source_path: str,
        *,
        timeout: float | None = None,
    ) -> StageOutResult:
        """Upload a file from the sandbox to blob store.

        Computes SHA256 before upload and verifies after.

        Args:
            source_path: Absolute source path in sandbox.
            timeout: Operation timeout in seconds.

        Returns:
            StageOutResult with blob_id and verification hash.

        Raises:
            BlobStagingUnsupported: If provider doesn't support this.
        """
        raise BlobStagingUnsupported(self.info.provider)

    # -------------------------------------------------------------------------
    # Computer Backup (Optional)
    # -------------------------------------------------------------------------

    async def backup_computer(
        self,
        area: BackupArea,
        *,
        exclude_credentials: bool = True,
        exclude_caches: bool = True,
        timeout: float | None = None,
    ) -> BackupResult:
        """Create a backup of the specified area.

        Uses tar with find -print0 for safe filename handling.

        Args:
            area: Area to backup (WORKSPACE or HOME).
            exclude_credentials: Exclude credential files.
            exclude_caches: Exclude cache directories.
            timeout: Operation timeout in seconds.

        Returns:
            BackupResult with archive path and fingerprint.

        Raises:
            ComputerBackupUnsupported: If provider doesn't support this.
        """
        raise ComputerBackupUnsupported(self.info.provider)

    # -------------------------------------------------------------------------
    # Read-Only Layers (Optional)
    # -------------------------------------------------------------------------

    async def materialize_ro_layers(
        self,
        manifest: LayerManifest,
        *,
        force: bool = False,
        timeout: float | None = None,
    ) -> MaterializationResult:
        """Materialize read-only layers from manifest.

        Short-circuits if manifest SHA matches current state.

        Args:
            manifest: Layer manifest with ordered entries.
            force: Force rematerialization even if fingerprint matches.
            timeout: Operation timeout in seconds.

        Returns:
            MaterializationResult with timing details.

        Raises:
            ROLayersUnsupported: If provider doesn't support this.
        """
        raise ROLayersUnsupported(self.info.provider)


    # -------------------------------------------------------------------------
    # Computer Status (Optional)
    # -------------------------------------------------------------------------



    # -------------------------------------------------------------------------
    # Egress Control (Optional)
    # -------------------------------------------------------------------------



# =============================================================================
# Sandbox Provider ABC
# =============================================================================


class SandboxProvider(ABC):
    """Factory for creating sandboxes. One implementation per backend.

    Responsibilities:
        - Create new sandboxes with specified configuration
        - Resume sandboxes from snapshots
        - Reattach to existing sandboxes (for crash recovery)
        - Clean up provider-level resources

    Thread Safety:
        Implementations must be thread-safe for concurrent create() calls.

    Implementations:
        - NoneProvider: No isolation (requires_sandbox=false)
        - LocalProcessProvider: Local process with temp directory
        - LocalDockerProvider: Docker containers
        - E2BProvider: E2B cloud sandboxes
    """

    __slots__ = ()

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging and metrics."""
        ...

    async def preflight(self, *, image: str | None = None) -> None:
        """Raise if this provider could not build a sandbox from ``image`` now.

        Cheap (no sandbox is built): sessions build theirs lazily, so creation calls
        this to refuse a configuration that could never get one. Default: nothing
        to check.
        """

    @abstractmethod
    async def create(
        self,
        *,
        image: str | None = None,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> Sandbox:
        """Provision a fresh sandbox.

        Args:
            image: Container/VM image digest. Pin by digest, not tag.
            workdir: Initial working directory.
            env: Environment variables (will be sanitized).
            labels: Metadata labels for tracking.

        Returns:
            Ready-to-use Sandbox instance.

        Raises:
            ProvisionError: If provisioning fails.
        """
        ...

    @abstractmethod
    async def resume(self, snapshot_id: str) -> Sandbox:
        """Restore a paused sandbox from snapshot.

        Args:
            snapshot_id: ID returned by Sandbox.pause().

        Returns:
            Restored Sandbox instance.

        Raises:
            SandboxGone: If snapshot no longer exists.
            SnapshotUnsupported: If provider doesn't support snapshots.
        """
        ...

    @abstractmethod
    async def get(self, sandbox_id: str) -> Sandbox | None:
        """Reattach to an existing sandbox.

        Used for crash recovery when the supervisor restarts but
        sandboxes are still running (e.g., Docker containers).

        Args:
            sandbox_id: ID of the sandbox to reattach.

        Returns:
            Sandbox if it still exists, None otherwise.
        """
        ...

    async def adopt(self, session_id: str) -> Sandbox | None:
        """A live sandbox this provider made for ``session_id``, if one survived its worker.

        A session moves to another worker when its worker dies; its sandbox may
        still be there (a directory on the same host, a running container), and
        with it everything the agent wrote. Default: none can be found.
        """
        return None

    async def close(self) -> None:
        """Release provider-level resources.

        Called during graceful shutdown. Default: no-op.
        """

    async def health_check(self) -> bool:
        """Check if provider is healthy (can create sandboxes).

        Returns:
            True if provider is operational.
        """
        return True
