"""AgentSessionWorkflow: every session is one long-lived (entity) workflow.

The workflow owns ordering and durability; activities own side effects. It never
touches the database or a runtime itself, so it stays deterministic on replay.

Patterns (Temporal entity-workflow and message-passing guidance):
- Creation is awaited through the ``wait_ready`` update, so the API's
  update-with-start returns real 4xx errors for bad compositions.
- ``submit_message`` is an update with a validator: messages for a dead session
  are rejected before anything is recorded. Commands are de-duplicated by id
  across continue-as-new.
- The session's live runtime sits in one worker. Its activities go to that
  worker's own task queue; if that worker is gone (schedule-to-start timeout),
  any worker adopts the session by cold-resuming it from the journal.
- Continue-as-new only between turns, after every handler has finished.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import TypeVar

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    TimeoutError,
    TimeoutType,
    is_cancelled_exception,
)

from supervisor.temporal.contracts import (
    NON_RETRYABLE,
    Accepted,
    Activity,
    ApprovalChoice,
    ApprovalRequest,
    Carried,
    CreateRequest,
    Message,
    Phase,
    Placement,
    SessionInput,
    SessionResult,
    SessionStatus,
    TurnOutcome,
    TurnRequest,
)

T = TypeVar("T")

# Long enough to ride out a busy worker, short enough to notice a dead one.
PLACEMENT_TIMEOUT = timedelta(seconds=60)
SEEN_COMMANDS_WINDOW = 1_000

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=list(NON_RETRYABLE),
)
# A retried turn may repeat side effects; run_turn refuses to resend after tools ran.
_TURN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
    non_retryable_error_types=list(NON_RETRYABLE),
)


def _is_placement_lost(err: BaseException) -> bool:
    return (
        isinstance(err, ActivityError)
        and isinstance(err.cause, TimeoutError)
        and err.cause.type is TimeoutType.SCHEDULE_TO_START
    )


def _domain_error(err: ActivityError) -> ApplicationError:
    """Surface an activity's domain error unchanged so the API maps it to the same status."""
    cause = err.cause
    if isinstance(cause, ApplicationError):
        return ApplicationError(
            cause.message, *cause.details, type=cause.type, non_retryable=True
        )
    return ApplicationError(str(err), type="ActivityFailed", non_retryable=True)


