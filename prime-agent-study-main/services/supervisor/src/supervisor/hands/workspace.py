"""The session's workspace, in whichever sandbox currently serves it."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Final

from supervisor.tools.errors import PathEscape, WorkspaceReset, WorkspaceUnavailable
from supervisor.tools.workspace import CommandResult, Entry, SandboxWorkspace, logical_relpath

if TYPE_CHECKING:
    from supervisor.sandbox.base import Sandbox

logger = logging.getLogger(__name__)

__all__ = ["HandWorkspace"]

_PROBE_S: Final = 10.0
"""How long a liveness probe may take before the sandbox counts as gone."""


class HandWorkspace:
    """A ``Workspace`` over the session's current sandbox, which may change.

    Tools see one workspace for the whole session. Each operation runs in the
    sandbox serving the session at that moment. When an operation fails, the
    sandbox is probed: if it is gone, the failure becomes ``WorkspaceReset`` (the
    model is told and the session replaces the sandbox); if it is alive, the
    failure is the tool's own and passes through unchanged.
    """

    __slots__ = ("_current",)

    def __init__(self, current: Callable[[], Sandbox | None]) -> None:
        self._current = current

    def __repr__(self) -> str:
        sandbox = self._current()
        return (
            f"HandWorkspace({sandbox.info.provider}:{sandbox.id})" if sandbox else "HandWorkspace()"
        )

    def relpath(self, path: str) -> str:
        return logical_relpath(path)

    async def read_bytes(self, path: str) -> bytes:
        return await self._in_sandbox(lambda ws: ws.read_bytes(path))

    async def write_bytes(self, path: str, data: bytes) -> None:
        await self._in_sandbox(lambda ws: ws.write_bytes(path, data))

    async def list_dir(self, path: str) -> list[Entry]:
        return await self._in_sandbox(lambda ws: ws.list_dir(path))

    async def files(self, path: str) -> list[str]:
        return await self._in_sandbox(lambda ws: ws.files(path))

    async def run(
        self, command: str, *, timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        return await self._in_sandbox(lambda ws: ws.run(command, timeout_s=timeout_s, env=env))

    async def _in_sandbox[T](self, op: Callable[[SandboxWorkspace], Awaitable[T]]) -> T:
        sandbox = self._current()
        if sandbox is None:
            raise WorkspaceUnavailable("the session's sandbox is not running")
        try:
            return await op(SandboxWorkspace(sandbox))
        except PathEscape:
            raise  # decided from the path alone, before the sandbox was asked
        except Exception as exc:
            if await _alive(sandbox):
                raise
            logger.warning("sandbox %s is gone: %s", sandbox.id, exc)
            raise WorkspaceReset(_reason(exc)) from exc


async def _alive(sandbox: Sandbox) -> bool:
    try:
        return await asyncio.wait_for(sandbox.is_alive(), _PROBE_S)
    except Exception:  # noqa: BLE001 - a sandbox that cannot answer is not serving
        return False


def _reason(exc: BaseException) -> str:
    from supervisor.sandbox.base import SandboxGone

    if isinstance(exc, SandboxGone):
        return exc.reason
    cause = exc.__cause__ or exc
    return f"it is no longer running; the call failed with {type(cause).__name__}: {cause}"[:240]
