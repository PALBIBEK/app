"""LocalDockerProvider tests against an injectable fake Docker client."""

from __future__ import annotations

import asyncio
import io
import tarfile
from typing import Any
from uuid import uuid4

import pytest

from supervisor.sandbox.base import SandboxGone
from supervisor.sandbox.local_docker import (
    LABEL_MANAGED,
    LABEL_SESSION,
    LocalDockerProvider,
)


def _mux_frame(stream: int, payload: bytes) -> bytes:
    return bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


class _RawSock:
    def __init__(self, frames: bytes) -> None:
        self._buf = frames
        self._pos = 0
        self.sent: list[bytes] = []
        self.closed = False

    def recv(self, n: int) -> bytes:
        if self._pos >= len(self._buf):
            return b""
        out = self._buf[self._pos : self._pos + n]
        self._pos += len(out)
        return out

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


class _SockWrapper:
    def __init__(self, raw: _RawSock) -> None:
        self._sock = raw


class _FakeImage:
    def __init__(self, image_id: str) -> None:
        self.id = image_id


class _FakeContainer:
    def __init__(
        self,
        *,
        cid: str,
        labels: dict[str, str],
        files: dict[str, bytes] | None = None,
        init: bool = True,
    ) -> None:
        self.id = cid
        self.labels = labels
        self.status = "running"
        self.files = files if files is not None else {}
        self.init = init
        self.killed = False
        self.removed = False
        self._client: Any = None

    def reload(self) -> None:
        if self.removed:
            raise RuntimeError("gone")

    def kill(self) -> None:
        self.killed = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True
        self.status = "removed"

    def commit(self, repository: str = "", tag: str = "") -> _FakeImage:
        snap = f"sha256:snap-{tag or self.id[:8]}"
        assert self._client is not None
        self._client.images._store[snap] = _FakeImage(snap)
        # Snapshot filesystem into image store for resume
        self._client.images._files[snap] = dict(self.files)
        return _FakeImage(snap)

    def get_archive(self, path: str) -> tuple[list[bytes], dict]:
        data = self.files.get(path, b"")
        name = path.rsplit("/", 1)[-1]
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return [buf.getvalue()], {}

    def put_archive(self, parent: str, data: bytes) -> bool:
        buf = io.BytesIO(data)
        with tarfile.open(fileobj=buf, mode="r:") as tar:
            for member in tar.getmembers():
                f = tar.extractfile(member)
                if f is None:
                    continue
                path = f"{parent.rstrip('/')}/{member.name}"
                self.files[path] = f.read()
        return True


class _FakeAPI:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client
        self._execs: dict[str, dict[str, Any]] = {}

    def exec_create(self, container_id: str, cmd: list[str], **kwargs: Any) -> dict:
        eid = uuid4().hex
        self._execs[eid] = {
            "container_id": container_id,
            "cmd": cmd,
            "Running": True,
            "ExitCode": None,
            "killed": False,
            "stdout": b"",
            "stderr": b"",
        }
        # Scripted behaviours by command
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "echo-stdout" in joined:
            self._execs[eid]["stdout"] = b"hello-out"
            self._execs[eid]["stderr"] = b""
        elif "echo-both" in joined:
            self._execs[eid]["stdout"] = b"OUT"
            self._execs[eid]["stderr"] = b"ERR"
        elif "sleep-forever" in joined:
            self._execs[eid]["hang"] = True
        elif "test -e" in joined or "echo yes" in joined:
            self._execs[eid]["stdout"] = b"yes\n"
        else:
            self._execs[eid]["stdout"] = b"ok"
        return {"Id": eid}

    def exec_start(self, exec_id: str, **kwargs: Any) -> Any:
        meta = self._execs[exec_id]
        hang = meta.get("hang", False)
        frames = (
            _mux_frame(1, meta["stdout"]) + _mux_frame(2, meta["stderr"])
            if not hang
            else b""
        )
        raw = _RawSock(frames)
        meta["sock"] = raw
        if not hang:
            meta["Running"] = False
            meta["ExitCode"] = 0
        if kwargs.get("socket"):
            return _SockWrapper(raw)
        return (meta["stdout"], meta["stderr"])

    def exec_inspect(self, exec_id: str) -> dict:
        meta = self._execs[exec_id]
        if meta.get("killed"):
            return {"Running": False, "ExitCode": -9}
        if meta.get("hang"):
            return {"Running": True, "ExitCode": None}
        return {
            "Running": bool(meta.get("Running", False)),
            "ExitCode": meta.get("ExitCode", 0),
        }

    def mark_killed(self, exec_id: str) -> None:
        meta = self._execs[exec_id]
        meta["killed"] = True
        meta["Running"] = False
        meta["ExitCode"] = -9


class _FakeImages:
    def __init__(self) -> None:
        self._store: dict[str, _FakeImage] = {}
        self._files: dict[str, dict[str, bytes]] = {}

    def get(self, image_id: str) -> _FakeImage:
        if image_id not in self._store:
            raise Exception(f"No such image: {image_id}")
        return self._store[image_id]


