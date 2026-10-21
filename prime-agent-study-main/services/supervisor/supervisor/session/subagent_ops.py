"""Subagent session ops — Protocol transport + journal lifecycle events.

Session/journal stay free of provider concretes (P-3). Callers inject
``SubagentTransport`` from ``build_runtime_seams`` / binders only.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from supervisor.agent.events import JobEnd, JobStart, SubagentEnd, SubagentStart
from supervisor.journal.writer import JournalWriter
from supervisor.seams import (
    ChildHandle,
    DepthExceeded,
    JobHandle,
    SubagentRequest,
    SubagentRunResult,
    SubagentTransport,
)
from supervisor.session.tree import SessionTree, build_session_tree

# Default when harness YAML omits subagents.max_depth.
DEFAULT_MAX_DEPTH = 3


async def journal_background_end(
    writer: JournalWriter,
    session_id: str,
    *,
    job_id: str,
    run_id: str,
    stop_reason: str,
    output: str | None,
) -> None:
    """Journal a background job's end; the first end recorded (done, error, interrupted) wins."""
    await writer.append(
        session_id,
        JobEnd(job_id=job_id, stop_reason=stop_reason, output=output),
        turn_id=None,
        trace_id=None,
        event_key=f"job:{job_id}:end",
    )
    await writer.append(
        session_id,
        SubagentEnd(run_id=run_id, stop_reason=stop_reason, output=output),
        turn_id=None,
        trace_id=None,
        event_key=f"job:{job_id}:subagent_end",
    )


class SubagentOps:
    """Bridge: SubagentTransport ↔ AGENT_CONTRACT subagent_*/job_* journal events."""

    def __init__(
        self,
        *,
        parent_session_id: str,
        transport: SubagentTransport,
        writer: JournalWriter,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self._parent = parent_session_id
        self._transport = transport
        self._writer = writer
        self._max_depth = max_depth if max_depth > 0 else DEFAULT_MAX_DEPTH

    def _run_id(self) -> str:
        return f"run-{uuid.uuid4().hex[:12]}"

    def _child_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def _enforce_depth(self, request: SubagentRequest) -> None:
        """Gate spawn BEFORE SubagentStart (D12). Over-depth → DepthExceeded.

        ``parent_depth`` must be server-bound (VibeSec H1) — never trust a client
        understatement. Nested spawn must use the session tree / journal ancestry.
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
            # The provider runs a model to completion: off the loop, so heartbeats go on.
            result = await asyncio.to_thread(self._transport.start_one_shot, provider_name, request)
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
        return _as_journaled(result, child_id)

    async def fork(
        self,
        provider_name: str,
        request: SubagentRequest,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> SubagentRunResult:
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
            result = await asyncio.to_thread(self._transport.start_fork, provider_name, request)
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
        return _as_journaled(result, child_id)

    async def begin_background(
        self,
        provider_name: str,
        request: SubagentRequest,
        *,
        job_id: str,
        run_id: str,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> JobHandle:
        """Validate and journal a background job's start; nothing runs yet.

        The job itself runs durably elsewhere (``run_background`` in a workflow-owned
        activity) and is closed by ``finish_background``. Keyed by ``job_id``, so a
        retried start journals once.
        """
        self._enforce_depth(request)
        self._transport.check_background(provider_name, request)
        await self._writer.append(
            self._parent,
            JobStart(job_id=job_id, kind="subagent"),
            turn_id=turn_id,
            trace_id=trace_id,
            event_key=f"job:{job_id}:start",
        )
        await self._writer.append(
            self._parent,
            SubagentStart(
                run_id=run_id,
                child_session_id=job_id,
                parent_session_id=self._parent,
                mode="background",
                provider=provider_name,
                label=request.description or None,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
            event_key=f"job:{job_id}:subagent",
        )
        return JobHandle(job_id=job_id)

    def run_background(self, provider_name: str, request: SubagentRequest) -> SubagentRunResult:
        """The job's work: the provider's run of the request. Blocking; call in a thread."""
        return self._transport.start_one_shot(provider_name, request)

    async def finish_background(
        self, *, job_id: str, run_id: str, stop_reason: str, output: str | None
    ) -> None:
        await journal_background_end(
            self._writer,
            self._parent,
            job_id=job_id,
            run_id=run_id,
            stop_reason=stop_reason,
            output=output,
        )

    async def start_continuable(
        self,
        provider_name: str,
        request: SubagentRequest,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> ChildHandle:
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
        self._transport.send_message(child_id, text)

    def interrupt_child(self, child_id: str) -> None:
        self._transport.interrupt(child_id)


    async def session_tree(self, reader: Any) -> SessionTree:
        envs = await reader.read(self._parent, since=-1)
        return build_session_tree(self._parent, envs)


def _as_journaled(result: SubagentRunResult, child_id: str) -> SubagentRunResult:
    """The result under the child id the journal recorded (SubagentStart), the one the
    children list and every client know. A one-shot child has finished, so nothing needs
    the provider's own id for a live handle; it is kept in ``meta``."""
    meta = dict(result.meta)
    if result.child_id and result.child_id != child_id:
        meta["provider_child_id"] = result.child_id
    return SubagentRunResult(text=result.text, child_id=child_id, status=result.status, meta=meta)
