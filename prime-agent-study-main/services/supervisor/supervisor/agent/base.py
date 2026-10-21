"""AgentRuntime protocol and AgentState. Defines the contract for runtime implementations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from supervisor.agent.capabilities import Capabilities
from supervisor.agent.events import Envelope
from supervisor.sandbox.base import Sandbox


class AgentPhase(Enum):
    """Agent lifecycle phases."""
    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    EXECUTING = "executing"
    COMPACTING = "compacting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"

    @property
    def is_active(self) -> bool:
        return self in (AgentPhase.READY, AgentPhase.EXECUTING, AgentPhase.COMPACTING)

    @property
    def is_terminal(self) -> bool:
        return self in (AgentPhase.STOPPED, AgentPhase.ERROR)


@dataclass(frozen=True)
class AgentState:
    """Immutable snapshot of agent state."""
    session_id: str
    phase: AgentPhase = AgentPhase.CREATED
    pending_count: int = 0
    message_count: int = 0
    turn_count: int = 0
    model: str | None = None
    provider: str | None = None
    context_window: int | None = None
    tokens_used: int = 0
    current_turn_id: str | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_busy(self) -> bool:
        return self.phase == AgentPhase.EXECUTING

    @property
    def is_compacting(self) -> bool:
        return self.phase == AgentPhase.COMPACTING

    @property
    def settled(self) -> bool:
        return self.phase == AgentPhase.READY and self.pending_count == 0

    @property
    def can_accept_message(self) -> bool:
        return self.phase == AgentPhase.READY

    @property
    def context_usage_ratio(self) -> float:
        return self.tokens_used / self.context_window if self.context_window else 0.0


    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "phase": self.phase.value,
            "is_busy": self.is_busy,
            "settled": self.settled,
            "pending_count": self.pending_count,
            "message_count": self.message_count,
            "turn_count": self.turn_count,
            "model": self.model,
            "provider": self.provider,
            "context_window": self.context_window,
            "tokens_used": self.tokens_used,
            "context_usage_ratio": self.context_usage_ratio,
        }


@runtime_checkable
class AgentRuntime(Protocol):
    """Protocol for agent runtime implementations.

    Lifecycle: start() -> send()/abort() -> stop()
    Events stream via events() async iterator.
    """

    async def start(self, sandbox: Sandbox | None, session_dir: str | None) -> None:
        """Initialize runtime with optional sandbox/session directory."""
        ...

    async def send(self, message: str, *, command_id: str, mode: str = "prompt") -> None:
        """Send message for processing. Non-blocking; results via events()."""
        ...

    async def abort(self) -> None:
        """Abort current turn. Safe if no turn active."""
        ...

    async def get_state(self) -> AgentState:
        """Get current state snapshot."""
        ...

    async def get_history(self) -> list[dict[str, Any]]:
        """Get message history."""
        ...

    async def get_capabilities(self) -> Capabilities:
        """Get runtime capabilities."""
        ...

    def events(self) -> AsyncIterator[Envelope]:
        """Stream events from the runtime."""
        ...

    async def stop(self) -> None:
        """Graceful shutdown."""
        ...


