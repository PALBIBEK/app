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
from datetime import datetime, timedelta
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
    COMPACT,
    JOB_FINISHED_SIGNAL,
    MAX_MESSAGE_CHARS,
    RETRY,
    RETRYABLE_OPS,
    START_JOB_SIGNAL,
    STOP_RUN_SIGNAL,
    SUBMIT_SIGNAL,
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
    Compacted,
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
    PlaceInputs,
    Placement,
    PlannedCall,
    RetryMode,
    RunPhase,
    RunPlan,
    RunState,
    SessionInput,
    SessionOp,
    SessionResult,
    SessionStatus,
    StartJob,
    StepRequest,
    StepResult,
    ToolBatch,
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
    from supervisor.policy.types import GuardDetector, GuardVerdict, is_allow_choice
    from supervisor.temporal.policies import RETRIES, Retry, policy

SEEN_COMMANDS_WINDOW = 1_000

# Each step's time limit, retries and heartbeat come from one table (supervisor.temporal.policies).
_ONCE = RETRIES[Retry.ONCE]
# Patch id (workflow.patched): histories recorded before lost calls were closed
# ahead of adoption replay without the extra activity.
_CLOSE_LOST_BEFORE_ADOPT = "close-lost-call-before-adopt"
# Histories from before model steps ran on any worker (with CHECKPOINT on the
# sandbox's worker beside them) replay with the step on the session's worker.
_BRAIN_ANYWHERE = "brain-anywhere"
# Histories from before a successful new call counted as progress replay with the
# stall guard counting every call.
_STALL_COUNTS_PROGRESS = "stall-counts-progress"
# Runs recorded before the run loop became githi's phase machine replay through _run_steps.
_PHASE_LOOP = "phase-loop"
# A round asks for every approval first, then runs its read-only calls together.
_TOOL_ROUND = "tool-round"
# A steering message waits for the round to finish (pi), instead of skipping its rest.
_STEER_AFTER_ROUND = "steer-after-round"
# A round's calls run at once, each its own activity (pi, pi-temporal's stepped mode), and a
# call that is lost or fails is answered as unknown while the run goes on.
_PARALLEL_ROUND = "parallel-round"
# Patch id: a stop (an abort, the hard deadline) that lands while nothing cancellable
# runs ends the run before its next step. Before it, the next step still ran.
_STOP_BETWEEN_STEPS = "stop-between-steps"
# A round's calls run at most this many at once (each is an activity slot on the
# session's worker); the rest wait their turn, in the model's order.
ROUND_CONCURRENCY = 8
_ROUND_LIMIT = "round-limit"
# A session whose worker misses its placement limit but still beats is waiting behind
# that worker's own work: it waits again (up to this many times) instead of moving.
_BUSY_WAITS = 5
_ADOPT_ONLY_GONE = "adopt-only-gone"
# A stall is judged by the rounds before, not by the round's own calls (``_round_verdict``).
_STALL_BY_ROUND = "stall-by-round"
# An in-run compaction reports what its summary spent (session.compact_counted).
_COMPACTION_COUNTED = "compaction-counted"
# Patch id prefixes for signals added after release (see ``_handled_here``).
_HELD_MESSAGE = "held-message"
_STOP_RUN = "stop-run"
# Steps that find the session's worker gone together adopt it once (a parallel round's
# calls, or a checkpoint beside a model step); histories before it adopted per step.
_ONE_ADOPTION = "one-adoption"


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


def _in_lanes(
    calls: list[PlannedCall],
    start: Callable[[PlannedCall, list[asyncio.Future[bool]]], asyncio.Future[bool]],
) -> list[asyncio.Future[bool]]:
    """Start every call, each after the calls in its lane it must not overtake.

    A read waits for the change before it; a change waits for that and for the reads
    since, so each sees the file as the model's order has it (pi's file mutation queue,
    reads included). A call with no lane waits for nothing.
    """
    changed: dict[str, asyncio.Future[bool]] = {}
    reads: dict[str, list[asyncio.Future[bool]]] = {}
    tasks: list[asyncio.Future[bool]] = []
    for call in calls:
        lane = call.lane
        before: list[asyncio.Future[bool]] = []
        if lane is not None:
            before = [changed[lane]] if lane in changed else []
            if not call.parallel:
                before += reads.pop(lane, [])
        task = start(call, before)
        if lane is not None:
            if call.parallel:
                reads.setdefault(lane, []).append(task)
            else:
                changed[lane] = task
        tasks.append(task)
    return tasks


