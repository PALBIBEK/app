"""The ``runner`` provider: a hand on a tenant's own machine, reached through its runner.

Every operation is a command the runner carries out (``supervisor.hands.runners``);
nothing connects to the tenant's network. A hand's sandbox id on the runner is
derived from its hand key, so after a worker or runner restart the session finds
its files where it left them (``adopt``); a sandbox whose runner stops polling is
gone, which makes the next tool call a workspace reset like any other.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from supervisor.hands.runners import RunnerHub, RunnerOffline, RunnerRefused, current_hub
from supervisor.sandbox.base import (
    ExecResult,
    ProcessHandle,
    Sandbox,
    SandboxGone,
    SandboxInfo,
    SandboxProvider,
    SnapshotUnsupported,
)

__all__ = ["LABEL_RUNNER", "LABEL_TENANT", "RunnerProvider", "RunnerSandbox"]

LABEL_RUNNER = "supervisor.runner"
LABEL_TENANT = "supervisor.tenant_id"
LABEL_SESSION = "supervisor.session_id"


def _sandbox_id(key: str) -> str:
    return "hand-" + hashlib.sha256(key.encode()).hexdigest()[:24]


class RunnerSandbox(Sandbox):
    """One hand's directory on a runner."""

    def __init__(self, hub: RunnerHub, tenant_id: str, runner: str, sandbox_id: str) -> None:
        self._hub = hub
        self._tenant = tenant_id
        self._runner = runner
        self._gone = False
        self._info = SandboxInfo(
            id=sandbox_id, provider="runner", workdir="/workspace", labels={LABEL_RUNNER: runner}
        )

    @property
    def info(self) -> SandboxInfo:
        return self._info

    async def _call(self, op: str, args: dict[str, Any] | None = None, *, timeout_s: float = 300.0) -> dict[str, Any]:
        if self._gone:
            raise SandboxGone(self.id, "destroyed")
        try:
            return await self._hub.call(
                self._tenant, self._runner, op, args, sandbox_id=self.id, timeout_s=timeout_s
            )
        except RunnerOffline as exc:
            raise SandboxGone(self.id, str(exc)) from exc
        except RunnerRefused as exc:
            if exc.kind == "FileNotFoundError":
                raise FileNotFoundError(str(exc)) from exc
            if exc.kind == "SandboxGone":
                raise SandboxGone(self.id, str(exc)) from exc
            raise

    async def spawn(self, cmd: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None) -> ProcessHandle:
        raise NotImplementedError("a runner hand runs commands to completion (exec), not interactively")

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        out = await self._call(
            "exec",
            {"cmd": list(cmd), "cwd": cwd, "env": env or {}, "timeout": timeout},
            timeout_s=(timeout or 300.0) + 30.0,
        )
        return ExecResult(
            exit_code=int(out["exit_code"]),
            stdout=base64.b64decode(out.get("stdout", "")),
            stderr=base64.b64decode(out.get("stderr", "")),
            duration_ms=int(out.get("duration_ms", 0)),
        )

    async def read_file(self, path: str) -> bytes:
        return base64.b64decode((await self._call("read", {"path": path}))["data"])

    async def write_file(self, path: str, data: bytes) -> None:
        await self._call("write", {"path": path, "data": base64.b64encode(data).decode()})

    async def exists(self, path: str) -> bool:
        return bool((await self._call("exists", {"path": path}))["exists"])

    async def pause(self) -> str:
        raise SnapshotUnsupported("runner")

    async def destroy(self) -> None:
        if self._gone:
            return
        try:
            await self._call("destroy")
        except SandboxGone:
            pass  # an offline runner's hand is gone either way
        finally:
            self._gone = True

    async def is_alive(self) -> bool:
        return not self._gone and await self._hub.online(self._tenant, self._runner)

    def as_tree_io(self) -> RunnerTreeIO:
        """The hand's files as a tree (checkpoint restore, scope hydration)."""
        return RunnerTreeIO(self)


class RunnerTreeIO:
    """``WorkspaceTreeIO`` over a runner hand's directory."""

    def __init__(self, sandbox: RunnerSandbox) -> None:
        self._sandbox = sandbox

    async def write_bytes(self, path: str, data: bytes) -> None:
        await self._sandbox.write_file(path, data)

    async def read_bytes(self, path: str) -> bytes:
        return await self._sandbox.read_file(path)

    async def list_paths(self) -> list[str]:
        return [str(p) for p in (await self._sandbox._call("list"))["paths"]]

    async def delete_path(self, path: str) -> None:
        await self._sandbox._call("delete", {"path": path})

    async def clear(self) -> None:
        await self._sandbox._call("clear")


class RunnerProvider(SandboxProvider):
    def __init__(self, hub: RunnerHub | None = None) -> None:
        self._hub = hub

    @property
    def hub(self) -> RunnerHub:
        return self._hub or current_hub()

    @property
    def name(self) -> str:
        return "runner"

    async def create(
        self,
        *,
        image: str | None = None,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> Sandbox:
        del image, workdir, env  # the runner's machine is the image; its directory the workdir
        labels = labels or {}
        tenant, runner, key = labels.get(LABEL_TENANT), labels.get(LABEL_RUNNER), labels.get(LABEL_SESSION)
        if not (tenant and runner and key):
            raise ValueError("a runner hand needs its tenant, runner and hand key (labels)")
        sandbox = RunnerSandbox(self.hub, tenant, runner, _sandbox_id(key))
        await sandbox._call("create", {"labels": labels})
        return sandbox

    async def adopt(self, session_id: str) -> Sandbox | None:
        return None  # the next build reuses the hand's directory (create is idempotent)

    async def resume(self, snapshot_id: str) -> Sandbox:
        raise SnapshotUnsupported("runner")

    async def get(self, sandbox_id: str) -> Sandbox | None:
        return None
