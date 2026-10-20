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
- githi turns are driven step by step: one activity per model call and per tool
  call. Tools retry or run at most once by their manifest class; the loop guard
  and approval timers live here, so they survive any worker.
- Continue-as-new only between turns, after every handler has finished.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    TimeoutType,
    WorkflowAlreadyStartedError,
    is_cancelled_exception,
)
from temporalio.exceptions import TimeoutError as ActivityTimeout
from temporalio.workflow import ParentClosePolicy

from supervisor.temporal.contracts import (
    CLOSED_STATUSES,
    JOB_FINISHED_SIGNAL,
    NON_RETRYABLE,
    RETRYABLE_OPS,
    START_JOB_SIGNAL,
    Accepted,
    Activity,
    ApprovalAsk,
    ApprovalChoice,
    ApprovalDecision,
    ApprovalNeeded,
    ApprovalRequest,
    CaptureConfig,
    CaptureRequest,
    Carried,
    CreateRequest,
    EndRunRequest,
    ForkRequest,
    GuardSettings,
    JobSpec,
    LostCall,
    Message,
    Op,
    OpResult,
    Phase,
    Placement,
    PlannedCall,
    RetryMode,
    RunPlan,
    SessionInput,
    SessionOp,
    SessionResult,
    SessionStatus,
    StartJob,
    StepRequest,
    StepResult,
    ToolRequest,
    TurnOutcome,
    TurnRequest,
    job_ids,
    job_workflow_id,
)
from supervisor.temporal.job_workflow import SubagentJobWorkflow

with workflow.unsafe.imports_passed_through():
    # Pure, deterministic policy code: the loop guard runs inside the workflow.
    from supervisor.policy.guards import GuardAction, GuardConfig, LoopGuard
    from supervisor.policy.types import is_allow_choice

SEEN_COMMANDS_WINDOW = 1_000

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=list(NON_RETRYABLE),
)
# A retried whole turn may repeat side effects; run_turn refuses to resend after tools ran.
_TURN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
    non_retryable_error_types=list(NON_RETRYABLE),
)
_ONCE = RetryPolicy(maximum_attempts=1)
_SHORT = timedelta(minutes=2)
_MODEL_CALL = timedelta(minutes=10)
# Patch id (workflow.patched): histories recorded before lost calls were closed
# ahead of adoption replay without the extra activity.
_CLOSE_LOST_BEFORE_ADOPT = "close-lost-call-before-adopt"
# Histories from before model steps ran on any worker (with CHECKPOINT on the
# sandbox's worker beside them) replay with the step on the session's worker.
_BRAIN_ANYWHERE = "brain-anywhere"


def _is_placement_lost(err: BaseException) -> bool:
    return (
        isinstance(err, ActivityError)
        and isinstance(err.cause, ActivityTimeout)
        and err.cause.type is TimeoutType.SCHEDULE_TO_START
    )


def _domain_error(err: ActivityError) -> ApplicationError:
    """Surface an activity's domain error unchanged so the API maps it to the same status."""
    cause = err.cause
    if isinstance(cause, ApplicationError):
        return ApplicationError(cause.message, *cause.details, type=cause.type, non_retryable=True)
    return ApplicationError(str(err), type="ActivityFailed", non_retryable=True)


def _is_domain(err: ActivityError) -> bool:
    """A final domain answer from the activity, as opposed to a crash or timeout."""
    return isinstance(err.cause, ApplicationError) and err.cause.non_retryable


def _failure(err: ActivityError) -> tuple[str, str]:
    cause = err.cause
    if isinstance(cause, ApplicationError):
        return cause.type or "ActivityFailed", cause.message
    return type(cause or err).__name__, str(cause or err)


def _guard(settings: GuardSettings | None) -> LoopGuard | None:
    if settings is None:
        return None
    guard = LoopGuard()
    guard.configure(
        "run",
        GuardConfig(
            repetition=GuardAction(settings.repetition),
            oscillation=GuardAction(settings.oscillation),
            stall_iterations=settings.stall_iterations,
        ),
    )
    return guard


