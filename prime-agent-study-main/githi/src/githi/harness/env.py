"""Execution environment abstraction (port of `harness/types.ts`).

Three connected ideas, in dependency order.

**`Result[T, E]`.** Filesystem and shell operations return failure as a
*value*. A tool's job is to turn every error into something the model can
read and act on, so an exception has to be caught at every call site anyway;
making the failure part of the return type means it cannot be silently
forgotten, and the "happy path" reads the same as the error path.

**Backend-independent error codes.** `not_found` means the same thing whether
the file was missing on local disk, inside a container, or over SSH. Without
a stable code, callers end up matching on message text — which is exactly
what breaks the moment the backend changes.

**`ExecutionEnv = FileSystem + Shell`.** The seam that lets the *same* tools
run somewhere else. githi's tools currently reach for `pathlib` and
`subprocess` directly, which is why its sandbox cannot be a container: there
is nowhere to substitute a different implementation. Taking an `ExecutionEnv`
instead makes a Docker or SSH backend a new class rather than a rewrite of
every tool.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Result(Generic[T, E]):
    """Success with a value, or failure with an error — never both."""

    _value: T | None = None
    _error: E | None = None
    is_ok: bool = True

    @property
    def value(self) -> T:
        if not self.is_ok:
            raise ValueError("Result is an error; check `is_ok` first")
        return self._value            # type: ignore[return-value]

    @property
    def error(self) -> E:
        if self.is_ok:
            raise ValueError("Result is ok; check `is_ok` first")
        return self._error            # type: ignore[return-value]

    def unwrap_or(self, default: T) -> T:
        return self._value if self.is_ok else default   # type: ignore[return-value]


def ok(value: T = None) -> Result[T, Any]:
    """Success. Note `is_ok` is an explicit flag rather than a truthiness
    check on the value: `ok(0)`, `ok("")` and `ok(False)` are successes, and
    a falsy-value test would report them as failures."""
    return Result(_value=value, is_ok=True)


def err(error: E) -> Result[Any, E]:
    return Result(_error=error, is_ok=False)


def get_or_raise(result: Result[T, E]) -> T:
    """Unwrap, raising the contained error. For call sites that genuinely
    cannot proceed — the escape hatch, not the default."""
    if result.is_ok:
        return result.value
    error = result.error
    raise error if isinstance(error, Exception) else RuntimeError(str(error))


class FileErrorCode(str, Enum):
    ABORTED = "aborted"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    NOT_DIRECTORY = "not_directory"
    IS_DIRECTORY = "is_directory"
    INVALID = "invalid"
    NOT_SUPPORTED = "not_supported"
    UNKNOWN = "unknown"


class ExecutionErrorCode(str, Enum):
    ABORTED = "aborted"
    TIMEOUT = "timeout"
    SHELL_UNAVAILABLE = "shell_unavailable"
    SPAWN_ERROR = "spawn_error"
    CALLBACK_ERROR = "callback_error"
    UNKNOWN = "unknown"


def _coerce(code: Any, enum_cls: type) -> Any:
    """Map a string onto its code, falling back to `unknown`.

    A backend reporting something unmapped must degrade, not raise — failing
    here would crash the code path that exists to *report* a failure.
    """
    if isinstance(code, enum_cls):
        return code
    try:
        return enum_cls(code)
    except ValueError:
        return enum_cls.UNKNOWN


class FileError(Exception):
    def __init__(self, code: FileErrorCode | str, message: str,
                 path: str | None = None, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.code = _coerce(code, FileErrorCode)
        self.path = path
        self.cause = cause


class ExecutionError(Exception):
    def __init__(self, code: ExecutionErrorCode | str, message: str,
                 cause: Exception | None = None,
                 partial_stdout: str = "", partial_stderr: str = "") -> None:
        super().__init__(message)
        self.code = _coerce(code, ExecutionErrorCode)
        self.cause = cause
        self.partial_stdout = partial_stdout
        self.partial_stderr = partial_stderr
        """What the command managed to emit before it was killed.

        A command that times out has usually done most of its work, and the
        output up to that point is often exactly what identifies the hang.
        Discarding it forces a re-run of the thing that already took too long.
        """


@dataclass
class FileInfo:
    name: str
    path: str
    kind: str            # "file" | "directory" | "symlink"
    size: int = 0
    modified: float = 0.0


@dataclass
class ExecOutput:
    stdout: str
    stderr: str
    exit_code: int


@runtime_checkable
class ExecutionEnv(Protocol):
    """`FileSystem + Shell` in one object.

    Deliberately a single protocol rather than two: a tool that needed both
    would otherwise take two dependencies that must refer to the *same*
    backend, and nothing would enforce that they do.
    """

    cwd: Path

    def absolute_path(self, path: str) -> Result[str, FileError]: ...
    def read_text_file(self, path: str) -> Result[str, FileError]: ...
    def write_file(self, path: str, content: str) -> Result[None, FileError]: ...
    def exists(self, path: str) -> Result[bool, FileError]: ...
    def list_dir(self, path: str) -> Result[list[FileInfo], FileError]: ...
    def exec(self, command: str, **options: Any) -> Result[ExecOutput, ExecutionError]: ...


def _classify_os_error(exc: OSError) -> FileErrorCode:
    import errno

    return {
        errno.ENOENT: FileErrorCode.NOT_FOUND,
        errno.EACCES: FileErrorCode.PERMISSION_DENIED,
        errno.EPERM: FileErrorCode.PERMISSION_DENIED,
        errno.ENOTDIR: FileErrorCode.NOT_DIRECTORY,
        errno.EISDIR: FileErrorCode.IS_DIRECTORY,
        errno.EINVAL: FileErrorCode.INVALID,
    }.get(exc.errno, FileErrorCode.UNKNOWN)


class LocalExecutionEnv:
    """The local machine. A Docker or SSH backend implements the same
    protocol and every tool keeps working unchanged."""

    def __init__(self, cwd: Path | str = ".") -> None:
        self.cwd = Path(cwd).resolve()

    # ---------------- filesystem ----------------

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.cwd / candidate

    def absolute_path(self, path: str) -> Result[str, FileError]:
        return ok(str(self._resolve(path)))

    def join_path(self, parts: list[str]) -> Result[str, FileError]:
        return ok(str(Path(*parts)))

    def read_text_file(self, path: str) -> Result[str, FileError]:
        target = self._resolve(path)
        if target.is_dir():
            return err(FileError(FileErrorCode.IS_DIRECTORY,
                                 f"{path} is a directory", str(target)))
        try:
            return ok(target.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return err(FileError(_classify_os_error(exc), str(exc), str(target), exc))

    def read_text_lines(self, path: str, max_lines: int | None = None
                        ) -> Result[list[str], FileError]:
        result = self.read_text_file(path)
        if not result.is_ok:
            return result                      # type: ignore[return-value]
        lines = result.value.splitlines()
        return ok(lines[:max_lines] if max_lines else lines)

    def read_binary_file(self, path: str) -> Result[bytes, FileError]:
        target = self._resolve(path)
        try:
            return ok(target.read_bytes())
        except OSError as exc:
            return err(FileError(_classify_os_error(exc), str(exc), str(target), exc))

    def write_file(self, path: str, content: str | bytes) -> Result[None, FileError]:
        target = self._resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
            return ok(None)
        except OSError as exc:
            return err(FileError(_classify_os_error(exc), str(exc), str(target), exc))

    def append_file(self, path: str, content: str) -> Result[None, FileError]:
        target = self._resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
            return ok(None)
        except OSError as exc:
            return err(FileError(_classify_os_error(exc), str(exc), str(target), exc))

    def exists(self, path: str) -> Result[bool, FileError]:
        return ok(self._resolve(path).exists())

    def file_info(self, path: str) -> Result[FileInfo, FileError]:
        target = self._resolve(path)
        try:
            stat = target.stat()
        except OSError as exc:
            return err(FileError(_classify_os_error(exc), str(exc), str(target), exc))
        return ok(_info(target, stat))

    def list_dir(self, path: str) -> Result[list[FileInfo], FileError]:
        target = self._resolve(path)
        if target.is_file():
            return err(FileError(FileErrorCode.NOT_DIRECTORY,
                                 f"{path} is a file", str(target)))
        try:
            entries = sorted(target.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            return err(FileError(_classify_os_error(exc), str(exc), str(target), exc))
        return ok([_info(entry, entry.stat()) for entry in entries])

    def canonical_path(self, path: str) -> Result[str, FileError]:
        return ok(str(self._resolve(path).resolve()))

    def create_dir(self, path: str, recursive: bool = False) -> Result[None, FileError]:
        target = self._resolve(path)
        try:
            target.mkdir(parents=recursive, exist_ok=recursive)
            return ok(None)
        except OSError as exc:
            return err(FileError(_classify_os_error(exc), str(exc), str(target), exc))

    def remove(self, path: str, recursive: bool = False,
               force: bool = False) -> Result[None, FileError]:
        target = self._resolve(path)
        try:
            if target.is_dir():
                shutil.rmtree(target) if recursive else target.rmdir()
            else:
                target.unlink(missing_ok=force)
            return ok(None)
        except OSError as exc:
            if force:
                return ok(None)
            return err(FileError(_classify_os_error(exc), str(exc), str(target), exc))

    def cleanup(self) -> None:
        """No-op locally. A container backend tears down the container here,
        which is why the method exists on the protocol at all."""

    # ---------------- shell ----------------

    def exec(self, command: str, cwd: str | None = None,
             env: dict[str, str] | None = None, inherit_env: bool = True,
             timeout: float | None = None, interrupt: Any = None,
             on_output: Any = None) -> Result[ExecOutput, ExecutionError]:
        """Run a command.

        A non-zero exit is a **successful** result carrying that code: the
        execution worked, the program disagreed. Only an inability to run the
        command at all — timeout, spawn failure, no shell — is an
        `ExecutionError`. Conflating the two makes `grep` finding nothing
        indistinguishable from `grep` being missing.
        """
        environment = dict(os.environ) if inherit_env else {}
        environment.update(env or {})

        try:
            # `Popen`, not `subprocess.run`, specifically to have the pid in
            # hand at timeout. `run` raises `TimeoutExpired`, which carries
            # cmd, timeout and output but **no pid** -- leaving nothing to
            # signal, so anything the command backgrounded outlives the kill
            # and keeps running untracked.
            proc = subprocess.Popen(
                ["bash", "-lc", command],
                cwd=str(self._resolve(cwd) if cwd else self.cwd),
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
                # New session => the child leads its own process group, so a
                # single killpg reaches every descendant it spawned.
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return err(ExecutionError(ExecutionErrorCode.SHELL_UNAVAILABLE,
                                      "bash not available", exc))
        except OSError as exc:
            return err(ExecutionError(ExecutionErrorCode.SPAWN_ERROR, str(exc), exc))

        output: queue.Queue[tuple[str, str | None]] = queue.Queue()

        def drain(stream_name: str, pipe: Any) -> None:
            try:
                for chunk in iter(pipe.readline, ""):
                    output.put((stream_name, chunk))
            finally:
                output.put((stream_name, None))

        readers = [
            threading.Thread(target=drain, args=("stdout", proc.stdout), daemon=True),
            threading.Thread(target=drain, args=("stderr", proc.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        closed: set[str] = set()
        deadline = time.monotonic() + timeout if timeout is not None else None
        failure: ExecutionError | None = None

        while len(closed) < 2 or proc.poll() is None:
            if failure is None and _is_interrupted(interrupt):
                failure = ExecutionError(ExecutionErrorCode.ABORTED, "command interrupted")
                _kill_process_group(proc)
            elif failure is None and deadline is not None and time.monotonic() >= deadline:
                failure = ExecutionError(
                    ExecutionErrorCode.TIMEOUT, f"command exceeded {timeout}s")
                _kill_process_group(proc)

            try:
                stream_name, chunk = output.get(timeout=0.05)
            except queue.Empty:
                continue
            if chunk is None:
                closed.add(stream_name)
                continue

            (stdout_parts if stream_name == "stdout" else stderr_parts).append(chunk)
            if on_output is not None:
                try:
                    on_output(stream_name, chunk)
                except Exception as exc:  # noqa: BLE001 - callback must not leak a process
                    if failure is None:
                        failure = ExecutionError(ExecutionErrorCode.CALLBACK_ERROR,
                                                 str(exc), exc)
                        _kill_process_group(proc)

        for reader in readers:
            reader.join(timeout=1)

        stdout, stderr = "".join(stdout_parts), "".join(stderr_parts)
        if failure is not None:
            failure.partial_stdout = stdout
            failure.partial_stderr = stderr
            return err(failure)
        return ok(ExecOutput(stdout=stdout, stderr=stderr, exit_code=proc.returncode))


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGKILL the whole group, tolerating a race with normal exit.

    The process may have finished between the timeout firing and this call, in
    which case the pid is gone and `killpg` raises -- which is a success, not
    a failure worth propagating.
    """
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:                            # pragma: no cover
            pass


def _is_interrupted(interrupt: Any) -> bool:
    """Accept the existing `Interrupt`, `threading.Event`, or no signal."""
    try:
        return bool(interrupt and interrupt.is_set())
    except Exception:  # noqa: BLE001 - a bad optional signal must not crash shell cleanup
        return False


def _info(path: Path, stat: os.stat_result) -> FileInfo:
    kind = "symlink" if path.is_symlink() else ("directory" if path.is_dir() else "file")
    return FileInfo(name=path.name, path=str(path), kind=kind,
                    size=stat.st_size, modified=stat.st_mtime)
