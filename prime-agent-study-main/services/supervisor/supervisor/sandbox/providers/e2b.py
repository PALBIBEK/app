"""E2B remote sandbox backend.

``ProcessHandle`` is a network stream bridged from E2B callbacks — not a pipe.
If callers above ``sandbox/`` need edits for this provider, ``spawn`` leaked
pipe semantics.

**Network / egress (D10):** E2B sessions run on E2B-managed cloud VMs. Egress is
enforced by the E2B platform (firewall / account policy), not by supervisor
NetworkPolicy. Use this provider when ``MULTI_TENANT_STRICT=true`` (shared deploy).
Supervisor applies ``sanitize_sandbox_env`` on create/spawn/exec — vendor keys and
``GATEWAY_TOKEN`` never enter the sandbox; optional ``vk_*`` inject only.
Operators must allow DNS + model-gateway egress in E2B policy. See
``contracts/sandbox_profile.md`` § Provider network / egress and ``deploy/README.md``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from typing import Any

from supervisor.sandbox.backup import (
    BackupArea,
    BackupResult,
    create_exec_backup,
)
from supervisor.sandbox.base import (
    BlobStagingUnsupported,
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
from supervisor.sandbox.layers import (
    LayerManifest,
    MaterializationResult,
    create_exec_layers,
)
from supervisor.sandbox.security.env import sanitize_sandbox_env
from supervisor.sandbox.staging import (
    BlobStagingConfig,
    CapabilityTokenMinter,
    StagedBlob,
    StageOutResult,
    create_exec_blob_staging,
)
from supervisor.seams.providers.workspace import normalize_workspace_path

# From the defining modules, not the package: supervisor.seams.services imports the
# sandbox package, so importing its __init__ from here was a cycle (it broke importing
# supervisor.tools first in a fresh interpreter).
from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator
from supervisor.seams.services.workspace_store import WorkspaceSyncReport, WorkspaceTreeIO

logger = logging.getLogger(__name__)

_QUEUE_MAX = 256


class _BoundedQueueReader:
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


class _CallbackWriter:
    def __init__(self, send_fn) -> None:  # type: ignore[no-untyped-def]
        self._send = send_fn

    def write(self, data: bytes) -> None:
        self._send(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)


class E2BProcessHandle(ProcessHandle):
    def __init__(
        self,
        *,
        stdout_r: ByteReader,
        stderr_r: ByteReader,
        stdin_w: ByteWriter,
        wait_fn,
        kill_fn,
    ) -> None:  # type: ignore[no-untyped-def]
        self._stdout = stdout_r
        self._stderr = stderr_r
        self._stdin = stdin_w
        self._wait_fn = wait_fn
        self._kill_fn = kill_fn
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
        self._rc = int(await self._wait_fn())
        return self._rc

    async def terminate(self) -> None:
        await self.kill()

    async def kill(self) -> None:
        await self._kill_fn()
        if self._rc is None:
            self._rc = -9


def _push_bounded(queue: asyncio.Queue[bytes | None], data: bytes) -> None:
    """Non-blocking callback push — drop oldest on overflow (backpressure)."""
    try:
        queue.put_nowait(data)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("e2b callback queue full; dropped chunk")


class E2BWorkspaceTreeIO:
    """WorkspaceTreeIO over an E2B sandbox ``/workspace`` (D10)."""

    def __init__(self, sandbox: E2BSandbox) -> None:
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
        files = getattr(self._sb._sbx, "files", None)
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
        files = getattr(self._sb._sbx, "files", None)
        if isinstance(files, dict):
            files.pop(abs_path, None)
            return
        await self._sb.exec(["rm", "-f", "--", abs_path], timeout=30)

    async def clear(self) -> None:
        files = getattr(self._sb._sbx, "files", None)
        prefix = self._root + "/"
        if isinstance(files, dict):
            for key in list(files):
                if key.startswith(prefix) or key == self._root:
                    files.pop(key, None)
            return
        await self._sb.exec(
            [
                "sh",
                "-c",
                f"rm -rf -- {self._root}/..?* {self._root}/.[!.]* {self._root}/* 2>/dev/null || true",
            ],
            timeout=60,
        )


class E2BSandbox(Sandbox):
    def __init__(
        self,
        handle: Any,
        *,
        info: SandboxInfo,
        hydrator: WorkspaceHydrator | None = None,
        staging_config: BlobStagingConfig | None = None,
        token_minter: CapabilityTokenMinter | None = None,
        actor_id: str | None = None,
        scope_id: str | None = None,
    ) -> None:
        self._sbx = handle
        self._info = info
        self._destroyed = False
        self._hydrator = hydrator
        self._staging_config = staging_config
        self._token_minter = token_minter
        self._actor_id = actor_id or ""
        self._scope_id = scope_id or ""

    @property
    def info(self) -> SandboxInfo:
        return self._info

    def as_tree_io(self) -> WorkspaceTreeIO:
        return E2BWorkspaceTreeIO(self)

    async def hydrate_workspace(self, scope_id: str) -> WorkspaceSyncReport:
        if self._hydrator is None:
            raise WorkspaceSyncUnsupported(
                "E2BSandbox has no WorkspaceHydrator; wire via provider"
            )
        return await self._hydrator.hydrate(scope_id, self.as_tree_io())

    async def sync_workspace(self, scope_id: str) -> WorkspaceSyncReport:
        if self._hydrator is None:
            raise WorkspaceSyncUnsupported(
                "E2BSandbox has no WorkspaceHydrator; wire via provider"
            )
        return await self._hydrator.sync(scope_id, self.as_tree_io())

    async def spawn(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessHandle:
        cleaned = sanitize_sandbox_env(env)
        stdout_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_QUEUE_MAX)
        stderr_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_QUEUE_MAX)
        exit_code = {"v": 0}

        def on_stdout(data: str | bytes) -> None:
            raw = data.encode() if isinstance(data, str) else data
            _push_bounded(stdout_q, raw)

        def on_stderr(data: str | bytes) -> None:
            raw = data.encode() if isinstance(data, str) else data
            _push_bounded(stderr_q, raw)

        run = getattr(self._sbx, "spawn_command", None)
        if run is not None:
            proc = await run(
                cmd,
                cwd=cwd or self._info.workdir,
                env=cleaned,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
            )
        else:
            proc = await self._sbx.commands.run(
                " ".join(cmd),
                background=True,
                cwd=cwd or self._info.workdir,
                envs=cleaned,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
            )

        def _stdin_send(data: bytes) -> None:
            send = getattr(proc, "send_stdin", None) or getattr(proc, "write", None)
            if send is not None:
                send(data)

        async def _wait() -> int:
            wait = getattr(proc, "wait", None)
            if wait is not None:
                rc = await wait()
                exit_code["v"] = int(
                    rc if not isinstance(rc, dict) else rc.get("exit_code", 0)
                )
            await stdout_q.put(None)
            await stderr_q.put(None)
            return exit_code["v"]

        async def _kill() -> None:
            kill = getattr(proc, "kill", None)
            if kill is not None:
                await kill()
            await stdout_q.put(None)
            await stderr_q.put(None)

        return E2BProcessHandle(
            stdout_r=_BoundedQueueReader(stdout_q),
            stderr_r=_BoundedQueueReader(stderr_q),
            stdin_w=_CallbackWriter(_stdin_send),
            wait_fn=_wait,
            kill_fn=_kill,
        )

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        cleaned = sanitize_sandbox_env(env)
        run = getattr(self._sbx, "exec_command", None)
        if run is not None:
            result = await run(cmd, cwd=cwd, env=cleaned, timeout=timeout)
            return ExecResult(
                exit_code=int(result.get("exit_code", 0)),
                stdout=result.get("stdout", b""),
                stderr=result.get("stderr", b""),
            )
        result = await self._sbx.commands.run(
            " ".join(cmd),
            cwd=cwd or self._info.workdir,
            envs=cleaned,
            timeout=timeout,
        )
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        code = int(getattr(result, "exit_code", 0) or 0)
        return ExecResult(
            exit_code=code,
            stdout=stdout.encode() if isinstance(stdout, str) else bytes(stdout),
            stderr=stderr.encode() if isinstance(stderr, str) else bytes(stderr),
        )

    async def read_file(self, path: str) -> bytes:
        read = getattr(self._sbx, "read_file", None)
        if read is None:
            files = getattr(self._sbx, "files", None)
            read = getattr(files, "read", None) if files else None
        if read is None:
            raise RuntimeError("e2b handle has no read_file")
        data = await read(path)
        return data.encode() if isinstance(data, str) else bytes(data)

    async def write_file(self, path: str, data: bytes) -> None:
        write = getattr(self._sbx, "write_file", None)
        if write is None:
            files = getattr(self._sbx, "files", None)
            write = getattr(files, "write", None) if files else None
        if write is None:
            raise RuntimeError("e2b handle has no write_file")
        payload: Any = data.decode() if isinstance(data, (bytes, bytearray)) else data
        await write(path, payload)

    async def exists(self, path: str) -> bool:
        try:
            await self.read_file(path)
            return True
        except Exception:
            logger.debug("e2b exists check failed", exc_info=True)
            return False

    async def pause(self) -> str:
        pause = getattr(self._sbx, "pause", None)
        if pause is None:
            raise RuntimeError("e2b handle cannot pause")
        await pause()
        return str(getattr(self._sbx, "sandbox_id", None) or self._info.id)

    async def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        kill = getattr(self._sbx, "kill", None) or getattr(self._sbx, "destroy", None)
        if kill is not None:
            try:
                await kill()
            except Exception:
                logger.exception("e2b destroy failed")

    async def is_alive(self) -> bool:
        alive = getattr(self._sbx, "is_alive", None)
        if alive is not None:
            return bool(await alive())
        return not self._destroyed

    # -------------------------------------------------------------------------
    # Blob Staging
    # -------------------------------------------------------------------------

    def _exec_fn(self, cmd: list[str], timeout: float) -> Any:
        """Create exec function for exec-based operations."""
        return self.exec(cmd, timeout=timeout)

    async def stage_in(
        self,
        blob_id: str,
        dest_path: str,
        *,
        timeout: float | None = None,
    ) -> StagedBlob:
        """Download blob via curl with atomic swap."""
        if self._staging_config is None or self._token_minter is None:
            raise BlobStagingUnsupported(self._info.provider)

        staging = create_exec_blob_staging(
            exec_fn=self._exec_fn,
            config=self._staging_config,
            token_minter=self._token_minter,
            actor_id=self._actor_id,
            scope_id=self._scope_id,
        )
        return await staging.stage_in(blob_id, dest_path, timeout=timeout)

    async def stage_out(
        self,
        source_path: str,
        *,
        timeout: float | None = None,
    ) -> StageOutResult:
        """Upload file via curl with SHA256 verification."""
        if self._staging_config is None or self._token_minter is None:
            raise BlobStagingUnsupported(self._info.provider)

        staging = create_exec_blob_staging(
            exec_fn=self._exec_fn,
            config=self._staging_config,
            token_minter=self._token_minter,
            actor_id=self._actor_id,
            scope_id=self._scope_id,
        )
        return await staging.stage_out(source_path, timeout=timeout)

    # -------------------------------------------------------------------------
    # Computer Backup
    # -------------------------------------------------------------------------

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
    # Read-Only Layers
    # -------------------------------------------------------------------------

    async def _read_file_bytes(self, path: str) -> bytes | None:
        """Read file for layer operations."""
        try:
            return await self.read_file(path)
        except Exception:
            logger.debug("e2b layer read failed", exc_info=True)
            return None

    async def _write_file_bytes(self, path: str, data: bytes) -> None:
        """Write file for layer operations."""
        await self.write_file(path, data)

    async def materialize_ro_layers(
        self,
        manifest: LayerManifest,
        *,
        force: bool = False,
        timeout: float | None = None,
    ) -> MaterializationResult:
        """Materialize layers with fingerprint-based caching."""
        layers = create_exec_layers(
            exec_fn=self._exec_fn,
            read_fn=self._read_file_bytes,
            write_fn=self._write_file_bytes,
        )
        return await layers.materialize_ro_layers(manifest, force=force, timeout=timeout)


    # -------------------------------------------------------------------------
    # Computer Status
    # -------------------------------------------------------------------------




class E2BProvider(SandboxProvider):
    name = "e2b"

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        hydrator: WorkspaceHydrator | None = None,
        staging_config: BlobStagingConfig | None = None,
        token_minter: CapabilityTokenMinter | None = None,
    ) -> None:
        if client is None and importlib.util.find_spec("e2b") is None:
            # Fail at construction (boot), not at the first user's session.
            raise ModuleNotFoundError("the e2b provider needs the 'e2b' package (pip install e2b)")
        self._client = client
        self._api_key = api_key
        self._hydrator = hydrator
        self._staging_config = staging_config
        self._token_minter = token_minter

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from e2b import AsyncSandbox  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "e2b package not installed; pip install 'yal-supervisor[e2b]'"
            ) from exc
        return AsyncSandbox

    async def create(
        self,
        *,
        image: str | None = None,
        workdir: str = "/workspace",
        env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        actor_id: str | None = None,
        scope_id: str | None = None,
    ) -> Sandbox:
        if not image:
            raise ValueError("e2b requires template digest/id")
        sdk = self._sdk()
        create = getattr(sdk, "create", None)
        if create is None:
            raise RuntimeError("invalid e2b client")
        cleaned = sanitize_sandbox_env(env)
        handle = await create(
            template=image,
            metadata=labels or {},
            envs=cleaned,
        )
        sid = str(getattr(handle, "sandbox_id", None) or getattr(handle, "id", ""))
        info = SandboxInfo(
            id=sid,
            provider=self.name,
            image=image,
            workdir=workdir,
            supports_snapshot=True,
            labels=dict(labels or {}),
        )
        return E2BSandbox(
            handle,
            info=info,
            hydrator=self._hydrator,
            staging_config=self._staging_config,
            token_minter=self._token_minter,
            actor_id=actor_id,
            scope_id=scope_id or sid,
        )

    async def resume(self, snapshot_id: str) -> Sandbox:
        sdk = self._sdk()
        resume = getattr(sdk, "resume", None) or getattr(sdk, "connect", None)
        if resume is None:
            raise RuntimeError("e2b client cannot resume")
        try:
            handle = await resume(snapshot_id)
        except Exception as exc:
            raise SandboxGone(f"snapshot missing: {snapshot_id!r}") from exc
        if handle is None:
            raise SandboxGone(f"snapshot missing: {snapshot_id!r}")
        sid = str(getattr(handle, "sandbox_id", None) or snapshot_id)
        info = SandboxInfo(
            id=sid,
            provider=self.name,
            image=None,
            supports_snapshot=True,
        )
        return E2BSandbox(
            handle,
            info=info,
            hydrator=self._hydrator,
            staging_config=self._staging_config,
            token_minter=self._token_minter,
            scope_id=sid,
        )

    async def get(self, sandbox_id: str) -> Sandbox | None:
        sdk = self._sdk()
        connect = getattr(sdk, "connect", None) or getattr(sdk, "get", None)
        if connect is None:
            return None
        try:
            handle = await connect(sandbox_id)
        except Exception:
            logger.warning("reconnecting to an e2b sandbox failed", exc_info=True)
            return None
        if handle is None:
            return None
        info = SandboxInfo(id=sandbox_id, provider=self.name, supports_snapshot=True)
        return E2BSandbox(
            handle,
            info=info,
            hydrator=self._hydrator,
            staging_config=self._staging_config,
            token_minter=self._token_minter,
            scope_id=sandbox_id,
        )
