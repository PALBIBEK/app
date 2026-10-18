"""Harness AgentRuntime — subprocess JSONL RPC behind AgentRuntime."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from supervisor.agent.base import AgentState
from supervisor.agent.capabilities import Capabilities
from supervisor.agent.events import Envelope, RunEnd
from supervisor.agent.runtimes.harness import commands
from supervisor.agent.runtimes.harness.framing import read_frames
from supervisor.harness import build_spawn_plan
from supervisor.agent.runtimes.harness.state import from_get_state
from supervisor.agent.runtimes.harness.translate import translate
from supervisor.agent.seams import RuntimeSeams
from supervisor.agent.stream import EventStream
from supervisor.sandbox.base import ProcessHandle, Sandbox

_log = logging.getLogger(__name__)


class HarnessRuntime:
    def __init__(
        self,
        *,
        provider: str = "openrouter",
        model: str = "anthropic/claude-sonnet-4",
        rpc_timeout: float = 60.0,
        seams: RuntimeSeams | None = None,
        session_id: str = "",
    ) -> None:
        self.seams = seams
        self._provider = provider
        self._model = model
        self._rpc_timeout = rpc_timeout
        self._proc: ProcessHandle | None = None
        self._stdin_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._stream = EventStream()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._session_id: str | None = session_id or None
        self._history: list[dict] = []
        self._stopped = False

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def seed_trace_id(self, trace_id: str) -> None:
        """Seed W3C trace id before the subprocess emits RunStart."""
        self._stream.seed_trace_id(trace_id)

    async def get_capabilities(self) -> Capabilities:
        return Capabilities(
            protocol_version="1",
            supports_abort=True,
            supports_fork=False,
            supports_approvals=True,
            supports_get_state=True,
        )

    async def start(self, sandbox: Sandbox | None, session_dir: str | None) -> None:
        if sandbox is None:
            raise ValueError("HarnessRuntime requires a sandbox")
        plan = build_spawn_plan(
            session_dir=session_dir,
            model=self._model,
            provider=self._provider,
            seams=self.seams,
        )
        # Bind files are materialized on the supervisor host (session_dir). The
        # agent process runs inside the sandbox — use the sandbox workdir so
        # docker exec does not chdir to a host-only path (D14 local_docker).
        spawn_cwd = session_dir
        if getattr(sandbox.info, "provider", None) == "local_docker":
            spawn_cwd = sandbox.info.workdir or "/workspace"
        self._proc = await sandbox.spawn(plan.argv, cwd=spawn_cwd, env=plan.env or None)
        self._reader_task = asyncio.create_task(self._read_loop(), name="harness-reader")
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name="harness-stderr")
        await self._rpc(commands.set_auto_retry(False, id=uuid4().hex))
        state = await self.get_state()
        # Events carry the supervisor's id; Pi's own id only when run standalone.
        self._session_id = self._session_id or state.session_id
        self._stream.session_id = self._session_id

    async def send(self, message: str, *, command_id: str, mode: str = "prompt") -> None:
        if mode != "prompt":
            raise ValueError(f"unsupported mode={mode!r}")
        await self._rpc(commands.prompt(message, id=command_id))

    async def abort(self) -> None:
        await self._rpc(commands.abort(id=uuid4().hex))

    async def respond_ui(
        self,
        *,
        harness_request_id: str,
        choice: str,
    ) -> None:
        confirmed = choice.lower() in ("yes", "true", "allow", "y")
        await self._write(
            commands.extension_ui_response(
                id=harness_request_id,
                confirmed=confirmed,
            )
        )

    async def get_state(self) -> AgentState:
        cid = uuid4().hex
        resp = await self._rpc(commands.get_state(id=cid))
        data = resp.get("data") or {}
        return from_get_state(data)

    async def get_history(self) -> list[dict]:
        return list(self._history)

    def events(self) -> AsyncIterator[Envelope]:
        return self._stream.events()

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._proc is not None:
            await self._proc.kill()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._fail_pending(RuntimeError("runtime stopped"))
        self._stream.close()

    async def _rpc(self, cmd: dict[str, Any]) -> dict[str, Any]:
        cid = str(cmd["id"])
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[cid] = fut
        try:
            await self._write(cmd)
            return await asyncio.wait_for(fut, timeout=self._rpc_timeout)
        finally:
            self._pending.pop(cid, None)

    async def _write(self, cmd: dict[str, Any]) -> None:
        if self._proc is None:
            raise RuntimeError("runtime not started")
        data = (json.dumps(cmd) + "\n").encode()
        async with self._stdin_lock:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc is not None
        try:
            async for raw in read_frames(self._proc.stdout):
                if raw.get("type") == "response":
                    cid = raw.get("id")
                    fut = self._pending.get(str(cid)) if cid is not None else None
                    if fut is not None and not fut.done():
                        fut.set_result(raw)
                    continue
                payload = translate(raw)
                if payload is None:
                    continue
                if isinstance(payload, RunEnd):
                    self._history = list(payload.messages)
                self._stream.emit(payload)
        finally:
            self._fail_pending(RuntimeError("stdout closed"))
            self._stream.close()

    async def _drain_stderr(self) -> None:
        assert self._proc is not None
        while True:
            chunk = await self._proc.stderr.read(65536)
            if not chunk:
                return
            _log.debug("harness stderr: %s", chunk[:500])

    def _fail_pending(self, exc: BaseException) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