class _FakeContainers:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client
        self._store: dict[str, _FakeContainer] = {}
        self.last_run_kwargs: dict[str, Any] = {}

    def run(self, image: str, **kwargs: Any) -> _FakeContainer:
        assert kwargs.get("init") is True, "--init is mandatory"
        assert kwargs.get("detach") is True
        self.last_run_kwargs = dict(kwargs)
        self.last_run_kwargs["image"] = image
        cid = uuid4().hex
        labels = dict(kwargs.get("labels") or {})
        files: dict[str, bytes] = {}
        if image in self._client.images._files:
            files = dict(self._client.images._files[image])
        c = _FakeContainer(cid=cid, labels=labels, files=files, init=True)
        c._client = self._client
        self._store[cid] = c
        return c

    def get(self, cid: str) -> _FakeContainer:
        if cid not in self._store or self._store[cid].removed:
            raise Exception("404")
        return self._store[cid]

    def list(self, all: bool = False, filters: dict | None = None) -> list[_FakeContainer]:
        out = [c for c in self._store.values() if not c.removed]
        if filters and "label" in filters:
            want = filters["label"]
            key, _, val = want.partition("=")
            out = [c for c in out if c.labels.get(key) == val]
        return out


class FakeDockerClient:
    def __init__(self) -> None:
        self.api = _FakeAPI(self)
        self.containers = _FakeContainers(self)
        self.images = _FakeImages()
        # Seed a base image so create works
        self.images._store["sha256:harness"] = _FakeImage("sha256:harness")


# Patch kill on DockerProcessHandle path: LocalDockerSandbox.kill cancels demux
# and sets rc; FakeAPI still shows Running until we sync — override kill to mark exec.


@pytest.fixture
def fake_docker() -> FakeDockerClient:
    return FakeDockerClient()


@pytest.mark.asyncio
async def test_create_returns_live_sandbox(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sbx = await provider.create(
        image="sha256:harness",
        labels={LABEL_SESSION: "sess-1", "supervisor.harness_version": "coding-v1"},
    )
    assert await sbx.is_alive()
    assert sbx.info.labels[LABEL_MANAGED] == "1"
    assert sbx.info.labels[LABEL_SESSION] == "sess-1"
    c = fake_docker.containers.get(sbx.info.id)
    assert c.init is True


@pytest.mark.asyncio
async def test_spawn_demux_stdout_stderr_separate(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sbx = await provider.create(image="sha256:harness")
    handle = await sbx.spawn(["echo-both"])
    out = await handle.stdout.read(64)
    err = await handle.stderr.read(64)
    assert out == b"OUT"
    assert err == b"ERR"
    assert b"ERR" not in out
    assert b"OUT" not in err


@pytest.mark.asyncio
async def test_spawn_stdout_roundtrip(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sbx = await provider.create(image="sha256:harness")
    handle = await sbx.spawn(["echo-stdout"])
    data = await handle.stdout.read(64)
    assert data == b"hello-out"
    rc = await handle.wait()
    assert rc == 0


@pytest.mark.asyncio
async def test_write_read_file_roundtrip(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sbx = await provider.create(image="sha256:harness")
    await sbx.write_file("/workspace/note.txt", b"payload")
    assert await sbx.read_file("/workspace/note.txt") == b"payload"


@pytest.mark.asyncio
async def test_destroy_idempotent(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sbx = await provider.create(image="sha256:harness")
    await sbx.destroy()
    await sbx.destroy()


@pytest.mark.asyncio
async def test_get_reattaches(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sbx = await provider.create(
        image="sha256:harness", labels={LABEL_SESSION: "s-reattach"}
    )
    cid = sbx.info.id
    # Simulate new process with same client/daemon
    provider2 = LocalDockerProvider(client=fake_docker)
    found = await provider2.get(cid)
    assert found is not None
    assert found.info.id == cid
    assert found.info.labels[LABEL_SESSION] == "s-reattach"


@pytest.mark.asyncio
async def test_create_uses_init(fake_docker: FakeDockerClient) -> None:
    """containers.run must be called with init=True (asserted in fake)."""
    provider = LocalDockerProvider(client=fake_docker)
    await provider.create(image="sha256:harness")


@pytest.mark.asyncio
async def test_exec_timeout_kills_process(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sbx = await provider.create(image="sha256:harness")

    # Monkeypatch kill to mark the fake exec killed so wait can observe it
    from supervisor.sandbox import local_docker as ld

    original_kill = ld.DockerProcessHandle.kill

    async def kill_and_mark(self: Any) -> None:  # noqa: ANN401
        fake_docker.api.mark_killed(self._exec_id)
        await original_kill(self)

    ld.DockerProcessHandle.kill = kill_and_mark  # type: ignore[method-assign]
    try:
        with pytest.raises(TimeoutError):
            await sbx.exec(["sleep-forever"], timeout=0.05)
    finally:
        ld.DockerProcessHandle.kill = original_kill  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_pause_commit_and_resume(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sbx = await provider.create(image="sha256:harness")
    await sbx.write_file("/workspace/x.txt", b"kept")
    snap = await sbx.pause()
    await sbx.destroy()
    restored = await provider.resume(snap)
    assert await restored.read_file("/workspace/x.txt") == b"kept"


@pytest.mark.asyncio
async def test_resume_missing_raises_sandbox_gone(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    with pytest.raises(SandboxGone):
        await provider.resume("sha256:does-not-exist")
