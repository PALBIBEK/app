"""Fake sandbox that synthesizes RPC responses and replays harness-events.jsonl."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from supervisor.sandbox.base import (
    ByteReader,
    ByteWriter,
    ExecResult,
    ProcessHandle,
    Sandbox,
    SandboxInfo,
    SnapshotUnsupported,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "harness-events.jsonl"
STATE = Path(__file__).resolve().parents[1] / "fixtures" / "harness-get-state.json"

class _Pipe(ByteReader, ByteWriter):
    def __init__(self) -> None:
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._buf = bytearray()

    def write(self, data: bytes) -> None:
        self._buf.extend(data)

    async def drain(self) -> None:
        return None

    async def read(self, n: int) -> bytes:
        while True:
            if self._buf:
                out = bytes(self._buf[:n]) if n > 0 else bytes(self._buf)
                del self._buf[: len(out)]
                return out
            item = await self._q.get()
            if item is None:
                return b""
            self._buf.extend(item)

    def push(self, data: bytes) -> None:
        self._q.put_nowait(data)

    def close(self) -> None:
        self._q.put_nowait(None)


class ReplayHandle(ProcessHandle):
    def __init__(self) -> None:
        self._stdin_buf = bytearray()
        self._stdout = _Pipe()
        self._stderr = _Pipe()
        self._killed = False
        self._terminated = False
        self._returncode: int | None = None
        self._pid = 4242
        self.state_data = json.loads(STATE.read_text())
        self.fixture_lines = [
            line for line in FIXTURE.read_text().splitlines() if line.strip()
        ]
        self.agent_event_lines = [
            line
            for line in self.fixture_lines
            if json.loads(line).get("type")
            not in ("response",)  # responses are synthesized from commands
        ]

    @property
    def stdin(self) -> ByteWriter:
        return self

    @property
    def stdout(self) -> ByteReader:
        return self._stdout

    @property
    def stderr(self) -> ByteReader:
        return self._stderr

    @property
    def pid(self) -> int | None:
        return self._pid

    def returncode(self) -> int | None:
        return self._returncode

    def write(self, data: bytes) -> None:
        self._stdin_buf.extend(data)
        while True:
            nl = self._stdin_buf.find(b"\n")
            if nl < 0:
                break
            raw = bytes(self._stdin_buf[:nl])
            del self._stdin_buf[: nl + 1]
            if not raw:
                continue
            cmd = json.loads(raw.decode())
            self._handle_command(cmd)

    async def drain(self) -> None:
        return None

    def _emit(self, obj: dict[str, Any]) -> None:
        self._stdout.push(json.dumps(obj).encode() + b"\n")

    def _handle_command(self, cmd: dict[str, Any]) -> None:
        cid = cmd["id"]
        t = cmd["type"]
        if t == "set_auto_retry":
            self._emit({"id": cid, "type": "response", "command": t, "success": True})
        elif t == "get_state":
            self._emit(
                {
                    "id": cid,
                    "type": "response",
                    "command": t,
                    "success": True,
                    "data": self.state_data,
                }
            )
        elif t == "prompt":
            # Match fixture shape: response first, then agent events.
            self._emit({"id": cid, "type": "response", "command": "prompt", "success": True})
            for line in self.agent_event_lines:
                self._emit(json.loads(line))
        elif t == "abort":
            self._emit({"id": cid, "type": "response", "command": "abort", "success": True})
        else:
            self._emit(
                {
                    "id": cid,
                    "type": "response",
                    "command": t,
                    "success": False,
                    "error": f"unknown {t}",
                }
            )

    async def wait(self) -> int:
        while self._returncode is None:
            await asyncio.sleep(0.01)
        return self._returncode

    async def terminate(self) -> None:
        self._terminated = True
        self._returncode = -15
        self._stdout.close()
        self._stderr.close()

    async def kill(self) -> None:
        self._killed = True
        self._returncode = -9
        self._stdout.close()
        self._stderr.close()


class ReplaySandbox(Sandbox):
    def __init__(self) -> None:
        self.handle = ReplayHandle()
        self._info = SandboxInfo(id="replay", provider="fake")

    @property
    def info(self) -> SandboxInfo:
        return self._info

    async def spawn(self, cmd, *, cwd=None, env=None) -> ProcessHandle:
        return self.handle

    async def exec(self, cmd, *, cwd=None, env=None, timeout=None) -> ExecResult:
        raise NotImplementedError

    async def read_file(self, path: str) -> bytes:
        raise NotImplementedError

    async def write_file(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    async def exists(self, path: str) -> bool:
        return False

    async def pause(self) -> str:
        raise SnapshotUnsupported("fake")

    async def destroy(self) -> None:
        return None

    async def is_alive(self) -> bool:
        return self.handle.returncode() is None