def _deadline_details(plan: RunPlan, step: int) -> dict[str, str]:
    return {
        "error_class": "RunDeadline",
        "detail": (
            f"the run's hard time limit ({plan.hard_run_s:g} s) was reached and it was "
            f"stopped where it was, after {step} model steps; ask the agent to continue"
        ),
    }


@dataclasses.dataclass(frozen=True, slots=True)
class _Used:
    """What a run and its session have used so far (the workflow's clock and counts, so a
    replay decides the same, whichever worker ran each step)."""

    run_s: float
    run_tokens: int
    session_s: float
    session_tokens: int


def _run_bound(plan: RunPlan, step: int, used: _Used) -> tuple[str, str] | None:
    """The bound a run is past before its next model step, as (error class, detail); None
    within them all. The session's counts survive continue-as-new."""
    if plan.max_run_s is not None and used.run_s > plan.max_run_s:
        return "RunTimeLimit", (
            f"the run's time limit ({plan.max_run_s:g} s) was reached after "
            f"{step} model steps; ask the agent to continue"
        )
    if plan.max_session_s is not None and used.session_s > plan.max_session_s:
        return "SessionTimeLimit", (
            f"the session's runs have taken {used.session_s:.0f} s, past its limit "
            f"({plan.max_session_s:g} s); start a new session or raise the limit"
        )
    if plan.max_session_tokens is not None and used.session_tokens > plan.max_session_tokens:
        return "SessionTokenLimit", (
            f"the session has used {used.session_tokens} tokens, past its limit "
            f"({plan.max_session_tokens}); start a new session or raise the limit"
        )
    if plan.max_run_tokens is not None and used.run_tokens > plan.max_run_tokens:
        return "RunTokenLimit", (
            f"the run used {used.run_tokens} tokens, past its limit ({plan.max_run_tokens}); "
            "ask the agent to continue"
        )
    if step >= plan.max_steps:
        return "AgentLoopError", (
            f"the step limit ({plan.max_steps} model steps) was reached "
            "without an answer; ask the agent to continue"
        )
    return None


