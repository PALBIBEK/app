"""Local Docker sandbox backend.

Containers outlive the supervisor process. Label every container so ``get()``
and orphan reaping can reattach after a restart. Always run with ``init=True``.
"""

from __future__ import annotations

import asyncio
import io
import logging
import tarfile
from typing import Any

from supervisor.sandbox.backup import (
    BackupArea,
    BackupResult,
    create_exec_backup,
)
from supervisor.sandbox.base import (
    ByteReader,
    ByteWriter,
    ExecResult,
    ProcessHandle,
    Sandbox,
    SandboxGone,
    SandboxInfo,
    SandboxProvider,
    WorkspaceSyncUnsupported,
)
from supervisor.sandbox.security.env import sanitize_sandbox_env
from supervisor.seams.providers.workspace import normalize_workspace_path

# From the defining modules, not the package (see providers/e2b.py: an import cycle).
from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator
from supervisor.seams.services.workspace_store import (
    WorkspacePathEscape,
    WorkspaceSyncReport,
    WorkspaceTreeIO,
)

logger = logging.getLogger(__name__)

LABEL_SESSION = "supervisor.session_id"
LABEL_HARNESS = "supervisor.harness_version"
LABEL_MANAGED = "supervisor.managed"

# Default container hardening for local_docker create (D10 T12).
_DOCKER_MEM_LIMIT = "512m"
_DOCKER_NANO_CPUS = 1_000_000_000
_DOCKER_CAP_DROP = ["ALL"]
_DOCKER_NETWORK_MODE = "none"


# ---------------------------------------------------------------------------
# Docker multiplex demux (8-byte headers)
# ---------------------------------------------------------------------------


class _QueueReader:
    """ByteReader backed by an asyncio.Queue of byte chunks."""

    def __init__(self, queue: asyncio.Queue[bytes | None]) -> None:
        self._q = queue
        self._buf = bytearray()
        self._eof = False

    async def read(self, n: int) -> bytes:
        """Read up to ``n`` bytes (pipe semantics — may return fewer)."""
        if n <= 0:
            return b""
        while not self._buf and not self._eof:
            chunk = await self._q.get()
            if chunk is None:
                self._eof = True
                break
            self._buf.extend(chunk)
        if not self._buf:
            return b""
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class _QueueWriter:
    def __init__(self, write_fn) -> None:  # type: ignore[no-untyped-def]
        self._write_fn = write_fn
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise RuntimeError("stdin closed")
        self._write_fn(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)


async def _demux_docker_socket(
    sock: Any,
    stdout_q: asyncio.Queue[bytes | None],
    stderr_q: asyncio.Queue[bytes | None],
) -> None:
    """Demultiplex Docker attach/exec stream into stdout and stderr queues."""
    loop = asyncio.get_running_loop()
    raw = sock._sock if hasattr(sock, "_sock") else sock

    def _recv(n: int) -> bytes:
        return raw.recv(n)

    try:
        while True:
            header = await loop.run_in_executor(None, _recv, 8)
            if not header or len(header) < 8:
                break
            stream_type = header[0]
            size = int.from_bytes(header[4:8], "big")
            payload = b""
            while len(payload) < size:
                chunk = await loop.run_in_executor(None, _recv, size - len(payload))
                if not chunk:
                    break
                payload += chunk
            if stream_type == 1:
                await stdout_q.put(payload)
            elif stream_type == 2:
                await stderr_q.put(payload)
            # 0 = stdin echo — ignore
    except Exception:
        logger.exception("docker demux failed")
    finally:
        await stdout_q.put(None)
        await stderr_q.put(None)


