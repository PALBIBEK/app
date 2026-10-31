"""The API's only way to drive a session: through its workflow.

Workflow failures come back as ``SessionCommandError`` carrying the original
domain error's class name, so endpoints keep their existing status codes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, Literal

from cryptography.exceptions import InvalidTag
from temporalio.client import (
    Client,
    WithStartWorkflowOperation,
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
    WorkflowUpdateStage,
)
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from supervisor.temporal.contracts import (
    MAX_COMMAND_BYTES,
    MAX_MESSAGE_CHARS,
    Accepted,
    ApprovalChoice,
    CreateSession,
    ForkSpec,
    Message,
    Phase,
    SessionInput,
    SessionOp,
    SessionResult,
    SessionStatus,
    Timing,
    job_workflow_id,
    workflow_id,
)
from supervisor.temporal.fleet import QueueStats, queue_stats
from supervisor.temporal.workflow import AgentSessionWorkflow


class SessionCommandError(Exception):
    """A session command the workflow refused; ``kind`` is the domain error's class name."""

    def __init__(self, kind: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.details = details or {}


class TemporalUnavailable(Exception):
    """Temporal cannot be reached; the command was not delivered."""


@dataclass(frozen=True, slots=True)
class Startup:
    """Where a session's start stands, answered by the Temporal server (no worker needed).

    ``starting``: accepted, its workflow is setting it up (or waiting for a worker to);
    ``ready``: it can take messages; ``failed``: it could not be created (``reason`` says why);
    ``closed``: it ran and has ended.
    """

    status: Literal["starting", "ready", "failed", "closed"]
    worker_available: bool
    reason: str | None = None
    kind: str | None = None
    retrying: str | None = None
    """The setup step failed unexpectedly and Temporal is retrying it: its last error."""


_QUICK: Final = timedelta(seconds=3)
UNSERVED_S: Final = 5.0
"""Work queued this long with no worker taking it means no worker is serving the session."""
WORKER_SEEN_WITHIN_S: Final = 90.0
"""A worker that polled the queue this recently counts as running (they poll every ~minute)."""


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


async def failure_text(converter: DataConverter, failure: Any) -> str | None:
    """A failure's message. With encrypted history it is sealed in the failure's
    attributes (the server holds only "Encoded failure"), so it is opened first."""
    copy = type(failure)()
    copy.CopyFrom(failure)  # decoding rewrites it in place
    try:
        text = str(await converter.decode_failure(copy))
    except (KeyError, ValueError, InvalidTag):  # a key this process lacks, or a corrupt seal
        text = failure.message
    return text[:500] or None


def _plain_memo(client: Client, values: dict[str, Any]) -> dict[str, Any]:
    """The memo as plain payloads, which the client stores as they are: never sealed by the
    codec, so a session whose run outlives a codec key's rotation (continue-as-new carries
    the memo over as it is) is still authorised by the tenant and principal it holds."""
    converter = client.data_converter.payload_converter
    return {key: converter.to_payloads([value])[0] for key, value in values.items()}


def _pending(raw: Any) -> tuple[bool, Any]:
    """From a workflow's description: is work waiting with no worker to take it, and is a step
    failing and being retried (its last failure, as the server holds it)?"""
    from temporalio.api.enums.v1 import PendingActivityState, PendingWorkflowTaskState

    now = time.time()

    def waited(ts: Any) -> bool:
        return bool(ts.seconds) and now - ts.ToSeconds() > UNSERVED_S

    task = raw.pending_workflow_task
    unserved = task.state == PendingWorkflowTaskState.PENDING_WORKFLOW_TASK_STATE_SCHEDULED and waited(
        task.scheduled_time
    )
    retrying: Any = None
    for act in raw.pending_activities:
        if act.state == PendingActivityState.PENDING_ACTIVITY_STATE_SCHEDULED and waited(act.scheduled_time):
            unserved = True
        if act.attempt > 1 and act.HasField("last_failure"):
            retrying = act.last_failure
    return unserved, retrying


@contextlib.asynccontextmanager
async def _connecting() -> AsyncIterator[None]:
    """A lazy client that cannot reach Temporal raises a plain RuntimeError: say so."""
    try:
        yield
    except RuntimeError as exc:
        if str(exc).startswith("Failed client connect"):
            raise TemporalUnavailable(str(exc)) from exc
        raise


CLOSE_WAIT_S: Final = 60.0
"""How long erasing a session waits for it and its background jobs to stop."""

SUBMIT_ACCEPT_S = 10.0
"""How long a message waits for a worker to take it before it is sent as a signal."""


class SessionWorkflows:
    def __init__(
        self,
        client: Client,
        *,
        task_queue: str,
        backlog_alert_s: float = 30.0,
        submit_accept_s: float = SUBMIT_ACCEPT_S,
    ) -> None:
        self._client = client
        self._task_queue = task_queue
        self.backlog_alert_s = backlog_alert_s
        self._submit_accept_s = submit_accept_s

    @property
    def task_queue(self) -> str:
        return self._task_queue

    @property
    def client(self) -> Client:
        return self._client

    async def queue_stats(self) -> QueueStats:
        """The session queue as the Temporal server sees it: pollers and backlog."""
        try:
            async with _connecting():
                return await queue_stats(self._client, self._task_queue)
        except RPCError as exc:
            if exc.status in (RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED):
                raise TemporalUnavailable(str(exc)) from exc
            raise

    async def _polled_lately(self) -> bool:
        """``worker_available``, where not knowing counts as no: the signal goes anyway."""
        try:
            return await self.worker_available()
        except (RPCError, TemporalUnavailable) as exc:
            logger.info("could not tell whether a worker is up (%s); not waiting for one", exc)
            return False

    async def worker_available(self) -> bool:
        """Whether any worker polled this session queue recently (a server-side answer)."""
        from temporalio.api.enums.v1 import TaskQueueType
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

        now = time.time()
        for kind in (TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY):
            try:
                async with _connecting():
                    resp = await self._client.workflow_service.describe_task_queue(
                        DescribeTaskQueueRequest(
                            namespace=self._client.namespace,
                            task_queue=TaskQueue(name=self._task_queue),
                            task_queue_type=kind,
                        ),
                        timeout=_QUICK,
                    )
            except RPCError as exc:
                if exc.status in (RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED):
                    raise TemporalUnavailable(str(exc)) from exc
                raise
            if not any(
                now - p.last_access_time.ToSeconds() <= WORKER_SEEN_WITHIN_S for p in resp.pollers
            ):
                return False
        return True

    async def start(
        self, session_id: str, spec: CreateSession, *, timing: Timing | None = None
    ) -> None:
        """Start the session's workflow and return at once (``startup`` reports how it goes).

        The production shape for long work: accept, answer with an id, and let the client
        watch, rather than hold the request open while a worker sets the session up.
        """
        async with _commands():
            await self._client.start_workflow(
                AgentSessionWorkflow.run,
                SessionInput(session_id, create=spec, timing=timing or Timing()),
                id=workflow_id(session_id),
                task_queue=self._task_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                memo=_plain_memo(
                    self._client, {"tenant_id": spec.tenant_id, "principal_id": spec.principal_id or ""}
                ),
            )

    async def startup(self, session_id: str) -> tuple[Startup, dict[str, Any]] | None:
        """How the session's start went, and its workflow's memo (tenant, principal); None:
        no such session. Answers whether or not a worker is running."""
        handle = self._handle(session_id)
        try:
            async with _connecting():
                desc = await handle.describe(rpc_timeout=_QUICK)
        except RPCError as exc:
            if exc.status is RPCStatusCode.NOT_FOUND:
                return None
            if exc.status in (RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED):
                raise TemporalUnavailable(str(exc)) from exc
            raise
        memo = dict(await desc.memo())
        worker = await self.worker_available()
        unserved, failure = _pending(desc.raw_description)
        retrying = None if failure is None else await failure_text(self._client.data_converter, failure)
        worker = worker and not unserved
        if desc.status is not WorkflowExecutionStatus.RUNNING:
            try:
                result: SessionResult = await handle.result(rpc_timeout=_QUICK)
            except Exception:  # noqa: BLE001 — a crashed or cancelled workflow
                return Startup("failed", worker, reason="the session's workflow ended unexpectedly"), memo
            if result.phase is Phase.FAILED and result.error:
                return Startup("failed", worker, reason=result.error, kind=result.error_kind), memo
            return Startup("closed", worker), memo
        if not worker:
            return Startup("starting", False, retrying=retrying), memo
        try:
            # Answered by a worker: kept short, a worker that just died must not stall the answer.
            st: SessionStatus = await handle.query(
                AgentSessionWorkflow.status, rpc_timeout=timedelta(seconds=1.5)
            )
        except Exception:  # noqa: BLE001 — busy or replaying: still starting as far as we know
            return Startup("starting", worker, retrying=retrying), memo
        if st.phase is Phase.CREATING:
            return Startup("starting", worker, retrying=retrying), memo
        if st.phase is Phase.FAILED:
            return Startup("failed", worker, reason=st.error or "the session could not start", kind=st.error_kind), memo
        return Startup("ready", worker), memo

    def _handle(self, session_id: str) -> WorkflowHandle[AgentSessionWorkflow, Any]:
        return self._client.get_workflow_handle_for(
            AgentSessionWorkflow.run, workflow_id(session_id)
        )

    async def _fits(self, value: object, what: str) -> None:
        """Refuse a command too big for the session's workflow before it is sent.

        Temporal stores each command in the session's history, refusing any item over 2 MB,
        and an accepted update that big terminates the workflow: the session would be lost.
        So the size is checked here, encoded as the server would get it (codec included).
        """
        payloads = await self._client.data_converter.encode([value])
        size = sum(p.ByteSize() for p in payloads)
        if size > MAX_COMMAND_BYTES:
            raise SessionCommandError(
                "CommandTooLarge",
                f"{what} is {size // 1024} KB; a session takes at most {MAX_COMMAND_BYTES // 1024} KB "
                "at once. Attach large content as a file instead.",
                {"bytes": size, "limit": MAX_COMMAND_BYTES},
            )

    async def create(
        self, session_id: str, spec: CreateSession, *, timing: Timing | None = None
    ) -> SessionStatus:
        """Start the session's workflow and wait until the session exists (or why it cannot)."""
        return await self._start(SessionInput(session_id, create=spec, timing=timing or Timing()))

    async def _start(self, inp: SessionInput) -> SessionStatus:
        await self._fits(inp, "the session's start")
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
        """Send ``message``; if no worker takes it within ``submit_accept_s`` (twice that
        while one polled lately), send it as a signal, which the server keeps until a
        worker comes (pi-temporal's fallback), so a message is not lost while every worker
        is down. Either way it runs once: the session dedupes by command id.
        """
        if len(message.text) > MAX_MESSAGE_CHARS:
            raise SessionCommandError(
                "MessageTooLong",
                f"the message is {len(message.text):,} characters; a session takes at most "
                f"{MAX_MESSAGE_CHARS:,}. Attach long content as a file instead.",
                {"chars": len(message.text), "limit": MAX_MESSAGE_CHARS},
            )
        await self._fits(message, "the message")
        handle = self._handle(session_id)
        waited = False
        async with _commands():
            while True:
                try:
                    update = await asyncio.wait_for(
                        _delivered(
                            lambda: handle.start_update(
                                AgentSessionWorkflow.submit_message,
                                message,
                                # The same id each time: the server delivers it once.
                                id=f"msg:{message.command_id}",
                                wait_for_stage=WorkflowUpdateStage.ACCEPTED,
                            )
                        ),
                        timeout=self._submit_accept_s,
                    )
                except (TimeoutError, WorkflowUpdateRPCTimeoutOrCancelledError):
                    # Nothing took it: our bound passed (the SDK reports the cancelled wait
                    # as its own error), or the server's own RPC deadline did first.
                    if not waited and await self._polled_lately():
                        # A worker polled lately: it may only be busy, so wait once more for
                        # it to answer (its prepare, or a refusal) before sending a signal.
                        # Once: a worker that just died still looks polled for a while.
                        waited = True
                        logger.info("message %s waits for a busy worker", message.command_id)
                        continue
                    logger.warning(
                        "no worker took message %s in %.0fs; sent as a signal the server keeps",
                        message.command_id,
                        self._submit_accept_s,
                    )
                    await handle.signal(AgentSessionWorkflow.submit_message_signal, message)
                    return Accepted(message.command_id, queued=True, held=True)
                return await update.result()

    async def op(self, op: SessionOp) -> dict[str, Any] | None:
        """Run a session operation on the session's worker; a re-sent op_id runs once."""
        await self._fits(op, f"the {op.kind} operation")
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
        """Stop whatever run is going."""
        async with _commands():
            await self._handle(session_id).signal(AgentSessionWorkflow.abort)

    async def stop_run(self, session_id: str, command_id: str) -> None:
        """Stop the run of message ``command_id``, and no other. A signal: it needs no worker
        to answer, and the server keeps it until one comes."""
        async with _commands():
            await self._handle(session_id).signal(AgentSessionWorkflow.stop_run, command_id)

    async def cancel_queued(self, session_id: str, command_id: str) -> bool:
        """Remove a queued message before it runs; False if it is not waiting in the queue."""
        handle = self._handle(session_id)
        async with _commands():
            return await _delivered(
                lambda: handle.execute_update(
                    AgentSessionWorkflow.cancel_queued, command_id, id=f"cancel:{command_id}"
                )
            )

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

    async def close(
        self,
        session_id: str,
        *,
        jobs: Collection[str] = (),
        sessions: Collection[str] = (),
        timeout_s: float = CLOSE_WAIT_S,
    ) -> None:
        """End the session and wait until it, its background ``jobs`` and its subagent
        ``sessions`` (which it ends as it ends) have stopped, so nothing it runs writes after
        this returns. ``TimeoutError`` if they do not stop in ``timeout_s`` (a job whose worker
        is gone, say)."""
        await self.terminate(session_id)
        handles = [
            self._handle(session_id),
            *(self._client.get_workflow_handle(job_workflow_id(session_id, job)) for job in jobs),
            *(self._client.get_workflow_handle(workflow_id(sid)) for sid in sessions),
        ]
        async with asyncio.timeout(timeout_s):
            for handle in handles:
                # A cancelled job's workflow fails; one already gone is not found: both stopped.
                with contextlib.suppress(WorkflowFailureError, RPCError):
                    await handle.result()

    async def status(self, session_id: str) -> SessionStatus:
        async with _commands():
            return await self._handle(session_id).query(AgentSessionWorkflow.status)

    async def check_reachable(self) -> bool:
        try:
            return await self._client.service_client.check_health()
        except Exception:  # noqa: BLE001 - a readiness probe reports failure, it never raises
            return False