def _round_verdict(guard: LoopGuard | None, calls: list[PlannedCall]) -> GuardVerdict | None:
    """The loop guard's verdict that stops a round before any of its calls runs, if any.

    The guard sees every call of the round first. A stall (calls that get nowhere) is
    judged by the rounds before: a round's own calls have not run yet, so none of them can
    have got anywhere, and a wide round (a dozen files read at once) is not a loop.
    """
    if guard is None:
        return None
    for i, call in enumerate(calls):
        verdict = guard.observe("run", call.name, call.args_hash)
        if not verdict.tripped or verdict.action is not GuardAction.ABORT:
            continue
        if i > 0 and verdict.detector is GuardDetector.STALL and workflow.patched(_STALL_BY_ROUND):
            continue
        return verdict
    return None


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
        self._state = inp.carried or Carried()
        self._running: Message | None = None
        # The run a steering message joins: set while a phase-loop run is working.
        self._steerable: str | None = None
        self._turn: asyncio.Task[object] | None = None
        # Every tracked step running now (a parallel round runs several): an abort stops all.
        # In the order started (a dict), so an abort cancels them in an order replay repeats.
        self._tracked: dict[asyncio.Task[Any], None] = {}
        # At-most-once calls running now: closed as unknown before the session is adopted,
        # so the adopting worker's recovery finds no half-done call (and does not fail it).
        self._running_once: dict[str, LostCall] = {}
        # Calls of one round can find the session's worker gone together: one adopts it.
        self._adopting = asyncio.Lock()
        self._terminate_requested = False
        self._abort_requested = False
        # The run's hard deadline passed (loop.hard_run_seconds): stopped like an abort.
        self._deadline_hit = False
        self._creation_error: ApplicationError | None = None
        self._submit_lock = asyncio.Lock()
        self._signals_seen: dict[str, int] = {}  # per kind, for ``_handled_here``
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
        err = self._creation_error
        return SessionResult(
            self._session_id, self._state.phase, self._state.turns_completed,
            error=err.message if err is not None else None,
            error_kind=err.type if err is not None else None,
        )

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
                **self._options(activity),
            )
            self._state.phase = Phase.IDLE
        except ActivityError as err:
            self._creation_error = _domain_error(err)
            self._state.phase = Phase.FAILED

    async def _run_turn(self, message: Message) -> None:
        self._running, self._state.phase = message, Phase.RUNNING
        try:
            plan = await self._session_activity(
                Activity.BEGIN_RUN,
                TurnRequest(
                    self._session_id,
                    message,
                    tokens_used=self._state.tokens_used,
                    seconds_used=self._state.seconds_used,
                ),
                RunPlan,
            )
            outcome = await (
                self._run_whole(message)
                if not plan.stepped
                # Histories recorded before the phase machine replay through the old loop.
                else self._run_phases(message, plan)
                if workflow.patched(_PHASE_LOOP)
                else self._run_steps(message, plan)
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
            self._deadline_hit = False
            self._steerable = None
            if self._state.steering:
                # Arrived as the run ended: they run next, in order (pi's follow-ups).
                self._state.pending[:0] = self._state.steering
                self._state.steering = []
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
                **self._options(Activity.CAPTURE_MEMORY),
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
            track=True,
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    # --- stepped (workflow-driven) turns --------------------------------------

    async def _run_phases(self, message: Message, plan: RunPlan) -> TurnOutcome:
        """githi's turn as phases over a small checkpoint (Pi Durable's generation task).

        REQUEST runs one model call; a reply with tool calls moves to TOOLS, which answers
        each call (guard, approval, tool step) and returns to REQUEST for the next step.
        The model's answer, a policy stop or the step limit ends the run. Every decision is
        here, in replayed code; the steps it runs only do their effect and journal it.
        """
        guard = _guard(plan.guard)
        st = RunState()
        self._steerable = plan.run_id
        started = workflow.now()
        self._spend(st, plan.tokens)  # opening the run (a compaction between turns)

        async def end(stop_reason: str, **details: Any) -> TurnOutcome:
            self._steerable = None  # a message sent from here on runs after this run
            return await self._end_run(message, plan, stop_reason, **details)

        async def halted(**details: Any) -> TurnOutcome:
            """The run was stopped where it was: by the user, or by its hard deadline."""
            if self._deadline_hit:
                return await end("error", **_deadline_details(plan, st.step), **details)
            return await end("aborted", **details)

        deadline = self._start_deadline(plan.hard_run_s)

        try:
            while True:
                if self._stopped_between_steps() or (
                    st.phase is RunPhase.REQUEST and await self._place_steering(plan, st)
                ):
                    return await halted(**self._unanswered(st))
                if st.phase is RunPhase.REQUEST:
                    if (bound := _run_bound(plan, st.step, self._used(st, started))) is not None:
                        return await end("error", error_class=bound[0], detail=bound[1])
                    req = StepRequest(
                        self._session_id, plan.run_id, st.step,
                        can_compact=True, compacted=st.compacted,
                        can_retry=True, attempt=st.attempt,
                    )
                    result = await self._model_step(req)
                    self._spend(st, result.tokens)
                    if result.kind == RETRY:
                        # pi's auto-retry, on a durable timer: no worker is held while it
                        # waits, a restart resumes the wait, and an abort ends it.
                        try:
                            await workflow.wait_condition(
                                lambda: self._abort_requested,
                                timeout=timedelta(seconds=result.delay_s),
                            )
                        except TimeoutError:
                            pass
                        if self._abort_requested:
                            return await halted()
                        st.attempt += 1
                        continue
                    if result.kind == COMPACT:
                        # githi's prepare phase: the request does not fit the window. Compact
                        # the conversation so far, then ask again; at most once per step.
                        if st.compacted or not await self._compact(req, st):
                            return await end(
                                "error",
                                error_class="ContextWindowFull",
                                detail=(
                                    "the conversation does not fit the model's context window, "
                                    "even after compacting it; start a new session or pin a "
                                    "model with a larger window"
                                ),
                            )
                        st.compacted = True
                        continue
                    if result.final and result.stop_reason == "stop" and self._state.steering:
                        # The user spoke while the model answered: the run goes on with it.
                        st.step, st.compacted, st.attempt = st.step + 1, False, 0
                        continue
                    if result.final:
                        return await end(result.stop_reason)
                    st.calls, st.phase = list(result.calls), RunPhase.TOOLS
                    st.lanes = result.lanes
                else:
                    stopped = await (
                        self._parallel_round(plan, st, guard, end)
                        # A step planned by an older worker has no lanes: it runs in order.
                        if workflow.patched(_PARALLEL_ROUND) and st.lanes
                        else self._round(plan, st, guard, end)
                        if workflow.patched(_TOOL_ROUND)
                        else self._tool_round(plan, st, guard, end)
                    )
                    if stopped is not None:
                        return stopped
                    st.step, st.calls, st.phase = st.step + 1, [], RunPhase.REQUEST
                    st.compacted, st.attempt = False, 0
        except (ActivityError, asyncio.CancelledError) as err:
            if is_cancelled_exception(err) and self._abort_requested:
                return await halted(
                    lost_call_id=st.in_flight,
                    lost_reason=(
                        "the run's hard time limit stopped it while the tool ran"
                        if self._deadline_hit
                        else "the run was aborted while the tool ran"
                    ),
                    **({"open_call_ids": list(st.open)} if st.open else {}),
                )
            if isinstance(err, ActivityError):
                kind, detail = _failure(err)
                return await end(
                    "error",
                    error_class=kind,
                    detail=detail,
                    **(
                        {"open_call_ids": list(st.open), "lost_reason": f"the run ended ({kind})"}
                        if st.open
                        else {}
                    ),
                )
            raise
        finally:
            if deadline is not None and not deadline.done():
                deadline.cancel()
            self._state.seconds_used += (workflow.now() - started).total_seconds()

    def _used(self, st: RunState, started: datetime) -> _Used:
        run_s = (workflow.now() - started).total_seconds()
        return _Used(run_s, st.tokens, self._state.seconds_used + run_s, self._state.tokens_used)

    def _spend(self, st: RunState, tokens: int) -> None:
        """Count what a model call of the run spent, toward the run's and the session's bounds."""
        st.tokens += tokens
        self._state.tokens_used += tokens

    async def _compact(self, req: StepRequest, st: RunState) -> bool:
        """Compact the conversation inside the run; whether it shrank. What the summary
        spent counts like a model step (histories from before that compact uncounted)."""
        if not workflow.patched(_COMPACTION_COUNTED):
            return await self._session_activity(Activity.COMPACT, req, bool, track=True)
        done = await self._session_activity(Activity.COMPACT_COUNTED, req, Compacted, track=True)
        self._spend(st, done.tokens)
        return done.ok

    def _unanswered(self, st: RunState) -> dict[str, Any]:
        """What a stop between steps leaves without a result: the round the model just
        asked for, none of it started (END_RUN answers each as not run)."""
        if st.phase is not RunPhase.TOOLS or not st.calls:
            return {}
        why = "the run's hard time limit stopped it" if self._deadline_hit else "the run was aborted"
        return {"open_call_ids": [c.call_id for c in st.calls], "lost_reason": f"{why} before the call ran"}

    def _stopped_between_steps(self) -> bool:
        """Stopped while nothing it could cancel ran (between steps, placing the user's
        words, compacting): the run ends before its next step starts."""
        return self._abort_requested and workflow.patched(_STOP_BETWEEN_STEPS)

    async def _place_steering(self, plan: RunPlan, st: RunState) -> bool:
        """What the user said while the run worked joins it before its next step; whether
        the run was stopped meanwhile."""
        if not self._state.steering:
            return False
        steering, self._state.steering = list(self._state.steering), []
        await self._session_activity(
            Activity.PLACE_INPUTS,
            PlaceInputs(self._session_id, plan.run_id, st.step, steering),
            type(None),
        )
        return self._stopped_between_steps()

    def _start_deadline(self, seconds: float | None) -> asyncio.Task[None] | None:
        """pi-temporal's hard deadline: a durable timer that stops the run mid-call."""
        if seconds is None:
            return None

        async def expire() -> None:
            await workflow.sleep(seconds)
            if self._abort_requested:
                return  # the user stopped it first: it ends as aborted
            self._deadline_hit = True
            self._stop_run()

        return asyncio.ensure_future(expire())

    async def _parallel_round(
        self,
        plan: RunPlan,
        st: RunState,
        guard: LoopGuard | None,
        end: Callable[..., Any],
    ) -> TurnOutcome | None:
        """The step's calls at once, as pi runs them (pi-temporal's stepped mode).

        The loop guard sees every call and every approval is asked first, as before. Then
        each call is its own tool step, all started together; a call waits only for the
        earlier ones in its lane that change something (the same file, or the platform
        tools), so a read sees the write before it. A call that is lost or fails is
        answered as unknown (or as not run, if nothing started it) and the run goes on:
        the model reads that and checks; a change queued behind a lost change in its lane
        is not run. A stop (an abort, the loop guard, an approval wait cut short) leaves no
        call of the round unanswered: END_RUN answers each that has no result.
        """
        denied: set[str] = set()
        unknown: set[str] = set()  # lanes where a change's outcome is unknown
        st.open = [c.call_id for c in st.calls]
        if (verdict := _round_verdict(guard, st.calls)) is not None:
            return await end(
                "error",
                error_class="LoopGuard",
                detail=verdict.detail or "loop detected",
                open_call_ids=list(st.open),
                lost_reason="the loop guard stopped the run",
            )
        for call in st.calls:
            if call.approval is not None and not await self._approve(plan.run_id, call):
                denied.add(call.call_id)
        # One session's round takes at most a few of its worker's activity slots, so the
        # worker's other sessions keep moving (a starved session looks like a lost worker).
        slots = asyncio.Semaphore(ROUND_CONCURRENCY) if workflow.patched(_ROUND_LIMIT) else None

        async def settle(call: PlannedCall, before: list[asyncio.Future[bool]]) -> bool:
            if before:
                await workflow.wait(before)  # their outcomes, whatever they were
            async with slots or contextlib.nullcontext():
                if self._abort_requested:
                    raise asyncio.CancelledError  # stopped before it started: END_RUN says so
                # A change waits for every earlier call in its lane, so this is exactly
                # whether an earlier change there was lost (or itself not run for that).
                behind = not call.parallel and call.lane is not None and call.lane in unknown
                failed, lost = await self._round_call(
                    plan.run_id, call, denied=call.call_id in denied, behind_unknown=behind
                )
            if lost and not call.parallel and call.lane is not None:
                unknown.add(call.lane)
            st.open.remove(call.call_id)  # answered (an abort leaves it for END_RUN)
            return failed

        tasks = _in_lanes(st.calls, lambda call, before: asyncio.ensure_future(settle(call, before)))
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        if self._abort_requested:
            raise asyncio.CancelledError
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        for call, call_failed in zip(st.calls, outcomes, strict=True):
            key = f"{call.name}:{call.args_hash}"
            if guard is not None and not call_failed and key not in st.tried:
                guard.progress("run")  # a stall is calls that get nowhere, not many calls
            st.tried.add(key)
        return None

    async def _round_call(
        self, run_id: str, call: PlannedCall, *, denied: bool, behind_unknown: bool
    ) -> tuple[bool, bool]:
        """One call of a parallel round: (whether it failed, whether it was lost).

        A lost call may or may not have run: it is never run again and never guessed at.
        It is answered as unknown, and the run goes on (pi-temporal's recovery seal).
        """
        try:
            return await self._call_tool(run_id, call, denied=denied, behind_unknown=behind_unknown), False
        except ActivityError as err:
            if is_cancelled_exception(err) or err.activity_type != Activity.TOOL_CALL:
                raise  # a stop, or the session could not be recovered: the run ends
            reason = _failure(err)[1]
            workflow.logger.warning("tool %s lost: %s", call.call_id, reason)
            await self._close_call(LostCall(self._session_id, run_id, call.call_id, reason))
            return True, True

    async def _tool_round(
        self,
        plan: RunPlan,
        st: RunState,
        guard: LoopGuard | None,
        end: Callable[..., Any],
    ) -> TurnOutcome | None:
        """Answer every call of the step, in order; a TurnOutcome when the run must stop."""
        for call in st.calls:
            if guard is not None:
                verdict = guard.observe("run", call.name, call.args_hash)
                if verdict.tripped and verdict.action is GuardAction.ABORT:
                    # The runaway run ends; the conversation does not (F32).
                    return await end(
                        "error", error_class="LoopGuard", detail=verdict.detail or "loop detected"
                    )
            approved = call.approval is None or await self._approve(plan.run_id, call)
            st.in_flight = call.call_id
            try:
                failed = await self._call_tool(plan.run_id, call, denied=not approved)
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
            st.in_flight = None
            key = f"{call.name}:{call.args_hash}"
            if guard is not None and not failed and key not in st.tried:
                guard.progress("run")  # a stall is calls that get nowhere, not many calls
            st.tried.add(key)
        return None

    async def _round(
        self,
        plan: RunPlan,
        st: RunState,
        guard: LoopGuard | None,
        end: Callable[..., Any],
    ) -> TurnOutcome | None:
        """The step's calls as one round (Pi Durable's tool tasks).

        Every call is checked by the loop guard and every approval is asked before any
        call runs, so a human answers the round's asks together and a refused round
        changes nothing. Then the calls run in the order made, consecutive read-only
        ones together; a call that changes anything runs alone, so it sees what the
        calls before it did.
        """
        denied: set[str] = set()
        if (verdict := _round_verdict(guard, st.calls)) is not None:
            return await end("error", error_class="LoopGuard", detail=verdict.detail or "loop detected")
        for call in st.calls:
            if call.approval is not None and not await self._approve(plan.run_id, call):
                denied.add(call.call_id)
        failed: dict[str, bool] = {}
        at = 0
        while at < len(st.calls):
            if self._state.steering and not workflow.patched(_STEER_AFTER_ROUND):
                # Runs recorded before steer-after-round: the calls not yet run gave way
                # to the user. Now the round finishes, as pi's does, and the next step
                # reads the message.
                rest = st.calls[at:]
                ids = [c.call_id for c in rest]
                await self._session_activity(
                    Activity.TOOL_BATCH,
                    ToolBatch(self._session_id, plan.run_id, ids, skipped=ids),
                    list[bool],
                    track=True,
                )
                failed |= dict.fromkeys(ids, True)
                break
            batch = [st.calls[at]]
            while batch[-1].parallel and at + len(batch) < len(st.calls) and st.calls[at + len(batch)].parallel:
                batch.append(st.calls[at + len(batch)])
            at += len(batch)
            if len(batch) > 1:
                ids = [c.call_id for c in batch]
                results = await self._session_activity(
                    Activity.TOOL_BATCH,
                    ToolBatch(self._session_id, plan.run_id, ids, [i for i in ids if i in denied]),
                    list[bool],
                    track=True,
                )
                failed |= dict(zip(ids, results, strict=True))
                continue
            call = batch[0]
            st.in_flight = call.call_id
            try:
                failed[call.call_id] = await self._call_tool(
                    plan.run_id, call, denied=call.call_id in denied
                )
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
            st.in_flight = None
        for call in st.calls:
            key = f"{call.name}:{call.args_hash}"
            if guard is not None and not failed.get(call.call_id, True) and key not in st.tried:
                guard.progress("run")  # a stall is calls that get nowhere, not many calls
            st.tried.add(key)
        return None

    async def _end_run(
        self, message: Message, plan: RunPlan, stop_reason: str, **details: Any
    ) -> TurnOutcome:
        req = EndRunRequest(self._session_id, plan.run_id, message.command_id, stop_reason, **details)
        lost = (
            LostCall(self._session_id, plan.run_id, req.lost_call_id, req.lost_reason)
            if req.lost_call_id is not None
            else None
        )
        # If the session's worker is gone, its lost call is closed before adoption.
        return await self._session_activity(Activity.END_RUN, req, TurnOutcome, lost=lost)

    async def _run_steps(self, message: Message, plan: RunPlan) -> TurnOutcome:
        """Model call, then each tool call, until the model answers or policy stops it."""
        guard = _guard(plan.guard)
        in_flight: str | None = None
        tried: set[str] = set()  # (tool, arguments) the run has already run

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
                        failed = await self._call_tool(plan.run_id, call, denied=not approved)
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
                    key = f"{call.name}:{call.args_hash}"
                    if (
                        guard is not None
                        and not failed
                        and key not in tried
                        and workflow.patched(_STALL_COUNTS_PROGRESS)
                    ):
                        guard.progress("run")  # a stall is calls that get nowhere, not many calls
                    tried.add(key)
            return await end(
                "error",
                error_class="AgentLoopError",
                detail=(
                    f"the step limit ({plan.max_steps} model steps) was reached without an "
                    "answer; ask the agent to continue"
                ),
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
            **self._options(Activity.MODEL_STEP),
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

    async def _call_tool(
        self, run_id: str, call: PlannedCall, *, denied: bool, behind_unknown: bool = False
    ) -> bool:
        """Run one tool call; whether it failed."""
        once = call.retry is RetryMode.ONCE and not denied and not behind_unknown
        lost = LostCall(self._session_id, run_id, call.call_id) if once else None
        if lost is not None:
            self._running_once[call.call_id] = lost
        try:
            return await self._session_activity(
                Activity.TOOL_CALL,
                ToolRequest(
                    self._session_id, run_id, call.call_id, denied=denied, behind_unknown=behind_unknown
                ),
                bool,
                retry=_ONCE if once else None,
                track=True,
                # An at-most-once tool whose worker died is not re-run by the adopting worker.
                rerun_after_adopt=not once,
                lost=lost,
            )
        finally:
            self._running_once.pop(call.call_id, None)

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

    def _options(self, activity: Activity) -> dict[str, Any]:
        """The step's time limit, heartbeat and retries (supervisor.temporal.policies)."""
        p = policy(activity)
        return {
            "start_to_close_timeout": p.start_to_close(self._timing),
            "heartbeat_timeout": p.heartbeat_timeout(self._timing),
            "retry_policy": p.retry_policy,
        }

    async def _session_activity[T](
        self,
        activity: Activity,
        arg: object,
        result_type: type[T],
        *,
        retry: RetryPolicy | None = None,
        track: bool = False,
        rerun_after_adopt: bool = True,
        lost: LostCall | None = None,
        cancellation_type: workflow.ActivityCancellationType = (
            workflow.ActivityCancellationType.TRY_CANCEL
        ),
    ) -> T:
        """Run an activity on the worker holding the session's runtime.

        Its time limit, heartbeat and retries are the step's policy; ``retry`` overrides the
        retries for a call that must not repeat (an at-most-once tool or operation).
        """
        options = self._options(activity)
        if retry is not None:
            options["retry_policy"] = retry

        def start(queue: str) -> asyncio.Task[T]:
            return workflow.start_activity(
                activity,
                arg,
                result_type=result_type,
                task_queue=queue,
                schedule_to_start_timeout=self._placement_timeout,
                cancellation_type=cancellation_type,
                **options,
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
        busy_waits = 0
        while True:
            queue = (
                self._state.placement.task_queue
                if self._state.placement is not None
                else workflow.info().task_queue
            )
            task = start(queue)
            if track:
                self._turn = task
                self._tracked[task] = None
            try:
                return await task
            except ActivityError as err:
                if not _is_placement_lost(err) or self._state.placement is None:
                    raise
                if busy_waits < _BUSY_WAITS and await self._still_beats(queue):
                    busy_waits += 1
                    workflow.logger.info("session worker %s is busy, not gone: waiting", queue)
                    if track and self._abort_requested:
                        raise asyncio.CancelledError from None
                    continue
                workflow.logger.warning("session worker %s lost; adopting", queue)
                one = workflow.patched(_ONE_ADOPTION)
                if one:
                    async with self._adopting:
                        if self._state.placement.task_queue == queue:
                            # Not adopted yet by a step beside this one. Every at-most-once
                            # call still running on the lost worker is closed first, so the
                            # adopting worker's recovery finds none half done.
                            closing = {} if lost is None else {lost.call_id: lost}
                            for running in (self._running_once | closing).values():
                                await self._close_lost(running)
                            await self._adopt()
                else:
                    await self._close_lost(lost)
                    await self._adopt()
                if not rerun_after_adopt:
                    raise
                if one and track and self._abort_requested:
                    # A step the stop would have cancelled, had it been running: it is not
                    # run again. Closing work (ending the run, an approval's answer) is.
                    raise asyncio.CancelledError
            finally:
                self._tracked.pop(task, None)

    async def _still_beats(self, queue: str) -> bool:
        """Whether the worker behind ``queue`` still heartbeats (busy, so the session stays
        there). Not knowing counts as gone: the session is adopted, as before."""
        if self._state.placement is None or self._state.placement.task_queue != queue:
            return False  # already adopted, by a step beside this one
        if not workflow.patched(_ADOPT_ONLY_GONE):
            return False
        try:
            return await workflow.execute_activity(
                Activity.WORKER_BEATS,
                queue,
                result_type=bool,
                task_queue=workflow.info().task_queue,
                **self._options(Activity.WORKER_BEATS),
            )
        except ActivityError as err:
            workflow.logger.warning("could not tell whether %s is up: %s", queue, err)
            return False

    async def _close_lost(self, lost: LostCall | None) -> None:
        if lost is not None and workflow.patched(_CLOSE_LOST_BEFORE_ADOPT):
            await self._close_call(lost)

    async def _close_call(self, lost: LostCall) -> None:
        """Answer a call that may or may not have run (journal only, so on any worker)."""
        await workflow.execute_activity(
            Activity.CLOSE_LOST_CALL,
            lost,
            task_queue=workflow.info().task_queue,
            **self._options(Activity.CLOSE_LOST_CALL),
        )

    async def _adopt(self) -> None:
        self._state.placement = await workflow.execute_activity(
            Activity.ADOPT,
            self._session_id,
            result_type=Placement,
            **self._options(Activity.ADOPT),
        )

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
        # A message sent while the session is still being set up waits for it (sessions start
        # asynchronously), then runs, or is refused with the reason the session could not start.
        await workflow.wait_condition(lambda: self._state.phase is not Phase.CREATING)
        if self._creation_error is not None:
            raise self._creation_error
        async with self._submit_lock:
            if message.command_id in self._state.seen_commands:
                # The same message, sent as a signal too (no worker took this update in
                # time), got here first while this one waited.
                return Accepted(message.command_id, queued=self._is_queued(message.command_id))
            if message.steer and self._steerable is not None:
                # pi's steer: placed in the working run at its next step, not queued.
                self._remember(message.command_id)
                self._state.steering.append(message)
                return Accepted(message.command_id, queued=False, steered=True, run_id=self._steerable)
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

    @workflow.signal(name=SUBMIT_SIGNAL)
    async def submit_message_signal(self, message: Message) -> None:
        """A message sent when no worker took ``submit_message`` in time (pi-temporal's
        Signal fallback): the server keeps a signal until a worker comes, so the message
        is not lost while none is up. A signal cannot answer, so a message the update
        would refuse (empty, for a closed or failed session, already seen) is logged and
        dropped, and one for an idle session is queued without the update's prepare
        (its run's first step meets what the prepare would have).
        """
        if not self._handled_here(_HELD_MESSAGE):
            return
        if not message.command_id or not message.text:
            workflow.logger.warning("dropped a message sent as a signal: no id or text")
            return
        await workflow.wait_condition(lambda: self._state.phase is not Phase.CREATING)
        async with self._submit_lock:
            if self._terminate_requested or self._state.phase in (Phase.TERMINATED, Phase.FAILED):
                workflow.logger.warning(
                    "dropped message %s sent as a signal: the session is closed", message.command_id
                )
                return
            if message.command_id in self._state.seen_commands:
                return  # the update delivered it after all, or it was sent twice
            self._remember(message.command_id)
            if message.steer and self._steerable is not None:
                self._state.steering.append(message)
            else:
                self._state.pending.append(message)

    @submit_message.validator
    def _validate_submit(self, message: Message) -> None:
        self._reject_if_closed()
        if not message.command_id or not message.text:
            raise ValueError("command_id and text are required")
        # Refused before it is accepted (validators never run on replay): an accepted
        # update too big for history would terminate the session.
        if len(message.text) > MAX_MESSAGE_CHARS:
            raise ApplicationError(
                f"the message is {len(message.text):,} characters; a session takes at most "
                f"{MAX_MESSAGE_CHARS:,}",
                type="MessageTooLong",
                non_retryable=True,
            )

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
        if lost is not None and workflow.patched(_ONE_ADOPTION):
            self._running_once[lost.call_id] = lost  # closed too if the session is adopted
        try:
            result = await self._session_activity(
                Activity.SESSION_OP,
                op,
                OpResult,
                # Side effects run at most once; a placement loss (never started) re-runs.
                retry=None if op.kind in RETRYABLE_OPS else _ONCE,
                lost=lost,
            )
        except ActivityError as err:
            if lost is not None and not _is_domain(err):
                # The worker died (or failed) mid-invoke: say so, and never re-run it.
                await self._close_call(lost)
                raise ApplicationError(
                    f"tool call {op.op_id!r} outcome unknown: {_failure(err)[1]}",
                    type="ToolOutcomeUnknown",
                    non_retryable=True,
                ) from err
            raise _domain_error(err) from err
        finally:
            if lost is not None:
                self._running_once.pop(lost.call_id, None)
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
        """Stop whatever run is going (a stop that names no run)."""
        if self._running is None:
            return
        self._stop_run()

    @workflow.signal(name=STOP_RUN_SIGNAL)
    def stop_run(self, command_id: str) -> None:
        """Stop the run of message ``command_id`` and no other (pi-temporal's ``interrupt``
        names its prompt), so a stop that lands as that run ends cannot stop the next."""
        if not self._handled_here(_STOP_RUN):
            return
        if self._running is not None and self._running.command_id == command_id:
            self._stop_run()

    def _handled_here(self, kind: str) -> bool:
        """Whether this signal of ``kind`` (one added after release) is acted on.

        A build without its handler left such a signal unread, so replay must leave it
        unread too. Each signal takes its own patch id (``kind-<n>``): a patch answers the
        same for the whole run, and a later signal, handled by a build that has the
        handler, must still be acted on. Call it before the handler's first ``await``.
        """
        seen = self._signals_seen[kind] = self._signals_seen.get(kind, 0) + 1
        return workflow.patched(f"{kind}-{seen}")

    def _stop_run(self) -> None:
        """Stop the running run where it is: every running step is cancelled."""
        self._abort_requested = True  # also wakes a turn waiting on an approval
        tasks = [self._turn, *(t for t in self._tracked if t is not self._turn)]
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()

    @workflow.update
    async def cancel_queued(self, command_id: str) -> bool:
        """Drop a message still waiting behind another run; False when it is not queued (it
        is running, already ran, or was never sent)."""
        for i, message in enumerate(self._state.pending):
            if message.command_id == command_id:
                del self._state.pending[i]
                return True
        return False

    @workflow.update
    async def terminate(self) -> SessionStatus:
        self._terminate_requested = True
        await workflow.wait_condition(lambda: self._state.phase in (Phase.TERMINATED, Phase.FAILED))
        return self.status()

    @workflow.query(name="status")
    def status(self) -> SessionStatus:
        err = self._creation_error
        return SessionStatus(
            phase=self._state.phase,
            turns_completed=self._state.turns_completed,
            queued=len(self._state.pending),
            running_command=self._running.command_id if self._running else None,
            error=err.message if err is not None else None,
            error_kind=err.type if err is not None else None,
            tokens_used=self._state.tokens_used,
            seconds_used=self._state.seconds_used,
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