class DockerProcessHandle(ProcessHandle):
    def __init__(
        self,
        *,
        exec_id: str,
        client: Any,
        stdin_w: ByteWriter,
        stdout_r: ByteReader,
        stderr_r: ByteReader,
        demux_task: asyncio.Task[None],
    ) -> None:
        self._exec_id = exec_id
        self._client = client
        self._stdin = stdin_w
        self._stdout = stdout_r
        self._stderr = stderr_r
        self._demux_task = demux_task
        self._rc: int | None = None

    @property
    def stdin(self) -> ByteWriter:
        return self._stdin

    @property
    def stdout(self) -> ByteReader:
        return self._stdout

    @property
    def stderr(self) -> ByteReader:
        return self._stderr

    @property
    def pid(self) -> int | None:
        return None

    def returncode(self) -> int | None:
        return self._rc

    async def wait(self) -> int:
        loop = asyncio.get_running_loop()
        while True:
            inspect = await loop.run_in_executor(
                None, lambda: self._client.api.exec_inspect(self._exec_id)
            )
            if not inspect.get("Running", True):
                self._rc = int(inspect.get("ExitCode") or 0)
                return self._rc
            await asyncio.sleep(0.05)

    async def terminate(self) -> None:
        # Docker exec has no SIGTERM; kill the exec by waiting/closing.
        await self.kill()

    async def kill(self) -> None:
        self._demux_task.cancel()
        try:
            await self._demux_task
        except asyncio.CancelledError:
            pass
        if self._rc is None:
            self._rc = -9


class DockerWorkspaceTreeIO:
    """WorkspaceTreeIO over a Docker sandbox ``/workspace`` (Stage 5 / D10)."""

    def __init__(self, sandbox: LocalDockerSandbox) -> None:
        self._sb = sandbox
        self._root = (sandbox.info.workdir or "/workspace").rstrip("/") or "/workspace"

    def _abs(self, path: str) -> str:
        rel = normalize_workspace_path(path)
        return f"{self._root}/{rel}"

    async def write_bytes(self, path: str, data: bytes) -> None:
        await self._sb.write_file(self._abs(path), data)

    async def read_bytes(self, path: str) -> bytes:
        return await self._sb.read_file(self._abs(path))

    async def list_paths(self) -> list[str]:
        files = getattr(self._sb._container, "files", None)
        prefix = self._root + "/"
        if isinstance(files, dict):
            return sorted(
                p[len(prefix) :] for p in files if p.startswith(prefix) and p != self._root
            )
        result = await self._sb.exec(
            ["find", self._root, "-type", "f", "-print"], timeout=30
        )
        out: list[str] = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith(prefix):
                out.append(line[len(prefix) :])
        return sorted(out)

    async def delete_path(self, path: str) -> None:
        abs_path = self._abs(path)
        files = getattr(self._sb._container, "files", None)
        if isinstance(files, dict):
            files.pop(abs_path, None)
            return
        await self._sb.exec(["rm", "-f", "--", abs_path], timeout=30)

    async def clear(self) -> None:
        files = getattr(self._sb._container, "files", None)
        prefix = self._root + "/"
        if isinstance(files, dict):
            for key in list(files):
                if key.startswith(prefix) or key == self._root:
                    files.pop(key, None)
            return
        await self._sb.exec(
            ["sh", "-c", f"rm -rf -- {self._root}/..?* {self._root}/.[!.]* {self._root}/* 2>/dev/null || true"],
            timeout=60,
        )


