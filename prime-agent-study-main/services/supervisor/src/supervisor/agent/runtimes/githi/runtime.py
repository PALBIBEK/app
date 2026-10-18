"""Githi AgentRuntime — the platform runtime protocol over ``AgentLoop``.

Matches ``EchoRuntime`` / ``HarnessRuntime`` so ``SessionManager`` can
``create_runtime("githi")`` without a special case. Durability lives outside
the runtime: the session journal and its Temporal workflow (``temporal.steps``
drives githi turns one model call and one tool call at a time).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from uuid import uuid4

from supervisor.agent.base import AgentState
from supervisor.agent.capabilities import Capabilities
from supervisor.agent.events import Envelope, RunEnd, TurnEnd
from supervisor.agent.runtimes.githi.loop import AgentLoop
from supervisor.agent.seams import RuntimeSeams
from supervisor.agent.stream import EventStream
from supervisor.sandbox.base import Sandbox

_log = logging.getLogger(__name__)


class GithiRuntime:
    """In-process githi agent: model via the gateway seam, tools via the tool seam."""

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
        del sandbox  # tools reach the workspace through the tool seam
        # The supervisor's session id; a fresh one only when run standalone.
        self._session_id = self._session_id or uuid4().hex
        self._session_dir = session_dir
        self._stream.session_id = self._session_id
        _log.info(
            "githi runtime started session_id=%s session_dir=%s model=%s",
            self._session_id,
            session_dir,
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
            loop = AgentLoop(
                seams=self.seams,
                model=self._model,
                session_dir=self._session_dir,
                should_abort=lambda: self._abort_requested,
                session_id=self._session_id,
            )
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
            is_busy=self._busy,
            is_compacting=False,
            pending_count=0,
            message_count=self._message_count,
            model=self._model,
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