@workflow.defn(name="AgentSession")
class AgentSessionWorkflow:
    @workflow.init
    def __init__(self, inp: SessionInput) -> None:
        self._session_id = inp.session_id
        self._timing = inp.timing
        self._placement_timeout = timedelta(seconds=inp.timing.placement_timeout_s)
        self._heartbeat = timedelta(seconds=inp.timing.heartbeat_timeout_s)
        self._state = inp.carried or Carried()
        self._running: Message | None = None
        self._turn: asyncio.Task[object] | None = None
        self._terminate_requested = False
        self._abort_requested = False
        self._creation_error: ApplicationError | None = None
        self._submit_lock = asyncio.Lock()
        # Approvals this workflow is waiting on (stepped turns), and their decisions.
        self._awaiting: dict[str, ApprovalNeeded] = {}
        self._decisions: dict[str, str] = {}

    # --- main loop -----------------------------------------------------------

    @workflow.run
    async def run(self, inp: SessionInput) -> SessionResult:
        try:
            if self._state.phase is Phase.CREATING:
                await self._create(inp)
            while self._state.phase is not Phase.FAILED:
                await self._wait_for_work()
                if self._terminate_requested:
                    break
                await self._run_turn(self._state.pending.pop(0))
                if workflow.info().is_continue_as_new_suggested():
                    await workflow.wait_condition(workflow.all_handlers_finished)
                    if not self._terminate_requested:
                        workflow.continue_as_new(
                            SessionInput(self._session_id, carried=self._state, timing=self._timing)
                        )
        except asyncio.CancelledError:
            # Workflow cancelled from outside (e.g. the Temporal UI): clean up like terminate.
            await self._terminate()
            raise
        if self._state.phase is not Phase.FAILED:
            await self._flush_capture()  # a pending burst is not lost with the session
            await self._terminate()
        for job_id in list(self._state.jobs):  # a closed session's jobs stop with it
            await self._cancel_job(job_id)
        await workflow.wait_condition(workflow.all_handlers_finished)
        return SessionResult(self._session_id, self._state.phase, self._state.turns_completed)

    async def _create(self, inp: SessionInput) -> None:
        arg: CreateRequest | ForkRequest
        if inp.fork is not None:
            activity, arg = Activity.FORK, ForkRequest(self._session_id, inp.fork)
        else:
            assert inp.create is not None, "a new session workflow needs CreateSession or ForkSpec"
            activity, arg = Activity.CREATE, CreateRequest(self._session_id, inp.create)
        try:
            self._state.placement = await workflow.execute_activity(
                activity,
                arg,
                result_type=Placement,
                start_to_close_timeout=_SHORT,
                retry_policy=_RETRY,
            )
            self._state.phase = Phase.IDLE
        except ActivityError as err:
            self._creation_error = _domain_error(err)
            self._state.phase = Phase.FAILED

    async def _run_turn(self, message: Message) -> None:
        self._running, self._state.phase = message, Phase.RUNNING
        try:
            plan = await self._session_activity(
                Activity.BEGIN_RUN, TurnRequest(self._session_id, message), RunPlan
            )
            outcome = await (
                self._run_steps(message, plan) if plan.stepped else self._run_whole(message)
            )
            if plan.capture is not None and outcome.stop_reason == "stop":
                await self._queue_capture(plan.run_id, plan.capture)
            if outcome.session_status in CLOSED_STATUSES:
                # Policy closed the session (e.g. a loop guard): nothing more can run.
                workflow.logger.info("session closed by policy: %s", outcome.stop_reason)
                self._state.phase = Phase.FAILED
        except (ActivityError, asyncio.CancelledError) as err:
            aborted = is_cancelled_exception(err) and self._abort_requested
            if isinstance(err, asyncio.CancelledError) and not aborted:
                raise  # the workflow itself is being cancelled
            if not aborted:
                # The activities journal the failure for clients; the session stays usable.
                workflow.logger.warning("turn %s failed: %s", message.command_id, err)
        finally:
            self._running, self._turn, self._abort_requested = None, None, False
            self._state.turns_completed += 1
            if self._state.phase is Phase.RUNNING:
                self._state.phase = Phase.IDLE

    # --- automatic memory capture (QM per-turn bursts, on durable timers) --------

    async def _wait_for_work(self) -> None:
        """Wait for a message or terminate; flush a capture burst when its quiet ends."""
        while True:
            due = self._state.capture_due
            timeout = (
                None
                if due is None
                else timedelta(seconds=max(0.0, due - workflow.now().timestamp()))
            )
            try:
                await workflow.wait_condition(
                    lambda: bool(self._state.pending) or self._terminate_requested,
                    timeout=timeout,
                )
                return
            except TimeoutError:
                await self._flush_capture()

    async def _queue_capture(self, run_id: str, config: CaptureConfig) -> None:
        runs = self._state.capture_runs
        if run_id not in runs:
            runs.append(run_id)
        if len(runs) >= config.max_turns:
            await self._flush_capture()
        else:  # every new turn restarts the quiet window
            self._state.capture_due = workflow.now().timestamp() + config.quiet_s

    async def _flush_capture(self) -> None:
        runs, self._state.capture_runs, self._state.capture_due = (
            list(self._state.capture_runs),
            [],
            None,
        )
        if not runs:
            return
        try:
            await workflow.execute_activity(
                Activity.CAPTURE_MEMORY,
                CaptureRequest(self._session_id, runs),
                result_type=int,
                task_queue=workflow.info().task_queue,  # journal-only: any worker
                start_to_close_timeout=_MODEL_CALL,
                heartbeat_timeout=self._heartbeat,
                retry_policy=_RETRY,
            )
        except ActivityError as err:
            # Capture is best effort: a failed burst is dropped, never blocks the session.
            workflow.logger.warning("memory capture failed for %d turns: %s", len(runs), err)

    async def _run_whole(self, message: Message) -> TurnOutcome:
        """Runtime-driven turn (harness, echo, offline githi): one heartbeated activity."""
        return await self._session_activity(
            Activity.RUN_TURN,
            TurnRequest(self._session_id, message),
            TurnOutcome,
            start_to_close=timedelta(seconds=self._timing.turn_timeout_s),
            retry=_TURN_RETRY,
            heartbeat=True,
            track=True,
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    # --- stepped (workflow-driven) turns --------------------------------------

    async def _run_steps(self, message: Message, plan: RunPlan) -> TurnOutcome:
        """Model call, then each tool call, until the model answers or policy stops it."""
        guard = _guard(plan.guard)
        in_flight: str | None = None

        async def end(stop_reason: str, **details: Any) -> TurnOutcome:
            req = EndRunRequest(
                self._session_id, plan.run_id, message.command_id, stop_reason, **details
            )
            lost = (
                LostCall(self._session_id, plan.run_id, req.lost_call_id, req.lost_reason)
                if req.lost_call_id is not None
                else None
            )
            # If the session's worker is gone, its lost call is closed before adoption.
            return await self._session_activity(Activity.END_RUN, req, TurnOutcome, lost=lost)

        try:
            for step in range(plan.max_steps):
                result = await self._model_step(StepRequest(self._session_id, plan.run_id, step))
                if result.final:
                    return await end(result.stop_reason)
                for call in result.calls:
                    if guard is not None:
                        verdict = guard.observe("run", call.name, call.args_hash)
                        if verdict.tripped and verdict.action is GuardAction.ABORT:
                            # The runaway run ends; the conversation does not (F32).
                            return await end(
                                "error",
                                error_class="LoopGuard",
                                detail=verdict.detail or "loop detected",
                            )
                    approved = call.approval is None or await self._approve(plan.run_id, call)
                    in_flight = call.call_id
                    try:
                        await self._call_tool(plan.run_id, call, denied=not approved)
                    except ActivityError as err:
                        if is_cancelled_exception(err):
                            raise
                        # The tool may or may not have run: never guess, stop the turn.
                        return await end(
                            "interrupted",
                            error_class="TurnInterrupted",
                            detail=f"tool {call.name!r} did not complete: {_failure(err)[1]}",
                            lost_call_id=call.call_id,
                        )
                    in_flight = None
            return await end(
                "error",
                error_class="AgentLoopError",
                detail=f"no answer after {plan.max_steps} model steps",
            )
        except (ActivityError, asyncio.CancelledError) as err:
            if is_cancelled_exception(err) and self._abort_requested:
                return await end(
                    "aborted",
                    lost_call_id=in_flight,
                    lost_reason="the run was aborted while the tool ran",
                )
            if isinstance(err, ActivityError):
                kind, detail = _failure(err)
                return await end("error", error_class=kind, detail=detail)
            raise

    async def _model_step(self, req: StepRequest) -> StepResult:
        """The brain thinks on any worker while the sandbox's worker checkpoints.

        A model step needs no sandbox, so it goes to the shared queue: no worker
        holding sandboxes is a bottleneck for thinking, and a session whose worker
        died keeps thinking while it is adopted. The checkpoint of what the last
        tools changed runs beside it on the sandbox's worker; both finish before
        the next tool starts (Crab's barrier, without making the model wait).
        """
        if not workflow.patched(_BRAIN_ANYWHERE):
            return await self._session_activity(
                Activity.MODEL_STEP,
                req,
                StepResult,
                start_to_close=_MODEL_CALL,
                heartbeat=True,
                track=True,
            )
        saving = asyncio.ensure_future(
            self._session_activity(Activity.CHECKPOINT, req.session_id, type(None))
        )
        thinking = workflow.start_activity(
            Activity.MODEL_STEP,
            dataclasses.replace(req, checkpoint=False),
            result_type=StepResult,
            task_queue=workflow.info().task_queue,
            start_to_close_timeout=_MODEL_CALL,
            heartbeat_timeout=self._heartbeat,
            retry_policy=_RETRY,
            cancellation_type=workflow.ActivityCancellationType.TRY_CANCEL,
        )
        self._turn = thinking
        try:
            return await thinking
        finally:
            try:
                await saving
            except ActivityError as err:
                # A lost checkpoint costs a reset's restore, never the turn.
                workflow.logger.warning("checkpoint failed: %s", err)

    async def _call_tool(self, run_id: str, call: PlannedCall, *, denied: bool) -> None:
        once = call.retry is RetryMode.ONCE and not denied
        await self._session_activity(
            Activity.TOOL_CALL,
            ToolRequest(self._session_id, run_id, call.call_id, denied=denied),
            bool,
            start_to_close=timedelta(seconds=self._timing.turn_timeout_s),
            retry=_ONCE if once else _RETRY,
            heartbeat=True,
            track=True,
            # An at-most-once tool whose worker died is not re-run by the adopting worker.
            rerun_after_adopt=not once,
            lost=LostCall(self._session_id, run_id, call.call_id) if once else None,
        )

    async def _approve(self, run_id: str, call: PlannedCall) -> bool:
        """Wait durably for a human; on timeout apply the manifest's on_timeout."""
        needed = call.approval
        assert needed is not None
        approval_id = await self._session_activity(
            Activity.REQUEST_APPROVAL,
            ApprovalAsk(self._session_id, run_id, call.call_id, call.name, needed),
            str,
        )
        self._awaiting[approval_id] = needed
        try:
            with contextlib.suppress(TimeoutError):  # a durable timer: survives any restart
                await workflow.wait_condition(
                    lambda: approval_id in self._decisions or self._abort_requested,
                    timeout=timedelta(seconds=needed.timeout_s),
                )
        finally:
            self._awaiting.pop(approval_id, None)
        if approval_id not in self._decisions:
            # Timed out, or aborted: settle it so it does not stay pending.
            choice = "deny" if self._abort_requested else needed.on_timeout
            self._decisions[approval_id] = await self._session_activity(
                Activity.RESOLVE_APPROVAL,
                ApprovalDecision(self._session_id, approval_id, choice, timed_out=True),
                str,
            )
        if self._abort_requested:
            raise asyncio.CancelledError
        return is_allow_choice(self._decisions.pop(approval_id))

    async def _terminate(self) -> None:
        if self._state.phase in (Phase.TERMINATED, Phase.FAILED):
            return
        if self._turn is not None and not self._turn.done():
            self._turn.cancel()
        await self._session_activity(Activity.TERMINATE, self._session_id, type(None))
        self._state.phase = Phase.TERMINATED

    # --- placement -----------------------------------------------------------

    async def _session_activity[T](
        self,
        activity: Activity,
        arg: object,
        result_type: type[T],
        *,
        start_to_close: timedelta = _SHORT,
        retry: RetryPolicy = _RETRY,
        heartbeat: bool = False,
        track: bool = False,
        rerun_after_adopt: bool = True,
        lost: LostCall | None = None,
        cancellation_type: workflow.ActivityCancellationType = (
            workflow.ActivityCancellationType.TRY_CANCEL
        ),
    ) -> T:
        """Run an activity on the worker holding the session's runtime."""

        def start(queue: str) -> asyncio.Task[T]:
            return workflow.start_activity(
                activity,
                arg,
                result_type=result_type,
                task_queue=queue,
                schedule_to_start_timeout=self._placement_timeout,
                start_to_close_timeout=start_to_close,
                heartbeat_timeout=self._heartbeat if heartbeat else None,
                retry_policy=retry,
                cancellation_type=cancellation_type,
            )

        return await self._on_session_worker(
            start, track=track, rerun_after_adopt=rerun_after_adopt, lost=lost
        )

    async def _on_session_worker[T](
        self,
        start: Callable[[str], asyncio.Task[T]],
        *,
        track: bool = False,
        rerun_after_adopt: bool = True,
        lost: LostCall | None = None,
    ) -> T:
        """Run on the worker holding the session; adopt it elsewhere if that worker is gone.

        ``lost`` is the call this activity would leave half done: it is closed in the
        journal first, so the adopting worker's recovery finds no dangling tool.
        """
        while True:
            queue = (
                self._state.placement.task_queue
                if self._state.placement is not None
                else workflow.info().task_queue
            )
            task = start(queue)
            if track:
                self._turn = task
            try:
                return await task
            except ActivityError as err:
                if not _is_placement_lost(err) or self._state.placement is None:
                    raise
                workflow.logger.warning("session worker %s lost; adopting", queue)
                if lost is not None and workflow.patched(_CLOSE_LOST_BEFORE_ADOPT):
                    await workflow.execute_activity(
                        Activity.CLOSE_LOST_CALL,
                        lost,
                        task_queue=workflow.info().task_queue,
                        start_to_close_timeout=_SHORT,
                        retry_policy=_RETRY,
                    )
                self._state.placement = await workflow.execute_activity(
                    Activity.ADOPT,
                    self._session_id,
                    result_type=Placement,
                    start_to_close_timeout=_SHORT,
                    retry_policy=_RETRY,
                )
                if not rerun_after_adopt:
                    raise

    # --- messages ------------------------------------------------------------

    @workflow.update
    async def wait_ready(self) -> SessionStatus:
        await workflow.wait_condition(lambda: self._state.phase is not Phase.CREATING)
        if self._creation_error is not None:
            raise self._creation_error
        return self.status()

    @workflow.update
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
                await self._session_activity(Activity.PREPARE_TURN, self._session_id, type(None))
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

    @workflow.update
    async def session_op(self, op: SessionOp) -> OpResult:
        """Tool invoke, memory, subagents, workspace: on the worker holding the session."""
        await workflow.wait_condition(lambda: self._state.phase is not Phase.CREATING)
        if self._creation_error is not None:
            raise self._creation_error
        if op.kind is Op.INTERRUPT_CHILD and op.args.get("child_id") in self._state.jobs:
            await self._cancel_job(op.args["child_id"])  # a durable background job
            return OpResult()
        lost = LostCall(self._session_id, None, op.op_id) if op.kind is Op.INVOKE_TOOL else None
        try:
            result = await self._session_activity(
                Activity.SESSION_OP,
                op,
                OpResult,
                start_to_close=timedelta(seconds=self._timing.turn_timeout_s),
                # Side effects run at most once; a placement loss (never started) re-runs.
                retry=_RETRY if op.kind in RETRYABLE_OPS else _ONCE,
                heartbeat=True,
                lost=lost,
            )
        except ActivityError as err:
            if lost is not None and not _is_domain(err):
                # The worker died (or failed) mid-invoke: say so, and never re-run it.
                await workflow.execute_activity(
                    Activity.CLOSE_LOST_CALL,
                    lost,
                    task_queue=workflow.info().task_queue,
                    start_to_close_timeout=_SHORT,
                    retry_policy=_RETRY,
                )
                raise ApplicationError(
                    f"tool call {op.op_id!r} outcome unknown: {_failure(err)[1]}",
                    type="ToolOutcomeUnknown",
                    non_retryable=True,
                ) from err
            raise _domain_error(err) from err
        if op.kind is Op.START_BACKGROUND:
            await self._start_job(op, provider=(result.body or {}).get("provider"))
        return result

    # --- background subagent jobs ----------------------------------------------

    async def _start_job(self, op: SessionOp, *, provider: str | None) -> None:
        """Run the job journalled by ``op`` as its own workflow, owned by this session.

        ``provider`` is the one the journalling activity resolved (the session's
        pinned default when the request named none).
        """
        job_id, run_id = job_ids(op.op_id)
        args = dict(op.args)
        named = args.pop("provider", None)
        provider = str(provider or named or "memory")  # "memory": histories from before
        spec = JobSpec(self._session_id, job_id, run_id, provider, args, self._timing)
        try:
            await workflow.start_child_workflow(
                SubagentJobWorkflow.run,
                spec,
                id=job_workflow_id(self._session_id, job_id),
                task_queue=workflow.info().task_queue,
                # Outlives this run's continue-as-new; the session cancels it explicitly.
                parent_close_policy=ParentClosePolicy.ABANDON,
                # A re-sent start never runs a job twice, even one that already ended.
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return  # the same op, re-sent (e.g. after continue-as-new): already running
        self._state.jobs.append(job_id)

    async def _cancel_job(self, job_id: str) -> None:
        with contextlib.suppress(Exception):  # it may have just ended
            await workflow.get_external_workflow_handle(
                job_workflow_id(self._session_id, job_id)
            ).cancel()

    @workflow.signal(name=START_JOB_SIGNAL)
    async def start_job(self, start: StartJob) -> None:
        """A background subagent the model started from a tool call (already journalled)."""
        await self._start_job(start.op, provider=start.provider)

    @workflow.signal(name=JOB_FINISHED_SIGNAL)
    def job_finished(self, job_id: str) -> None:
        with contextlib.suppress(ValueError):
            self._state.jobs.remove(job_id)

    @session_op.validator
    def _validate_session_op(self, op: SessionOp) -> None:
        self._reject_if_closed()
        if op.session_id != self._session_id or not op.op_id:
            raise ValueError("op must name this session and carry an op_id")

    @workflow.update
    async def respond_approval(self, choice: ApprovalChoice) -> None:
        try:
            if choice.approval_id in self._awaiting:
                # A stepped turn is waiting on this one: settle it, then wake the turn.
                self._decisions[choice.approval_id] = await self._session_activity(
                    Activity.RESOLVE_APPROVAL,
                    ApprovalDecision(
                        self._session_id,
                        choice.approval_id,
                        choice.choice,
                        responded_by=choice.responded_by,
                    ),
                    str,
                )
                return
            # A runtime-driven approval: deliver it to the runtime holding the request.
            await self._session_activity(
                Activity.RESPOND_APPROVAL,
                ApprovalRequest(self._session_id, choice),
                type(None),
            )
        except ActivityError as err:
            raise _domain_error(err) from err

    @respond_approval.validator
    def _validate_approval(self, choice: ApprovalChoice) -> None:
        self._reject_if_closed()
        if not choice.approval_id or not choice.choice:
            raise ValueError("approval_id and choice are required")
        needed = self._awaiting.get(choice.approval_id)
        if needed is not None and choice.choice not in needed.options:
            raise ApplicationError(
                f"choice must be one of {needed.options}", type="InvalidApprovalChoice"
            )

    @workflow.signal(name="abort")
    def abort(self) -> None:
        if self._running is None:
            return
        self._abort_requested = True  # also wakes a turn waiting on an approval
        if self._turn is not None and not self._turn.done():
            self._turn.cancel()

    @workflow.update
    async def terminate(self) -> SessionStatus:
        self._terminate_requested = True
        await workflow.wait_condition(lambda: self._state.phase in (Phase.TERMINATED, Phase.FAILED))
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
