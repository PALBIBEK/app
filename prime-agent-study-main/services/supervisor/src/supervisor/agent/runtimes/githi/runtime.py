"""Githi AgentRuntime — the platform runtime protocol over ``AgentLoop``.

Matches ``EchoRuntime`` / ``HarnessRuntime`` so ``SessionManager`` can
``create_runtime("githi")`` without a special case.

Everything githi uses is pinned and handed in: seams (prompt, tools, model
gateway, memory, subagents), the model, and the sandbox, whose workspace every
tool call acts on. Turns with a model are driven by the session workflow
(``temporal.steps``) one model call and one tool call at a time, from
``loop()``; ``send`` only plays offline turns.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

from supervisor.agent.base import AgentPhase, AgentState
from supervisor.agent.capabilities import Capabilities
from supervisor.agent.events import Envelope, RunEnd, TurnEnd
from supervisor.agent.runtimes.githi.loop import AgentLoop
from supervisor.agent.seams import RuntimeSeams
from supervisor.agent.stream import EventStream
from supervisor.sandbox.base import Sandbox
from supervisor.tools import HostWorkspace, SandboxWorkspace, Workspace

_log = logging.getLogger(__name__)


class GithiRuntime:
    """In-process githi agent over its pinned seams, model and sandbox."""

    def __init__(
        self,
        *,
        seams: RuntimeSeams | None = None,
        model: str | None = None,
        provider: str | None = None,
        session_id: str = "",
    ) -> None:
        del provider  # model-gateway owns provider routing
        self.seams = seams
        self._model = model
        self._stream = EventStream()
        self._session_id: str | None = session_id or None
        self._session_dir: str | None = None
        self._workspace: Workspace | None = None
        self._on_mutate: Callable[[], None] | None = None
        self._history: list[dict[str, Any]] = []
        self._busy = False
        self._abort_requested = False
        self._stopped = False
        self._message_count = 0

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def model(self) -> str | None:
        """The pinned model (route) every model call names."""
        return self._model

    @property
    def workspace(self) -> Workspace | None:
        """Where tools act: the pinned sandbox, else the session's host directory."""
        return self._workspace

    def set_on_mutate(self, callback: Callable[[], None]) -> None:
        """Called whenever a tool changes the workspace (drives workspace sync)."""
        self._on_mutate = callback

    def loop(self, *, should_abort: Callable[[], bool] = lambda: False) -> AgentLoop:
        """Turn pieces bound to this session's seams and workspace."""
        return AgentLoop(
            seams=self.seams,
            session_id=self._session_id,
            workspace=self._workspace,
            on_mutate=self._on_mutate,
            should_abort=should_abort,
        )

    def seed_trace_id(self, trace_id: str) -> None:
        self._stream.seed_trace_id(trace_id)

    async def start(self, sandbox: Sandbox | None, session_dir: str | None) -> None:
        # The supervisor's session id; a fresh one only when run standalone.
        self._session_id = self._session_id or uuid4().hex
        self._session_dir = session_dir
        self._stream.session_id = self._session_id
        if sandbox is not None:
            self._workspace = SandboxWorkspace(sandbox)
        elif session_dir is not None:
            self._workspace = HostWorkspace(session_dir)
        _log.info(
            "githi runtime started session_id=%s workspace=%r model=%s",
            self._session_id,
            self._workspace,
            self._model,
        )

    async def send(self, message: str, *, command_id: str, mode: str = "prompt") -> None:
        del command_id
        if mode != "prompt":
            raise ValueError(f"unsupported mode={mode!r}")
        if self._stopped:
            raise RuntimeError("runtime stopped")
        if self._busy:
            raise RuntimeError("runtime busy")

        self._busy = True
        self._abort_requested = False
        try:
            loop = self.loop(should_abort=lambda: self._abort_requested)
            async for payload in loop.run_turn(message, self._history):
                if self._abort_requested and not isinstance(payload, (TurnEnd, RunEnd)):
                    self._stream.emit(TurnEnd(stop_reason="aborted"))
                    return
                self._stream.emit(payload)
                if isinstance(payload, RunEnd):
                    self._history = list(payload.messages)
                    self._message_count = len(self._history)
                await asyncio.sleep(0)
        finally:
            self._busy = False
            self._abort_requested = False

    async def abort(self) -> None:
        if self._busy:
            self._abort_requested = True

    async def get_state(self) -> AgentState:
        return AgentState(
            session_id=self._session_id or "",
            phase=_phase(busy=self._busy, stopped=self._stopped),
            message_count=self._message_count,
            model=self._model,
        )

    async def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)

    async def get_capabilities(self) -> Capabilities:
        return Capabilities(
            protocol_version="1",
            supports_abort=True,
            supports_fork=False,
            supports_approvals=True,
            supports_get_state=True,
            extras=frozenset({"githi", "offline_workspace_probe"}),
        )

    def events(self) -> AsyncIterator[Envelope]:
        return self._stream.events()

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._abort_requested = True
        self._stream.close()


def _phase(*, busy: bool, stopped: bool) -> AgentPhase:
    if stopped:
        return AgentPhase.STOPPED
    return AgentPhase.EXECUTING if busy else AgentPhase.READY
