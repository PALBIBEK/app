"""The API's only way to drive a session: through its workflow.

Workflow failures come back as ``SessionCommandError`` carrying the original
domain error's class name, so endpoints keep their existing status codes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final

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


logger = logging.getLogger(__name__)

_NOT_READY_RETRIES: Final = 8
_NOT_READY_BACKOFF_S: Final = 0.1


def _workflow_not_ready(exc: RPCError) -> bool:
    """The update reached a workflow whose task failed or was not yet running it.

    Temporal's ``WorkflowNotReadyFailure``: nothing ran, so sending the same update
    again is how it is meant to be handled. Every update here carries its own id,
    so a retry that races a delivered one returns that one's outcome.
    """
    if exc.status is not RPCStatusCode.FAILED_PRECONDITION:
        return False
    return "WorkflowNotReady" in str(exc.raw_grpc_status) or "workflow task failure" in str(exc)


async def _delivered[T](send: Callable[[], Awaitable[T]]) -> T:
    """``send`` an update, again while the workflow is not ready for it (bounded backoff)."""
    delay = _NOT_READY_BACKOFF_S
    for attempt in range(_NOT_READY_RETRIES):
        try:
            return await send()
        except RPCError as exc:
            if not _workflow_not_ready(exc) or attempt == _NOT_READY_RETRIES - 1:
                raise
            logger.info("workflow not ready for an update (%s); retrying in %.1fs", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 2.0)
    raise AssertionError("unreachable")


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
        handle = self._handle(session_id)
        async with _commands():
            return await _delivered(
                lambda: handle.execute_update(
                    AgentSessionWorkflow.submit_message, message, id=f"msg:{message.command_id}"
                )
            )

    async def op(self, op: SessionOp) -> dict[str, Any] | None:
        """Run a session operation on the session's worker; a re-sent op_id runs once."""
        handle = self._handle(op.session_id)
        async with _commands():
            result = await _delivered(
                lambda: handle.execute_update(AgentSessionWorkflow.session_op, op, id=f"op:{op.op_id}")
            )
        return result.body

    async def respond_approval(
        self, session_id: str, approval_id: str, choice: str, *, responded_by: str | None = None
    ) -> None:
        handle = self._handle(session_id)
        async with _commands():
            await _delivered(
                lambda: handle.execute_update(
                    AgentSessionWorkflow.respond_approval,
                    ApprovalChoice(approval_id, choice, responded_by),
                    # Per choice: a retry of the same answer runs once, but a corrected
                    # answer is a new request, not the stored outcome of the refused one.
                    id=f"approval:{approval_id}:{choice}",
                )
            )

    async def abort(self, session_id: str) -> None:
        async with _commands():
            await self._handle(session_id).signal(AgentSessionWorkflow.abort)

    async def terminate(self, session_id: str) -> None:
        try:
            handle = self._handle(session_id)
            async with _commands():
                await _delivered(
                    lambda: handle.execute_update(AgentSessionWorkflow.terminate, id="terminate")
                )
        except SessionCommandError as exc:
            if exc.kind != "SessionClosed":  # already closed means already terminated
                raise

    async def status(self, session_id: str) -> SessionStatus:
        async with _commands():
            return await self._handle(session_id).query(AgentSessionWorkflow.status)

    async def check_reachable(self) -> bool:
        try:
            return await self._client.service_client.check_health()
        except Exception:  # noqa: BLE001 - a readiness probe reports failure, it never raises
            return False
