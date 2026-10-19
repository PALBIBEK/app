"""The filesystem and shell a tool acts on, behind one interface.

A session's files live in its sandbox (``SandboxWorkspace``: files and commands
both inside the sandbox), in a sandbox file tree reached through
``WorkspaceTreeIO`` (``TreeWorkspace``), or, for a harness that pins no
sandbox, in a directory on the supervisor's host (``HostWorkspace``).
``workspace_for`` picks the right one. Tools are written once against
``Workspace`` and never learn which one they were given:

* every path a model supplies goes through ``relpath`` first, which confines it
  to the workspace (``..`` segments, foreign absolute paths and, on the host,
  symlinks pointing outside are all refused);
* every path a tool returns is workspace-relative, so output reads the same
  wherever the session runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from supervisor.tools.errors import FileMissing, PathEscape, UnsupportedCapability

if TYPE_CHECKING:
    from supervisor.sandbox.base import Sandbox
    from supervisor.seams.services import WorkspaceTreeIO

__all__ = [
    "LOGICAL_ROOT",
    "CommandResult",
    "Entry",
    "HostWorkspace",
    "SandboxWorkspace",
    "TreeWorkspace",
    "Workspace",
    "workspace_for",
]

LOGICAL_ROOT: Final = "/workspace"
"""Where the workspace is mounted in a sandbox; accepted as a path prefix everywhere."""

_MAX_STREAM_CHARS: Final = 64_000
"""Per-stream cap on command output kept for the model; the rest is cut with a notice."""

_DRAIN_S: Final = 5.0
"""How long to wait for a finished command's pipes to close."""

_SHELL_ENV_KEYS: Final = ("PATH", "LANG", "LC_ALL", "TZ")
"""Host variables a command inherits. Nothing else leaks: the supervisor's own
environment holds service tokens and database URLs the agent must never see."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One child of a directory."""

    name: str
    is_dir: bool

    def __str__(self) -> str:
        return f"{self.name}/" if self.is_dir else self.name


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


@runtime_checkable
class Workspace(Protocol):
    """A session's files, plus a shell if the backing store can run one."""

    def relpath(self, path: str) -> str:
        """Confine ``path`` to the workspace; ``""`` is the root. Raises ``PathEscape``."""
        ...

    async def read_bytes(self, path: str) -> bytes: ...

    async def write_bytes(self, path: str, data: bytes) -> None: ...

    async def list_dir(self, path: str) -> list[Entry]:
        """Children of a directory, sorted by name. Raises ``FileMissing``."""
        ...

    async def files(self, path: str) -> list[str]:
        """Every file at or below ``path``, workspace-relative and sorted."""
        ...

    async def run(
        self, command: str, *, timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        """Run a shell command in the workspace root, with ``env`` added to its
        scrubbed environment. Raises ``UnsupportedCapability``."""
        ...


def _logical_relpath(path: str) -> str:
    """Normalize a model-supplied path without touching any filesystem."""
    text = str(path).strip().replace("\\", "/")
    if "\x00" in text:
        raise PathEscape(path)
    pure = PurePosixPath(text)
    if pure.is_absolute():
        try:
            pure = pure.relative_to(LOGICAL_ROOT)
        except ValueError:
            raise PathEscape(path) from None
    parts = [part for part in pure.parts if part != "."]
    if ".." in parts:
        raise PathEscape(path)
    return "/".join(parts)


class HostWorkspace:
    """A directory on the supervisor's host; also runs commands there."""

    __slots__ = ("_root",)

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def __repr__(self) -> str:
        return f"HostWorkspace({str(self._root)!r})"

    def relpath(self, path: str) -> str:
        raw = Path(str(path).strip()).expanduser()
        if raw.is_absolute() and raw.is_relative_to(self._root):
            path = raw.relative_to(self._root).as_posix()
        rel = _logical_relpath(path)
        # Lexically inside; now make sure no symlink on the way leads out.
        if not (self._root / rel).resolve().is_relative_to(self._root):
            raise PathEscape(path)
        return rel

    def _host(self, rel: str) -> Path:
        return self._root / rel

    async def read_bytes(self, path: str) -> bytes:
        rel = self.relpath(path)
        target = self._host(rel)
        if not target.is_file():
            raise FileMissing(rel or ".")
        return await asyncio.to_thread(target.read_bytes)

    async def write_bytes(self, path: str, data: bytes) -> None:
        rel = self.relpath(path)
        if not rel:
            raise PathEscape(path)
        target = self._host(rel)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        await asyncio.to_thread(write)

    async def list_dir(self, path: str) -> list[Entry]:
        rel = self.relpath(path)
        target = self._host(rel)
        if not target.is_dir():
            raise FileMissing(rel or ".", kind="directory")
        children = await asyncio.to_thread(lambda: sorted(target.iterdir()))
        return [Entry(child.name, child.is_dir()) for child in children]

    async def files(self, path: str) -> list[str]:
        rel = self.relpath(path)
        start = self._host(rel)
        if start.is_file():
            return [rel]
        if not start.is_dir():
            raise FileMissing(rel or ".", kind="path")
        return await asyncio.to_thread(lambda: sorted(self._walk(start)))

    def _walk(self, start: Path) -> Iterator[str]:
        for dirpath, dirnames, filenames in os.walk(start):
            # Never follow a directory symlink out of the workspace.
            dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
            for name in filenames:
                yield (Path(dirpath) / name).relative_to(self._root).as_posix()

    async def run(
        self, command: str, *, timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        shell = shutil.which("bash") or "/bin/sh"
        scrubbed = {key: os.environ[key] for key in _SHELL_ENV_KEYS if key in os.environ}
        scrubbed |= {**(env or {}), "HOME": str(self._root), "PWD": str(self._root), "TERM": "dumb"}
        proc = await asyncio.create_subprocess_exec(
            shell,
            "-c",
            command,
            cwd=self._root,
            env=scrubbed,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # its own process group, so a timeout kills children too
        )
        assert proc.stdout is not None and proc.stderr is not None
        # Readers of their own, so output produced before a kill is still returned.
        readers = asyncio.gather(proc.stdout.read(), proc.stderr.read())
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):  # it may have just exited
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        try:
            out, err = await asyncio.wait_for(readers, _DRAIN_S)
        except TimeoutError:  # a process that left the group still holds the pipes
            out, err = b"", b"[output unavailable: a background process kept it open]"
        stdout, cut_out = _clip(out)
        stderr, cut_err = _clip(err)
        return CommandResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=cut_out or cut_err,
        )