@workflow.defn(name="AgentSession")
class AgentSessionWorkflow:
    @workflow.init
    def __init__(self, inp: SessionInput) -> None:
        self._session_id = inp.session_id
        self._state = inp.carried or Carried()
        self._running: Message | None = None
        self._turn: asyncio.Task[TurnOutcome] | None = None
        self._terminate_requested = False
        self._abort_requested = False
        self._creation_error: ApplicationError | None = None
        self._submit_lock = asyncio.Lock()

    # --- main loop -----------------------------------------------------------

    @workflow.run
    async def run(self, inp: SessionInput) -> SessionResult:
        try:
            if self._state.phase is Phase.CREATING:
                await self._create(inp)
            while self._state.phase is not Phase.FAILED:
                await workflow.wait_condition(
                    lambda: bool(self._state.pending) or self._terminate_requested
                )
                if self._terminate_requested:
                    break
                await self._run_turn(self._state.pending.pop(0))
                if workflow.info().is_continue_as_new_suggested():
                    await workflow.wait_condition(workflow.all_handlers_finished)
                    if not self._terminate_requested:
                        workflow.continue_as_new(
                            SessionInput(self._session_id, carried=self._state)
                        )
        except asyncio.CancelledError:
            # Workflow cancelled from outside (e.g. the Temporal UI): clean up like terminate.
            await self._terminate()
            raise
        if self._state.phase is not Phase.FAILED:
            await self._terminate()
        await workflow.wait_condition(workflow.all_handlers_finished)
        return SessionResult(self._session_id, self._state.phase, self._state.turns_completed)

    async def _create(self, inp: SessionInput) -> None:
        assert inp.create is not None, "a new session workflow needs CreateSession"
        try:
            self._state.placement = await workflow.execute_activity(
                Activity.CREATE,
                CreateRequest(self._session_id, inp.create),
                result_type=Placement,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_RETRY,
            )
            self._state.phase = Phase.IDLE
        except ActivityError as err:
            self._creation_error = _domain_error(err)
            self._state.phase = Phase.FAILED

    async def _run_turn(self, message: Message) -> None:
        self._running, self._state.phase = message, Phase.RUNNING
        try:
            await self._on_session_worker(
                lambda queue: workflow.start_activity(
                    Activity.RUN_TURN,
                    TurnRequest(self._session_id, message),
                    result_type=TurnOutcome,
                    task_queue=queue,
                    schedule_to_start_timeout=PLACEMENT_TIMEOUT,
                    start_to_close_timeout=timedelta(hours=24),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=_TURN_RETRY,
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                ),
                track_turn=True,
            )
        except (ActivityError, asyncio.CancelledError) as err:
            aborted = is_cancelled_exception(err) and self._abort_requested
            if isinstance(err, asyncio.CancelledError) and not aborted:
                raise  # the workflow itself is being cancelled
            if not aborted:
                # run_turn journals the failure for clients; the session stays usable.
                workflow.logger.warning("turn %s failed: %s", message.command_id, err)
        finally:
            self._running, self._turn, self._abort_requested = None, None, False
            self._state.turns_completed += 1
            if self._state.phase is Phase.RUNNING:
                self._state.phase = Phase.IDLE

    async def _terminate(self) -> None:
        if self._state.phase in (Phase.TERMINATED, Phase.FAILED):
            return
        if self._turn is not None and not self._turn.done():
            self._turn.cancel()
        await self._on_session_worker(
            lambda queue: workflow.start_activity(
                Activity.TERMINATE,
                self._session_id,
                task_queue=queue,
                schedule_to_start_timeout=PLACEMENT_TIMEOUT,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_RETRY,
            )
        )
        self._state.phase = Phase.TERMINATED

    # --- placement -----------------------------------------------------------

    async def _on_session_worker(
        self,
        start: Callable[[str], asyncio.Task[T]],
        *,
        track_turn: bool = False,
    ) -> T:
        """Run on the worker holding the session; adopt it elsewhere if that worker is gone."""
        while True:
            queue = (
                self._state.placement.task_queue
                if self._state.placement is not None
                else workflow.info().task_queue
            )
            task = start(queue)
            if track_turn:
                self._turn = task
            try:
                return await task
            except ActivityError as err:
                if not _is_placement_lost(err) or self._state.placement is None:
                    raise
                workflow.logger.warning("session worker %s lost; adopting", queue)
                self._state.placement = await workflow.execute_activity(
                    Activity.ADOPT,
                    self._session_id,
                    result_type=Placement,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_RETRY,
                )

    # --- messages ------------------------------------------------------------

    @workflow.update(name="wait_ready")
    async def wait_ready(self) -> SessionStatus:
        await workflow.wait_condition(lambda: self._state.phase is not Phase.CREATING)
        if self._creation_error is not None:
            raise self._creation_error
        return self.status()

    @workflow.update(name="submit_message")
    async def submit_message(self, message: Message) -> Accepted:
        if message.command_id in self._state.seen_commands:
            return Accepted(message.command_id, queued=self._is_queued(message.command_id))
        async with self._submit_lock:
            if self._running is not None or self._state.pending:
                self._remember(message.command_id)
                self._state.pending.append(message)
                return Accepted(message.command_id, queued=True)
            # Idle: prepare now so recovery/budget/approval problems reach the caller.
            try:
                await self._on_session_worker(
                    lambda queue: workflow.start_activity(
                        Activity.PREPARE_TURN,
                        self._session_id,
                        task_queue=queue,
                        schedule_to_start_timeout=PLACEMENT_TIMEOUT,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=_RETRY,
                    )
                )
            except ActivityError as err:
                raise _domain_error(err) from err
            self._remember(message.command_id)
            self._state.pending.append(message)
            return Accepted(message.command_id, queued=False)

    @submit_message.validator
    def _validate_submit(self, message: Message) -> None:
        self._reject_if_closed()
        if not message.command_id or not message.text:
            raise ValueError("command_id and text are required")

    @workflow.update(name="respond_approval")
    async def respond_approval(self, choice: ApprovalChoice) -> None:
        try:
            await self._on_session_worker(
                lambda queue: workflow.start_activity(
                    Activity.RESPOND_APPROVAL,
                    ApprovalRequest(self._session_id, choice),
                    task_queue=queue,
                    schedule_to_start_timeout=PLACEMENT_TIMEOUT,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=_RETRY,
                )
            )
        except ActivityError as err:
            raise _domain_error(err) from err

    @respond_approval.validator
    def _validate_approval(self, choice: ApprovalChoice) -> None:
        self._reject_if_closed()
        if not choice.approval_id or not choice.choice:
            raise ValueError("approval_id and choice are required")

    @workflow.signal(name="abort")
    def abort(self) -> None:
        if self._turn is not None and not self._turn.done():
            self._abort_requested = True
            self._turn.cancel()

    @workflow.update(name="terminate")
    async def terminate(self) -> SessionStatus:
        self._terminate_requested = True
        await workflow.wait_condition(
            lambda: self._state.phase in (Phase.TERMINATED, Phase.FAILED)
        )
        return self.status()

    @workflow.query(name="status")
    def status(self) -> SessionStatus:
        return SessionStatus(
            phase=self._state.phase,
            turns_completed=self._state.turns_completed,
            queued=len(self._state.pending),
            running_command=self._running.command_id if self._running else None,
        )

    # --- helpers -------------------------------------------------------------

    def _reject_if_closed(self) -> None:
        if self._terminate_requested or self._state.phase in (Phase.TERMINATED, Phase.FAILED):
            raise ApplicationError("session terminated", type="SessionTerminated")

    def _remember(self, command_id: str) -> None:
        seen = self._state.seen_commands
        seen.append(command_id)
        del seen[:-SEEN_COMMANDS_WINDOW]

    def _is_queued(self, command_id: str) -> bool:
        return any(m.command_id == command_id for m in self._state.pending)

