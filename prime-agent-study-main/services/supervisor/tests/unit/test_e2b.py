"""E2BProvider tests against an injectable fake client."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from supervisor.sandbox.base import SandboxGone
from supervisor.sandbox.e2b import E2BProvider, _push_bounded


class _FakeProc:
    def __init__(self, *, hang: bool = False, large: bytes | None = None) -> None:
        self._hang = hang
        self._large = large
        self._killed = False
        self._stdin: list[bytes] = []

    def send_stdin(self, data: bytes) -> None:
        self._stdin.append(data)

    async def wait(self) -> int:
        if self._hang:
            while not self._killed:
                await asyncio.sleep(0.01)
            return -9
        return 0

    async def kill(self) -> None:
        self._killed = True


class _FakeHandle:
    def __init__(self, sandbox_id: str, *, client: FakeE2BClient | None = None) -> None:
        self.sandbox_id = sandbox_id
        self.files: dict[str, bytes] = {}
        self.paused = False
        self.dead = False
        self.commands = self
        self._client = client

    async def run(
        self,
        cmd: str,
        *,
        background: bool = False,
        cwd: str | None = None,
        envs: dict | None = None,
        on_stdout=None,  # type: ignore[no-untyped-def]
        on_stderr=None,  # type: ignore[no-untyped-def]
        timeout: float | None = None,
    ) -> Any:
        if self._client is not None:
            self._client.last_spawn_envs = dict(envs) if envs is not None else {}
        if background:
            proc = _FakeProc(hang="sleep-forever" in cmd)
            if on_stdout and "echo-out" in cmd:
                on_stdout(b"OUT")
            if on_stderr and "echo-out" in cmd:
                on_stderr(b"ERR")
            if on_stdout and "large-out" in cmd:
                on_stdout(b"x" * (1024 * 1024))
            return proc
        return type(
            "R",
            (),
            {
                "stdout": "ok",
                "stderr": "",
                "exit_code": 0,
            },
        )()

    async def pause(self) -> None:
        self.paused = True

    async def kill(self) -> None:
        self.dead = True

    async def is_alive(self) -> bool:
        return not self.dead

    async def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, path: str, data: Any) -> None:
        raw = data.encode() if isinstance(data, str) else bytes(data)
        self.files[path] = raw


class FakeE2BClient:
    def __init__(self) -> None:
        self._store: dict[str, _FakeHandle] = {}
        self.last_create_envs: dict | None = None
        self.last_spawn_envs: dict | None = None

    async def create(
        self, *, template: str, metadata: dict | None = None, envs: dict | None = None
    ) -> _FakeHandle:
        self.last_create_envs = dict(envs) if envs is not None else {}
        sid = uuid4().hex
        h = _FakeHandle(sid, client=self)
        self._store[sid] = h
        return h

    async def resume(self, snapshot_id: str) -> _FakeHandle:
        if snapshot_id not in self._store:
            raise Exception("not found")
        h = self._store[snapshot_id]
        h.paused = False
        return h

    async def connect(self, sandbox_id: str) -> _FakeHandle | None:
        return self._store.get(sandbox_id)


@pytest.mark.asyncio
async def test_create_and_alive() -> None:
    client = FakeE2BClient()
    provider = E2BProvider(client=client)
    sbx = await provider.create(image="tpl-digest", labels={"supervisor.session_id": "s1"})
    assert await sbx.is_alive()
    assert sbx.info.labels["supervisor.session_id"] == "s1"


@pytest.mark.asyncio
async def test_spawn_stdio_separate() -> None:
    client = FakeE2BClient()
    provider = E2BProvider(client=client)
    sbx = await provider.create(image="tpl")
    handle = await sbx.spawn(["echo-out"])
    out = await handle.stdout.read(64)
    err = await handle.stderr.read(64)
    assert out == b"OUT"
    assert err == b"ERR"


@pytest.mark.asyncio
async def test_write_read_roundtrip() -> None:
    client = FakeE2BClient()
    provider = E2BProvider(client=client)
    sbx = await provider.create(image="tpl")
    await sbx.write_file("/workspace/a.txt", b"data")
    assert await sbx.read_file("/workspace/a.txt") == b"data"


@pytest.mark.asyncio
async def test_destroy_idempotent() -> None:
    client = FakeE2BClient()
    provider = E2BProvider(client=client)
    sbx = await provider.create(image="tpl")
    await sbx.destroy()
    await sbx.destroy()


@pytest.mark.asyncio
async def test_pause_resume_preserves_file_five_cycles() -> None:
    client = FakeE2BClient()
    provider = E2BProvider(client=client)
    sbx = await provider.create(image="tpl")
    await sbx.write_file("/workspace/keep.txt", b"v0")
    for i in range(5):
        snap = await sbx.pause()
        sbx = await provider.resume(snap)
        data = await sbx.read_file("/workspace/keep.txt")
        assert data == b"v0"
        await sbx.write_file("/workspace/keep.txt", b"v0")


@pytest.mark.asyncio
async def test_resume_missing_raises_sandbox_gone() -> None:
    client = FakeE2BClient()
    provider = E2BProvider(client=client)
    with pytest.raises(SandboxGone):
        await provider.resume("missing-snap")


@pytest.mark.asyncio
async def test_callback_backpressure_no_deadlock() -> None:
    q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)
    # Fill beyond capacity — must not block the callback path
    for i in range(20):
        _push_bounded(q, f"chunk-{i}".encode())
    assert q.full() or q.qsize() <= 2


@pytest.mark.asyncio
async def test_large_stdout_through_bridge() -> None:
    client = FakeE2BClient()
    provider = E2BProvider(client=client)
    sbx = await provider.create(image="tpl")
    handle = await sbx.spawn(["large-out"])
    wait_task = asyncio.create_task(handle.wait())
    chunks: list[bytes] = []
    while True:
        part = await handle.stdout.read(65536)
        if not part:
            break
        chunks.append(part)
    await wait_task
    assert len(b"".join(chunks)) == 1024 * 1024


@pytest.mark.asyncio
async def test_get_reattaches() -> None:
    client = FakeE2BClient()
    provider = E2BProvider(client=client)
    sbx = await provider.create(image="tpl")
    found = await provider.get(sbx.info.id)
    assert found is not None
    assert found.info.id == sbx.info.id
