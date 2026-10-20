"""Null sandbox provider - for harnesses that don't need isolation.

This implements the Null Object Pattern for sandboxes. When a harness
declares `requires_sandbox: false`, it gets a NoneSandbox that:
- Satisfies the Sandbox interface
- Raises clear errors if process/file APIs are misused
- Always reports as "alive" (no real resources to check)

This allows session/spawn code to remain uniform without special-casing
the "no sandbox" path.

Usage:
    provider = NoneProvider()
    sandbox = await provider.create()

    # These will raise SandboxError
    await sandbox.exec(["ls"])  # NoProcessSupport
    await sandbox.read_file("x")  # NoFilesystemSupport
"""

from __future__ import annotations

from supervisor.sandbox.base import (
    ExecResult,
    ProcessHandle,
    Sandbox,
    SandboxError,
    SandboxGone,
    SandboxInfo,
    SandboxProvider,
    SandboxStatus,
    SnapshotUnsupported,
)


class NoProcessSupport(SandboxError):
    """None sandbox cannot run processes."""

    def __init__(self) -> None:
        super().__init__(
            "none sandbox cannot spawn or exec processes",
            provider="none",
            retryable=False,
        )


class NoFilesystemSupport(SandboxError):
    """None sandbox has no filesystem."""

    def __init__(self) -> None:
        super().__init__(
            "none sandbox has no filesystem",
            provider="none",
            retryable=False,
        )


# Singleton ID for the null sandbox
_NONE_SANDBOX_ID = "none"


class NoneSandbox(Sandbox):
    """A no-op sandbox that satisfies the interface but does nothing.

    All process and file operations raise descriptive errors.
    Lifecycle operations are no-ops that always succeed.

    This is used for harnesses that don't need code execution,
    such as knowledge-graph-only agents.
    """

    __slots__ = ("_info",)

    def __init__(self, sandbox_id: str = _NONE_SANDBOX_ID) -> None:
        self._info = SandboxInfo(
            id=sandbox_id,
            provider="none",
            status=SandboxStatus.READY,
            image=None,
            workdir="",
            supports_snapshot=False,
            supports_workspace_sync=False,
            labels={"kind": "none"},
        )

    @property
    def info(self) -> SandboxInfo:
        return self._info

    # -------------------------------------------------------------------------
    # Process Execution (all raise)
    # -------------------------------------------------------------------------

    async def spawn(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessHandle:
        raise NoProcessSupport()

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        raise NoProcessSupport()

    # -------------------------------------------------------------------------
    # File Operations (all raise except exists)
    # -------------------------------------------------------------------------

    async def read_file(self, path: str) -> bytes:
        raise NoFilesystemSupport()

    async def write_file(self, path: str, data: bytes) -> None:
        raise NoFilesystemSupport()

    async def exists(self, path: str) -> bool:
        # Rather than raise, return False - nothing exists in a null sandbox
        return False

    # -------------------------------------------------------------------------
    # Lifecycle (no-ops)
    # -------------------------------------------------------------------------

    async def pause(self) -> str:
        raise SnapshotUnsupported("none")

    async def destroy(self) -> None:
        # No-op - nothing to destroy
        pass

    async def is_alive(self) -> bool:
        # Always alive - no real resources to fail
        return True


class NoneProvider(SandboxProvider):
    """Provider that creates null sandboxes.

    Always returns the same logical sandbox. No resources are provisioned.
    This is safe for use in production when harness doesn't need isolation.
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        return "none"

    async def create(
        self,
        *,
        image: str | None = None,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> Sandbox:
        # Ignore all parameters - none sandbox has no configuration
        return NoneSandbox()

    async def resume(self, snapshot_id: str) -> Sandbox:
        raise SandboxGone(
            sandbox_id=snapshot_id,
            reason="none provider does not support snapshots",
        )

    async def get(self, sandbox_id: str) -> Sandbox | None:
        # The only valid ID is "none"
        if sandbox_id == _NONE_SANDBOX_ID:
            return NoneSandbox()
        return None

    async def health_check(self) -> bool:
        # Always healthy - no dependencies
        return True
