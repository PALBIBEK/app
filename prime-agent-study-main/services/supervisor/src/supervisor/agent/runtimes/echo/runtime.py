"""Echo AgentRuntime — in-memory canned sequence. Proves the interface is not Pi-shaped."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from supervisor.agent.base import AgentState
from supervisor.agent.capabilities import Capabilities
from supervisor.agent.events import (
    Envelope,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.agent.seams import RuntimeSeams
from supervisor.agent.stream import EventStream
from supervisor.sandbox.base import Sandbox


class EchoRuntime:
    """No subprocess, no model, no sandbox, no cost."""

    # Test hooks, class-level so a runtime built inside a worker picks them up.
    # Zero usage by default so budgets never trip.
    forced_usage: Usage | None = None
    # Repeat the echo tool call (fresh ids, same args) to trip loop guards.
    repeat_tool_times: int = 1
    # Hold each tool open this long (abortable), to exercise mid-turn failures.
    tool_delay_s: float = 0.0

    def __init__(self, *, seams: RuntimeSeams | None = None, session_id: str = "") -> None:
        self.seams = seams
        self._stream = EventStream()
        self._session_id: str | None = session_id or None
        self._history: list[dict] = []
        self._busy = False
        self._abort_requested = False
        self._stopped = False
        self._message_count = 0

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def seed_trace_id(self, trace_id: str) -> None:
        self._stream.seed_trace_id(trace_id)

    async def start(self, sandbox: Sandbox | None, session_dir: str | None) -> None:
        # Ignores sandbox and session_dir entirely — sandbox-optional is real.
        del sandbox, session_dir
        # The supervisor's session id; a fresh one only when run standalone.
        self._session_id = self._session_id or uuid4().hex
        self._stream.session_id = self._session_id

    async def send(self, message: str, *, command_id: str, mode: str = "prompt") -> None:
        del command_id
        if mode != "prompt":
            raise ValueError(f"unsupported mode={mode!r}")
        if self._stopped:
            raise RuntimeError("runtime stopped")
        self._busy = True
        self._abort_requested = False
        tool_call_id = uuid4().hex
        user_msg = {"role": "user", "content": [{"type": "text", "text": message}]}
        assistant_msg = {"role": "assistant", "content": [{"type": "text", "text": message}]}
        usage = self.forced_usage or Usage(0, 0, 0, 0, 0.0)
        tool_events: list = []
        for i in range(max(1, self.repeat_tool_times)):
            # Distinct tool_call_id each time unless repeating for loop-guard tests
            # that want identical (tool_name, args) with fresh ids.
            cid = tool_call_id if self.repeat_tool_times == 1 else uuid4().hex
            tool_events.append(
                ToolStart(tool_call_id=cid, tool_name="echo", args={"text": message})
            )
            tool_events.append(
                ToolEnd(
                    tool_call_id=cid,
                    tool_name="echo",
                    result={"text": message},
                    is_error=False,
                )
            )
        sequence = [
            RunStart(),
            TurnStart(),
            *tool_events,
            MessageEnd(
                role="assistant",
                content=[{"type": "text", "text": message}],
                usage=usage,
            ),
            TurnEnd(stop_reason="stop"),
            RunEnd(messages=[user_msg, assistant_msg]),
        ]
        try:
            for payload in sequence:
                if self._abort_requested:
                    self._stream.emit(TurnEnd(stop_reason="aborted"))
                    return
                self._stream.emit(payload)
                if isinstance(payload, RunEnd):
                    self._history = list(payload.messages)
                    self._message_count = len(self._history)
                if isinstance(payload, ToolStart) and self.tool_delay_s > 0:
                    await self._hold(self.tool_delay_s)
                # Yield so abort() can interleave mid-sequence.
                await asyncio.sleep(0)
        finally:
            self._busy = False
            self._abort_requested = False

    async def _hold(self, seconds: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while not self._abort_requested and loop.time() < deadline:
            await asyncio.sleep(0.05)

    async def abort(self) -> None:
        if self._busy:
            self._abort_requested = True

    async def get_state(self) -> AgentState:
        return AgentState(
            session_id=self._session_id or "",
            is_busy=self._busy,
            is_compacting=False,
            pending_count=0,
            message_count=self._message_count,
            model=None,
            context_window=None,
        )

    async def get_history(self) -> list[dict]:
        return list(self._history)

    async def get_capabilities(self) -> Capabilities:
        return Capabilities(
            protocol_version="1",
            supports_abort=True,
            supports_fork=False,
            supports_approvals=True,
            supports_get_state=True,
        )

    def events(self) -> AsyncIterator[Envelope]:
        return self._stream.events()

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stream.close()
