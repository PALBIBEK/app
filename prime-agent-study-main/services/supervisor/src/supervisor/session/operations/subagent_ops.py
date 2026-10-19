"""Subagent session operations — Protocol transport + journal lifecycle events.

Bridges SubagentTransport with journal events for session lifecycle tracking.
Session/journal stay free of provider concretes (P-3). Callers inject
SubagentTransport from build_runtime_seams / binders only.

Features:
- Spawn one-shot subagents
- Fork persistent child sessions
- Start background jobs
- Start continuable sessions
- Depth enforcement for security (VibeSec H1)
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from supervisor.agent.events import JobEnd, JobStart, SubagentEnd, SubagentStart
from supervisor.seams import (
    ChildHandle,
    DepthExceeded,
    JobHandle,
    SubagentRequest,
    SubagentRunResult,
)
from supervisor.session.operations.tree import SessionTree, build_session_tree

if TYPE_CHECKING:
    from supervisor.journal.writer import JournalWriter
    from supervisor.seams import SubagentTransport

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "SubagentOps",
]

logger = logging.getLogger(__name__)

# Default when harness YAML omits subagents.max_depth
DEFAULT_MAX_DEPTH = 3


class SubagentOps:
    """Bridge: SubagentTransport ↔ AGENT_CONTRACT subagent_*/job_* journal events.

    Provides journaled subagent operations with depth enforcement
    and proper event logging for crash recovery and session tree
    reconstruction.

    Example:
        ops = SubagentOps(
            parent_session_id="sess-123",
            transport=transport,
            writer=writer,
            max_depth=3,
        )

        result = await ops.spawn(
            provider_name="claude",
            request=SubagentRequest(prompt="..."),
        )
    """

    def __init__(
        self,
        *,
        parent_session_id: str,
        transport: SubagentTransport,
        writer: JournalWriter,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        """Initialize subagent operations.

        Args:
            parent_session_id: The parent session context
            transport: SubagentTransport for provider communication
            writer: Journal writer for event logging
            max_depth: Maximum allowed subagent nesting depth
        """
        self._parent = parent_session_id
        self._transport = transport
        self._writer = writer
        self._max_depth = max_depth if max_depth > 0 else DEFAULT_MAX_DEPTH

    def _run_id(self) -> str:
        """Generate unique run identifier."""
        return f"run-{uuid.uuid4().hex[:12]}"

    def _child_id(self, prefix: str) -> str:
        """Generate unique child session identifier."""
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def _enforce_depth(self, request: SubagentRequest) -> None:
        """Enforce depth limits before spawn (D12).

        Gate spawn BEFORE SubagentStart. Over-depth → DepthExceeded.
        parent_depth must be server-bound (VibeSec H1) — never trust
        a client understatement. Nested spawn must use the session
        tree / journal ancestry.

        Args:
            request: The subagent request to validate

        Raises:
            DepthExceeded: If depth would exceed limit
        """
        claimed = max(int(request.parent_depth), 0)
        request.parent_depth = claimed
        limit = (
            request.depth_limit
            if request.depth_limit is not None
            else self._max_depth
        )
        limit = min(int(limit), self._max_depth)
        child_depth = claimed + 1
        if child_depth > limit:
            logger.warning(
                "subagent depth exceeded",
                extra={
                    "parent_session_id": self._parent,
                    "child_depth": child_depth,
                    "limit": limit,
                },
            )
            raise DepthExceeded(
                f"subagent depth {child_depth} exceeds max_depth {limit}"
            )

    async def spawn(
        self,
        provider_name: str,
        request: SubagentRequest,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> SubagentRunResult:
        """Spawn a one-shot subagent with journaling.

        Args:
            provider_name: The provider to use
            request: Subagent request configuration
            turn_id: Optional turn identifier
            trace_id: Optional trace identifier

        Returns:
            SubagentRunResult with output
        """
        self._enforce_depth(request)
        run_id = self._run_id()
        child_id = self._child_id("child")

        await self._writer.append(
            self._parent,
            SubagentStart(
                run_id=run_id,
                child_session_id=child_id,
                parent_session_id=self._parent,
                mode="one_shot",
                provider=provider_name,
                label=request.description or None,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )

        try:
            result = self._transport.start_one_shot(provider_name, request)
        except Exception:
            await self._writer.append(
                self._parent,
                SubagentEnd(run_id=run_id, stop_reason="error", output=None),
                turn_id=turn_id,
                trace_id=trace_id,
            )
            raise

        await self._writer.append(
            self._parent,
            SubagentEnd(
                run_id=run_id,
                stop_reason=result.status or "ok",
                output=result.text or None,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )

        # Prefer provider child_id for live handles; journal tree uses minted id
        if result.child_id:
            return result
        return SubagentRunResult(
            text=result.text,
            child_id=child_id,
            status=result.status,
            meta=result.meta,
        )

    async def fork(
        self,
        provider_name: str,
        request: SubagentRequest,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> SubagentRunResult:
        """Fork a persistent child session with journaling.

        Args:
            provider_name: The provider to use
            request: Subagent request configuration
            turn_id: Optional turn identifier
            trace_id: Optional trace identifier

        Returns:
            SubagentRunResult with child session info
        """
        self._enforce_depth(request)
        run_id = self._run_id()
        child_id = self._child_id("fork")

        await self._writer.append(
            self._parent,
            SubagentStart(
                run_id=run_id,
                child_session_id=child_id,
                parent_session_id=self._parent,
                mode="fork",
                provider=provider_name,
                label=request.description or None,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )

        try:
            result = self._transport.start_fork(provider_name, request)
        except Exception:
            await self._writer.append(
                self._parent,
                SubagentEnd(run_id=run_id, stop_reason="error", output=None),
                turn_id=turn_id,
                trace_id=trace_id,
            )
            raise

        await self._writer.append(
            self._parent,
            SubagentEnd(
                run_id=run_id,
                stop_reason=result.status or "ok",
                output=result.text or None,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )

        if result.child_id:
            return result
        return SubagentRunResult(
            text=result.text,
            child_id=child_id,
            status=result.status,
            meta=result.meta,
        )

    async def start_background(
        self,
        provider_name: str,
        request: SubagentRequest,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
        complete_immediately: bool = False,
    ) -> JobHandle:
        """Start a background job with journaling.

        Journals JobStart + SubagentStart. Optional immediate end for sync fakes.

        Args:
            provider_name: The provider to use
            request: Subagent request configuration
            turn_id: Optional turn identifier
            trace_id: Optional trace identifier
            complete_immediately: If True, immediately journal completion

        Returns:
            JobHandle for tracking the background job
        """
        self._enforce_depth(request)
        handle = self._transport.start_background(provider_name, request)

        await self._writer.append(
            self._parent,
            JobStart(job_id=handle.job_id, kind="subagent"),
            turn_id=turn_id,
            trace_id=trace_id,
        )

        run_id = self._run_id()
        await self._writer.append(
            self._parent,
            SubagentStart(
                run_id=run_id,
                child_session_id=handle.job_id,
                parent_session_id=self._parent,
                mode="background",
                provider=provider_name,
                label=request.description or None,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )

        if complete_immediately:
            await self._writer.append(
                self._parent,
                JobEnd(job_id=handle.job_id, stop_reason="ok", output=None),
                turn_id=turn_id,
                trace_id=trace_id,
            )
            await self._writer.append(
                self._parent,
                SubagentEnd(run_id=run_id, stop_reason="ok", output=None),
                turn_id=turn_id,
                trace_id=trace_id,
            )

        return handle

    async def start_continuable(
        self,
        provider_name: str,
        request: SubagentRequest,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> ChildHandle:
        """Start a continuable child session with journaling.

        Args:
            provider_name: The provider to use
            request: Subagent request configuration
            turn_id: Optional turn identifier
            trace_id: Optional trace identifier

        Returns:
            ChildHandle for the continuable session
        """
        self._enforce_depth(request)
        run_id = self._run_id()
        handle = self._transport.start_continuable(provider_name, request)

        await self._writer.append(
            self._parent,
            SubagentStart(
                run_id=run_id,
                child_session_id=handle.child_id,
                parent_session_id=self._parent,
                mode="continuable",
                provider=provider_name,
                label=request.description or None,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )

        return handle

    def send_child_message(self, child_id: str, text: str) -> None:
        """Send a message to a child session.

        Args:
            child_id: The child session identifier
            text: Message content
        """
        self._transport.send_message(child_id, text)

    def interrupt_child(self, child_id: str) -> None:
        """Interrupt a child session.

        Args:
            child_id: The child session identifier
        """
        self._transport.interrupt(child_id)

    def list_children_live(self, parent_id: str | None = None) -> list[Any]:
        """List live children from the transport.

        Args:
            parent_id: Optional parent filter

        Returns:
            List of live child session info
        """
        return self._transport.list_children(parent_id)

    async def session_tree(self, reader: Any) -> SessionTree:
        """Build session tree from journal events.

        Args:
            reader: Journal reader instance

        Returns:
            SessionTree with children and jobs
        """
        envs = await reader.read(self._parent, since=-1)
        return build_session_tree(self._parent, envs)