def _clip(raw: bytes) -> tuple[str, bool]:
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= _MAX_STREAM_CHARS:
        return text, False
    dropped = len(text) - _MAX_STREAM_CHARS
    return f"{text[:_MAX_STREAM_CHARS]}\n[... {dropped} more characters cut]", True


class TreeWorkspace:
    """A sandbox's ``/workspace`` seen through ``WorkspaceTreeIO``.

    ``WorkspaceTreeIO`` only knows files, so directories are inferred from the
    paths under them. It cannot run processes.
    """

    __slots__ = ("_tree",)

    def __init__(self, tree: WorkspaceTreeIO) -> None:
        self._tree = tree

    def __repr__(self) -> str:
        return f"TreeWorkspace({self._tree!r})"

    def relpath(self, path: str) -> str:
        return _logical_relpath(path)

    async def read_bytes(self, path: str) -> bytes:
        rel = self.relpath(path)
        with _store_escapes(path):
            try:
                return await self._tree.read_bytes(rel)
            except (FileNotFoundError, KeyError):
                raise FileMissing(rel or ".") from None

    async def write_bytes(self, path: str, data: bytes) -> None:
        rel = self.relpath(path)
        if not rel:
            raise PathEscape(path)
        with _store_escapes(path):
            await self._tree.write_bytes(rel, data)

    async def list_dir(self, path: str) -> list[Entry]:
        rel = self.relpath(path)
        prefix = f"{rel}/" if rel else ""
        children: dict[str, bool] = {}
        for file in await self._tree.list_paths():
            if not file.startswith(prefix):
                continue
            head, sep, _ = file[len(prefix) :].partition("/")
            children[head] = children.get(head, False) or bool(sep)
        if rel and not children:
            raise FileMissing(rel, kind="directory")
        return [Entry(name, is_dir) for name, is_dir in sorted(children.items())]

    async def files(self, path: str) -> list[str]:
        rel = self.relpath(path)
        every = await self._tree.list_paths()
        if not rel:
            return sorted(every)
        if rel in every:
            return [rel]
        matched = sorted(file for file in every if file.startswith(f"{rel}/"))
        if not matched:
            raise FileMissing(rel, kind="path")
        return matched

    async def run(
        self, command: str, *, timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        raise UnsupportedCapability(
            "this session's workspace is a sandbox file tree; it cannot run commands"
        )


_MISSING: Final = 3
"""Exit status the listing scripts use for "no such path"."""

# POSIX sh only: sandbox images may ship busybox rather than GNU tools.
_LIST_DIR: Final = """\
cd -- "$1" 2>/dev/null || exit 3
for entry in * .[!.]* ..?*; do
  [ -e "$entry" ] || [ -L "$entry" ] || continue
  if [ -d "$entry" ]; then echo "d $entry"; else echo "f $entry"; fi
done
"""
_FILES: Final = """\
if [ -f "$1" ]; then echo "$1"; elif [ -d "$1" ]; then find "$1" -type f; else exit 3; fi
"""


class SandboxWorkspace:
    """The session's sandbox: files read and written there, commands run there.

    Paths are confined lexically before they reach the sandbox; the sandbox
    itself is the isolation boundary for everything else (symlinks included).
    """

    __slots__ = ("_sandbox",)

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    def __repr__(self) -> str:
        return f"SandboxWorkspace({self._sandbox.info.provider}:{self._sandbox.id})"

    def relpath(self, path: str) -> str:
        return _logical_relpath(path)

    def _abs(self, rel: str) -> str:
        root = self._sandbox.info.workdir.rstrip("/") or LOGICAL_ROOT
        return f"{root}/{rel}" if rel else root

    async def read_bytes(self, path: str) -> bytes:
        rel = self.relpath(path)
        with _sandbox_errors():
            try:
                return await self._sandbox.read_file(self._abs(rel))
            except (FileNotFoundError, IsADirectoryError):
                raise FileMissing(rel or ".") from None

    async def write_bytes(self, path: str, data: bytes) -> None:
        rel = self.relpath(path)
        if not rel:
            raise PathEscape(path)
        with _sandbox_errors():
            await self._sandbox.write_file(self._abs(rel), data)

    async def list_dir(self, path: str) -> list[Entry]:
        rel = self.relpath(path)
        lines = await self._script(
            _LIST_DIR, rel, missing=FileMissing(rel or ".", kind="directory")
        )
        entries = (Entry(line[2:], line[0] == "d") for line in lines if line[1:2] == " ")
        return sorted(entries, key=lambda entry: entry.name)

    async def files(self, path: str) -> list[str]:
        rel = self.relpath(path)
        lines = await self._script(_FILES, rel, missing=FileMissing(rel or ".", kind="path"))
        return sorted(line.removeprefix("./") for line in lines if line)

    async def _script(self, script: str, rel: str, *, missing: FileMissing) -> list[str]:
        with _sandbox_errors():
            done = await self._sandbox.exec(["sh", "-c", script, "sh", f"./{rel}" if rel else "."])
        if done.exit_code == _MISSING:
            raise missing
        return done.stdout_text.splitlines()

    async def run(
        self, command: str, *, timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        from supervisor.sandbox.base import SandboxTimeout

        with _sandbox_errors():
            try:
                done = await self._sandbox.exec(
                    ["sh", "-c", command], env=dict(env) if env else None, timeout=timeout_s
                )
            except SandboxTimeout:
                return CommandResult(exit_code=-1, stdout="", stderr="", timed_out=True)
        stdout, cut_out = _clip(done.stdout)
        stderr, cut_err = _clip(done.stderr)
        return CommandResult(
            exit_code=done.exit_code,
            stdout=stdout,
            stderr=stderr,
            truncated=cut_out or cut_err,
        )


@contextlib.contextmanager
def _store_escapes(path: str) -> Iterator[None]:
    """The store refuses a symlink out of its tree; to the model that is ``PATH_ESCAPE`` (F22)."""
    from supervisor.seams.services import WorkspacePathEscape

    try:
        yield
    except WorkspacePathEscape as exc:
        raise PathEscape(path) from exc


@contextlib.contextmanager
def _sandbox_errors() -> Iterator[None]:
    """A sandbox that cannot do something is a capability gap, not a tool crash;
    a path the sandbox refuses (a symlink out, say) is ``PATH_ESCAPE`` (F22)."""
    from supervisor.sandbox.base import SandboxError, SandboxPathEscape

    try:
        yield
    except SandboxPathEscape as exc:
        raise PathEscape(exc.path) from exc
    except (NotImplementedError, SandboxError) as exc:
        raise UnsupportedCapability(f"the session's sandbox cannot do this: {exc}") from exc


def workspace_for(sandbox: Sandbox | None, host_dir: str | os.PathLike[str]) -> Workspace:
    """The workspace a session's tools act on: its sandbox when it has one."""
    return SandboxWorkspace(sandbox) if sandbox is not None else HostWorkspace(host_dir)
