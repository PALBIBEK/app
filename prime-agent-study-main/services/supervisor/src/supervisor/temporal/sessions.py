"""The API's only way to drive a session: through its workflow.

Workflow failures come back as ``SessionCommandError`` carrying the original
domain error's class name, so endpoints keep their existing status codes.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from temporalio.client import (
    Client,
    WithStartWorkflowOperation,
    WorkflowHandle,
    WorkflowUpdateFailedError,
)
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from supervisor.temporal.contracts import (
    Accepted,
    ApprovalChoice,
    CreateSession,
    ForkSpec,
    Message,
    SessionInput,
    SessionOp,
    SessionStatus,
    Timing,
    workflow_id,
)
from supervisor.temporal.workflow import AgentSessionWorkflow


class SessionCommandError(Exception):
    """A session command the workflow refused; ``kind`` is the domain error's class name."""

    def __init__(self, kind: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.details = details or {}


class TemporalUnavailable(Exception):
    """Temporal cannot be reached; the command was not delivered."""


@contextlib.asynccontextmanager
async def _commands() -> AsyncIterator[None]:
    try:
        yield
    except WorkflowUpdateFailedError as exc:
        cause = exc.cause
        if isinstance(cause, ApplicationError):
            first = cause.details[0] if cause.details else None
            details = first if isinstance(first, dict) else {}
            raise SessionCommandError(cause.type or "Error", cause.message, details) from exc
        raise
    except RPCError as exc:
        if exc.status in (RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED):
            raise TemporalUnavailable(str(exc)) from exc
        if exc.status is RPCStatusCode.NOT_FOUND:
            raise SessionCommandError("SessionClosed", "session is no longer running") from exc
        raise
    except RuntimeError as exc:
        # A lazy client that cannot connect raises a plain RuntimeError with this prefix.
        if str(exc).startswith("Failed client connect"):
            raise TemporalUnavailable(str(exc)) from exc
        raise


class SessionWorkflows:
    def __init__(self, client: Client, *, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    def _handle(self, session_id: str) -> WorkflowHandle[AgentSessionWorkflow, Any]:
        return self._client.get_workflow_handle_for(
            AgentSessionWorkflow.run, workflow_id(session_id)
        )

    async def create(
        self, session_id: str, spec: CreateSession, *, timing: Timing | None = None
    ) -> SessionStatus:
        """Start the session's workflow and wait until the session exists (or why it cannot)."""
        return await self._start(SessionInput(session_id, create=spec, timing=timing or Timing()))

    async def _start(self, inp: SessionInput) -> SessionStatus:
        start = WithStartWorkflowOperation(
            AgentSessionWorkflow.run,
            inp,
            id=workflow_id(inp.session_id),
            task_queue=self._task_queue,
            # Re-sending the same create is idempotent; a closed session id is never reused.
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
        async with _commands():
            return await self._client.execute_update_with_start_workflow(
                AgentSessionWorkflow.wait_ready, start_workflow_operation=start
            )

    async def fork(
        self,
        source_id: str,
        new_id: str,
        *,
        at_seq: int | None = None,
        timing: Timing | None = None,
    ) -> SessionStatus:
        """Start ``new_id`` as a copy of ``source_id`` with its own workflow; wait until ready."""
        return await self._start(
            SessionInput(new_id, fork=ForkSpec(source_id, at_seq), timing=timing or Timing())
        )

    async def submit(self, session_id: str, message: Message) -> Accepted:
        async with _commands():
            return await self._handle(session_id).execute_update(
                AgentSessionWorkflow.submit_message, message, id=f"msg:{message.command_id}"
            )

    async def op(self, op: SessionOp) -> dict[str, Any] | None:
        """Run a session operation on the session's worker; a re-sent op_id runs once."""
        async with _commands():
            result = await self._handle(op.session_id).execute_update(
                AgentSessionWorkflow.session_op, op, id=f"op:{op.op_id}"
            )
        return result.body

    async def respond_approval(
        self, session_id: str, approval_id: str, choice: str, *, responded_by: str | None = None
    ) -> None:
        async with _commands():
            await self._handle(session_id).execute_update(
                AgentSessionWorkflow.respond_approval,
                ApprovalChoice(approval_id, choice, responded_by),
                id=f"approval:{approval_id}",
            )

    async def abort(self, session_id: str) -> None:
        async with _commands():
            await self._handle(session_id).signal(AgentSessionWorkflow.abort)

    async def terminate(self, session_id: str) -> None:
        try:
            async with _commands():
                await self._handle(session_id).execute_update(AgentSessionWorkflow.terminate)
        except SessionCommandError as exc:
            if exc.kind != "SessionClosed":  # already closed means already terminated
                raise

    async def status(self, session_id: str) -> SessionStatus:
        async with _commands():
            return await self._handle(session_id).query(AgentSessionWorkflow.status)

    async def check_reachable(self) -> bool:
        try:
            return await self._client.service_client.check_health()
        except Exception:  # a readiness probe reports failure, it never raises
            return False
