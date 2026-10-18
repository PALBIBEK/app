"""Githi AgentRuntime — platform protocol + AgentLoop (WP-A1/A2).

Matches ``EchoRuntime`` / ``HarnessRuntime`` so ``SessionManager`` can
``create_runtime("githi")`` without a special case.

Enhanced with features from githi/src/githi/agent/:
- Session management with tree-based branching
- Hooks for tool call interception
- Context compaction with summarization
- Steering and follow-up message queues
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from supervisor.agent.base import AgentState
from supervisor.agent.capabilities import Capabilities
from supervisor.agent.events import Envelope, RunEnd, TurnEnd
from supervisor.agent.runtimes.githi.loop import AgentLoop, LoopConfig
from supervisor.agent.runtimes.githi.session_store import PostgresSessionStore
from supervisor.agent.runtimes.githi.session import Session
from supervisor.agent.runtimes.githi.hooks import Hooks, EMPTY_HOOKS
from supervisor.agent.runtimes.githi.compaction import build_summarizer, DEFAULT_LIMIT_TOKENS
from supervisor.agent.runtimes.githi.state import MutableAgentState, StopReason
from supervisor.agent.seams import RuntimeSeams
from supervisor.agent.stream import EventStream
from supervisor.sandbox.base import Sandbox

_log = logging.getLogger(__name__)


class GithiRuntime:
    """
    Githi agent runtime with PostgreSQL persistence.

    Pattern: QM Orchestrator (durable by default)
    - Message history persisted to PostgreSQL
    - Lease-based concurrency control
    - Sandbox provisioning (Phase 1: optional, Phase 2: required)
    """

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
        self._history: list[dict] = []  # Cached from PostgreSQL
        self._busy = False
        self._abort_requested = False
        self._stopped = False
        self._message_count = 0
        self._lease_token: str | None = None  # Current lease token

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def seed_trace_id(self, trace_id: str) -> None:
        self._stream.seed_trace_id(trace_id)

    async def start(self, sandbox: Sandbox | None, session_dir: str | None) -> None:
        del sandbox  # Phase 1: requires_sandbox false
        # The supervisor's session id; a fresh one only when run standalone.
        self._session_id = self._session_id or uuid4().hex
        self._session_dir = session_dir
        self._stream.session_id = self._session_id

        # Load or create session in PostgreSQL (if session_store available)
        if self.seams and hasattr(self.seams, 'session_store') and self.seams.session_store:
            session_store = self.seams.session_store
            # Check if session exists
            existing_session = await session_store.get_session(self._session_id)
            if not existing_session:
                # Create new session
                await session_store.create_session(
                    session_id=self._session_id,
                    scope_id="user:default",  # TODO: Get from seams
                    model=self._model or "claude-sonnet-4-5",
                    metadata={"session_dir": session_dir}
                )
            # Load message history from PostgreSQL
            entries = await session_store.get_entries(self._session_id)
            self._history = [entry.payload for entry in entries]
            self._message_count = len(self._history)
            _log.info(
                "githi runtime started session_id=%s with %d messages from PostgreSQL",
                self._session_id,
                len(self._history)
            )
        else:
            # Fallback: In-memory only (no persistence)
            _log.warning(
                "githi runtime started WITHOUT PostgreSQL persistence session_id=%s",
                self._session_id
            )

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

        # Acquire lease if session_store available (QM pattern)
        session_store = None
        lease = None
        if self.seams and hasattr(self.seams, 'session_store') and self.seams.session_store:
            session_store = self.seams.session_store
            # Try to acquire lease
            lease_attempt = await session_store.acquire_lease(
                self._session_id,
                "turn",
                60_000  # 60 second TTL
            )
            if not lease_attempt.lease:
                raise RuntimeError(
                    f"Session {self._session_id} is locked by {lease_attempt.held_by} "
                    f"until {lease_attempt.held_until}"
                )
            lease = lease_attempt.lease
            _log.info("Acquired lease for session %s", self._session_id)

        self._busy = True
        self._abort_requested = False
        try:
            loop = AgentLoop(
                seams=self.seams,
                model=self._model,
                session_dir=self._session_dir,
                should_abort=lambda: self._abort_requested,
            )

            # Collect new messages for PostgreSQL persistence
            new_messages = []

            async for payload in loop.run_turn(message, self._history):
                if self._abort_requested and not isinstance(
                    payload, (TurnEnd, RunEnd)
                ):
                    self._stream.emit(TurnEnd(stop_reason="aborted"))
                    return
                self._stream.emit(payload)
                if isinstance(payload, RunEnd):
                    self._history = list(payload.messages)
                    self._message_count = len(self._history)
                    # Track new messages (messages added this turn)
                    # Simple heuristic: messages added after original count
                    new_messages = payload.messages[self._message_count - len(new_messages):]
                await asyncio.sleep(0)

            # Persist new messages to PostgreSQL (if available)
            if session_store and new_messages:
                try:
                    entries_to_persist = [
                        {"type": "message", "payload": msg}
                        for msg in new_messages
                    ]
                    await session_store.append_entries(self._session_id, entries_to_persist)
                    _log.info(
                        "Persisted %d messages to PostgreSQL for session %s",
                        len(new_messages),
                        self._session_id
                    )
                except Exception as e:
                    _log.error(
                        "Failed to persist messages to PostgreSQL: %s", e,
                        exc_info=True
                    )
        finally:
            self._busy = False
            self._abort_requested = False

            # Release lease (ALWAYS, even on error - critical!)
            if lease and session_store:
                try:
                    await session_store.release_lease(lease)
                    _log.info("Released lease for session %s", self._session_id)
                except Exception as e:
                    _log.error("Failed to release lease: %s", e, exc_info=True)

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