class LocalDockerSandbox(Sandbox):
    def __init__(
        self,
        container: Any,
        *,
        client: Any,
        info: SandboxInfo,
        hydrator: WorkspaceHydrator | None = None,
    ) -> None:
        self._container = container
        self._client = client
        self._info = info
        self._destroyed = False
        self._hydrator = hydrator

    @property
    def info(self) -> SandboxInfo:
        return self._info

    def as_tree_io(self) -> WorkspaceTreeIO:
        return DockerWorkspaceTreeIO(self)

    async def spawn(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessHandle:
        loop = asyncio.get_running_loop()
        workdir = cwd or self._info.workdir
        cleaned = sanitize_sandbox_env(env)
        env_list = [f"{k}={v}" for k, v in cleaned.items()]

        def _create() -> str:
            return self._client.api.exec_create(
                self._container.id,
                cmd,
                stdin=True,
                stdout=True,
                stderr=True,
                tty=False,
                workdir=workdir,
                environment=env_list or None,
            )["Id"]

        exec_id = await loop.run_in_executor(None, _create)

        def _start() -> Any:
            return self._client.api.exec_start(
                exec_id, socket=True, demux=False, tty=False, stream=True
            )

        sock = await loop.run_in_executor(None, _start)
        stdout_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
        stderr_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
        demux_task = asyncio.create_task(
            _demux_docker_socket(sock, stdout_q, stderr_q),
            name=f"demux-{exec_id[:8]}",
        )

        def _stdin_write(data: bytes) -> None:
            raw = sock._sock if hasattr(sock, "_sock") else sock
            raw.sendall(data)

        return DockerProcessHandle(
            exec_id=exec_id,
            client=self._client,
            stdin_w=_QueueWriter(_stdin_write),
            stdout_r=_QueueReader(stdout_q),
            stderr_r=_QueueReader(stderr_q),
            demux_task=demux_task,
        )

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Collect output via spawn so a timeout can kill the exec, not only the client."""
        handle = await self.spawn(cmd, cwd=cwd, env=env)
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def _collect(reader: ByteReader, sink: list[bytes]) -> None:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                sink.append(chunk)

        collectors = [
            asyncio.create_task(_collect(handle.stdout, stdout_chunks)),
            asyncio.create_task(_collect(handle.stderr, stderr_chunks)),
        ]
        try:
            if timeout is None:
                code = await handle.wait()
            else:
                try:
                    code = await asyncio.wait_for(handle.wait(), timeout=timeout)
                except TimeoutError:
                    await handle.kill()
                    raise TimeoutError(
                        f"exec timed out after {timeout}s: {cmd!r}"
                    ) from None
        finally:
            await asyncio.gather(*collectors, return_exceptions=True)
        return ExecResult(
            exit_code=code,
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
        )

    async def read_file(self, path: str) -> bytes:
        loop = asyncio.get_running_loop()

        def _read() -> bytes:
            bits, _stat = self._container.get_archive(path)
            buf = io.BytesIO()
            for chunk in bits:
                buf.write(chunk)
            buf.seek(0)
            with tarfile.open(fileobj=buf, mode="r:") as tar:
                members = tar.getmembers()
                if not members:
                    return b""
                f = tar.extractfile(members[0])
                if f is None:
                    return b""
                return f.read()

        return await loop.run_in_executor(None, _read)

    async def write_file(self, path: str, data: bytes) -> None:
        loop = asyncio.get_running_loop()

        def _write() -> None:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                info = tarfile.TarInfo(name=path.lstrip("/").split("/")[-1])
                info.size = len(data)
                # Prefer absolute path parent via put_archive on parent dir
                tar.addfile(info, io.BytesIO(data))
            buf.seek(0)
            parent = path.rsplit("/", 1)[0] or "/"
            name = path.rsplit("/", 1)[-1]
            # Rebuild tar with correct member name relative to parent
            buf2 = io.BytesIO()
            with tarfile.open(fileobj=buf2, mode="w") as tar:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            buf2.seek(0)
            self._container.put_archive(parent, buf2.getvalue())

        await loop.run_in_executor(None, _write)

    async def exists(self, path: str) -> bool:
        try:
            rel = path
            if path.startswith(self._info.workdir.rstrip("/") + "/"):
                rel = path[len(self._info.workdir.rstrip("/")) + 1 :]
            abs_path = (
                path
                if path.startswith("/")
                else f"{(self._info.workdir or '/workspace').rstrip('/')}/{normalize_workspace_path(rel)}"
            )
            if ".." in abs_path.split("/"):
                raise WorkspacePathEscape(path)
            result = await self.exec(
                ["test", "-e", abs_path],
                timeout=10,
            )
            return result.exit_code == 0
        except WorkspacePathEscape:
            raise
        except Exception:
            logger.debug("docker exists check failed", exc_info=True)
            return False

    async def hydrate_workspace(self, scope_id: str) -> WorkspaceSyncReport:
        if self._hydrator is None:
            raise WorkspaceSyncUnsupported(
                "LocalDockerSandbox has no WorkspaceHydrator; wire via provider"
            )
        return await self._hydrator.hydrate(scope_id, self.as_tree_io())

    async def sync_workspace(self, scope_id: str) -> WorkspaceSyncReport:
        if self._hydrator is None:
            raise WorkspaceSyncUnsupported(
                "LocalDockerSandbox has no WorkspaceHydrator; wire via provider"
            )
        return await self._hydrator.sync(scope_id, self.as_tree_io())

    async def pause(self) -> str:
        """Filesystem snapshot via commit. Process dies — cold resume required."""
        loop = asyncio.get_running_loop()

        def _commit() -> str:
            img = self._container.commit(
                repository="supervisor-snap",
                tag=self._info.id[:12],
            )
            return img.id

        return await loop.run_in_executor(None, _commit)

    async def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        loop = asyncio.get_running_loop()

        def _rm() -> None:
            try:
                self._container.kill()
            except Exception:
                logger.debug("killing the container failed (it may have exited)", exc_info=True)
            try:
                self._container.remove(force=True)
            except Exception:
                logger.warning("removing the container failed", exc_info=True)

        await loop.run_in_executor(None, _rm)

    async def is_alive(self) -> bool:
        loop = asyncio.get_running_loop()

        def _alive() -> bool:
            try:
                self._container.reload()
                return self._container.status == "running"
            except Exception:
                logger.debug("docker liveness check failed", exc_info=True)
                return False

        return await loop.run_in_executor(None, _alive)

    async def logs(self, *, limit_bytes: int = 256_000) -> bytes | None:
        """The container's state (exit code, OOM kill, error) and the tail of its output."""
        if self._destroyed:
            return None
        loop = asyncio.get_running_loop()

        def _read() -> bytes | None:
            try:
                self._container.reload()
                state = dict(self._container.attrs.get("State") or {})
                head = (
                    f"container {self._container.id[:12]} status={state.get('Status')} "
                    f"exit_code={state.get('ExitCode')} oom_killed={state.get('OOMKilled')} "
                    f"error={state.get('Error') or '-'} finished_at={state.get('FinishedAt')}\n"
                ).encode()
                out = self._container.logs(stdout=True, stderr=True, tail=2000) or b""
            except Exception:
                logger.debug("reading the container's logs failed", exc_info=True)
                return None
            return head + bytes(out)[-max(limit_bytes - len(head), 0):]

        return await loop.run_in_executor(None, _read)

    # -------------------------------------------------------------------------
    # Computer Backup
    # -------------------------------------------------------------------------

    def _exec_fn(self, cmd: list[str], timeout: float) -> Any:
        """Create exec function for exec-based operations."""
        return self.exec(cmd, timeout=timeout)

    async def backup_computer(
        self,
        area: BackupArea,
        *,
        exclude_credentials: bool = True,
        exclude_caches: bool = True,
        timeout: float | None = None,
    ) -> BackupResult:
        """Create backup using tar with find -print0."""
        backup = create_exec_backup(exec_fn=self._exec_fn)
        return await backup.backup_computer(
            area,
            exclude_credentials=exclude_credentials,
            exclude_caches=exclude_caches,
            timeout=timeout,
        )

    # -------------------------------------------------------------------------
    # Computer Status (basic)
    # -------------------------------------------------------------------------



class LocalDockerProvider(SandboxProvider):
    name = "local_docker"

    def __init__(
        self,
        client: Any | None = None,
        *,
        hydrator: WorkspaceHydrator | None = None,
    ) -> None:
        # Fail loud at construct time when the optional ``docker`` dep is missing
        # (so resolve_sandbox_create can soft-fall back to deploy/TrackingProvider).
        # Defer docker.from_env() until first use so bootstrap can succeed without
        # a mounted docker.sock (readyz stays up).
        if client is None:
            import docker  # type: ignore[import-untyped]

            self._docker_mod = docker
            self._client: Any | None = None
        else:
            self._docker_mod = None
            self._client = client
        self._hydrator = hydrator

    def _ensure_client(self) -> Any:
        if self._client is None:
            assert self._docker_mod is not None
            self._client = self._docker_mod.from_env()
        return self._client

    async def preflight(self, *, image: str | None = None) -> None:
        """The daemon answers and ``image`` resolves (locally, else its registry manifest)."""
        if not image:
            raise ValueError("local_docker requires image digest")
        client = self._ensure_client()

        def _check() -> None:
            client.ping()
            try:
                client.images.get(image)
            except self._docker_errors().ImageNotFound:
                client.images.get_registry_data(image)  # a manifest lookup, no layers pulled

        await asyncio.to_thread(_check)

    def _docker_errors(self) -> Any:
        import docker.errors  # type: ignore[import-untyped]

        return docker.errors

    async def create(
        self,
        *,
        image: str | None = None,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> Sandbox:
        if not image:
            raise ValueError("local_docker requires image digest")
        client = self._ensure_client()
        loop = asyncio.get_running_loop()
        lbl = {
            LABEL_MANAGED: "1",
            **(labels or {}),
        }
        cleaned = sanitize_sandbox_env(env)

        def _run() -> Any:
            return client.containers.run(
                image,
                command=["sleep", "infinity"],
                detach=True,
                init=True,  # mandatory — reap zombies
                working_dir=workdir,
                environment=cleaned,
                labels=lbl,
                auto_remove=False,
                mem_limit=_DOCKER_MEM_LIMIT,
                nano_cpus=_DOCKER_NANO_CPUS,
                cap_drop=list(_DOCKER_CAP_DROP),
                network_mode=_DOCKER_NETWORK_MODE,
            )

        container = await loop.run_in_executor(None, _run)
        info = SandboxInfo(
            id=container.id,
            provider=self.name,
            image=image,
            workdir=workdir,
            supports_snapshot=True,
            labels=dict(lbl),
        )
        return LocalDockerSandbox(
            container,
            client=client,
            info=info,
            hydrator=self._hydrator,
        )

    async def resume(self, snapshot_id: str) -> Sandbox:
        """Start a new container from a committed image id."""
        client = self._ensure_client()
        loop = asyncio.get_running_loop()

        def _run() -> Any:
            images = getattr(client, "images", None)
            if images is not None:
                try:
                    images.get(snapshot_id)
                except Exception as exc:
                    raise SandboxGone(f"snapshot missing: {snapshot_id!r}") from exc
            try:
                return client.containers.run(
                    snapshot_id,
                    command=["sleep", "infinity"],
                    detach=True,
                    init=True,
                    labels={LABEL_MANAGED: "1"},
                    auto_remove=False,
                )
            except SandboxGone:
                raise
            except Exception as exc:
                raise SandboxGone(f"snapshot missing: {snapshot_id!r}") from exc

        container = await loop.run_in_executor(None, _run)
        info = SandboxInfo(
            id=container.id,
            provider=self.name,
            image=snapshot_id,
            supports_snapshot=True,
            labels={LABEL_MANAGED: "1"},
        )
        return LocalDockerSandbox(
            container, client=client, info=info, hydrator=self._hydrator
        )

    async def get(self, sandbox_id: str) -> Sandbox | None:
        client = self._ensure_client()
        loop = asyncio.get_running_loop()

        def _get() -> Any | None:
            try:
                return client.containers.get(sandbox_id)
            except Exception:
                logger.debug("no container with that id", exc_info=True)
                return None

        container = await loop.run_in_executor(None, _get)
        if container is None:
            return None
        labels = dict(getattr(container, "labels", None) or {})
        info = SandboxInfo(
            id=container.id,
            provider=self.name,
            image=None,
            supports_snapshot=True,
            labels=labels,
        )
        return LocalDockerSandbox(container, client=client, info=info, hydrator=self._hydrator)

    async def adopt(self, session_id: str) -> Sandbox | None:
        """The session's container, started again if it stopped (its files are kept)."""
        client = self._ensure_client()
        loop = asyncio.get_running_loop()

        def _find() -> Any | None:
            found = client.containers.list(
                all=True, filters={"label": [f"{LABEL_MANAGED}=1", f"{LABEL_SESSION}={session_id}"]}
            )
            if not found:
                return None
            container = found[0]  # the newest
            if getattr(container, "status", "running") != "running":
                container.start()
                container.reload()
            return container

        try:
            container = await loop.run_in_executor(None, _find)
        except Exception:  # no container to adopt: start afresh
            logger.warning("adopting the sandbox of session=%s failed", session_id, exc_info=True)
            return None
        if container is None:
            return None
        info = SandboxInfo(
            id=container.id,
            provider=self.name,
            supports_snapshot=True,
            labels=dict(getattr(container, "labels", None) or {}),
        )
        return LocalDockerSandbox(container, client=client, info=info, hydrator=self._hydrator)

    async def list_managed(self) -> list[Sandbox]:
        """Containers labelled by this supervisor (for orphan reaping)."""
        client = self._ensure_client()
        loop = asyncio.get_running_loop()

        def _list() -> list[Any]:
            return list(
                client.containers.list(
                    all=True, filters={"label": f"{LABEL_MANAGED}=1"}
                )
            )

        containers = await loop.run_in_executor(None, _list)
        out: list[Sandbox] = []
        for c in containers:
            labels = dict(getattr(c, "labels", None) or {})
            info = SandboxInfo(
                id=c.id,
                provider=self.name,
                supports_snapshot=True,
                labels=labels,
            )
            out.append(LocalDockerSandbox(c, client=client, info=info, hydrator=self._hydrator))
        return out
