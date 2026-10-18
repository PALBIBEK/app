"""Session activities: every side effect of a session runs here, in the worker process.

They delegate to SessionManager, so seams are rebuilt from the session's pinned
composition, events go through the same journal/policy fan-out, and a cold
worker resumes the session from the journal exactly like a restart does.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from supervisor.agent.events import ErrorEvent, RunEnd, ToolStart, TurnEnd
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.session import SessionManager, is_dropped_sentinel
from supervisor.temporal.contracts import (
    NON_RETRYABLE,
    Activity,
    ApprovalAsk,
    ApprovalDecision,
    ApprovalRequest,
    CreateRequest,
    EndRunRequest,
    Placement,
    RunPlan,
    StepRequest,
    StepResult,
    ToolRequest,
    TurnOutcome,
    TurnRequest,
)
from supervisor.temporal.steps import GithiSteps, heartbeating

# How long an aborted turn gets to wind down before the next one may start.
ABORT_GRACE_S = 10.0


def _domain_type(exc: BaseException) -> str | None:
    """The exception's class name when retrying cannot fix it, else None.

    Errors that declare ``retryable`` (catalog, harness, policy, sandbox) decide
    for themselves; the rest are matched against NON_RETRYABLE by class or base.
    """
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        return None if retryable else type(exc).__name__
    if any(cls.__name__ in NON_RETRYABLE for cls in type(exc).__mro__):
        return type(exc).__name__
    return None


def _details(exc: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if code := getattr(exc, "error_code", None):
        details["code"] = code
    if (tools := getattr(exc, "tool_call_ids", None)) is not None:
        details |= {"state": str(getattr(exc, "state", "")), "tool_call_ids": list(tools)}
    return details


@contextlib.contextmanager
def _as_application_error() -> Iterator[None]:
    """Raise domain errors as non-retryable ApplicationErrors; let anything else retry."""
    try:
        yield
    except ApplicationError:
        raise
    except Exception as exc:
        if (kind := _domain_type(exc)) is None:
            raise
        raise ApplicationError(str(exc), _details(exc), type=kind, non_retryable=True) from exc


def _is_terminal(payload: object) -> bool:
    return isinstance(payload, RunEnd | ErrorEvent) or (
        isinstance(payload, TurnEnd) and payload.stop_reason == "aborted"
    )


class SessionActivities:
    """Bound to one worker process; ``home_queue`` is that worker's own task queue."""

    def __init__(
        self,
        manager: SessionManager,
        *,
        reader: JournalReader,
        writer: JournalWriter,
        home_queue: str,
    ) -> None:
        self._manager = manager
        self._reader = reader
        self._writer = writer
        self._home = Placement(home_queue)
        self._steps = GithiSteps(manager, reader)

    @property
    def home_queue(self) -> str:
        return self._home.task_queue

    @activity.defn(name=Activity.CREATE)
    async def create(self, req: CreateRequest) -> Placement:
        with _as_application_error():
            if await self._manager.session_status(req.session_id) is None:
                await self._manager.create(session_id=req.session_id, **asdict(req.spec))
            else:  # a retry after the row was written: attach instead of creating twice
                await self._manager.ensure_ready(req.session_id)
        return self._home

    @activity.defn(name=Activity.ADOPT)
    async def adopt(self, session_id: str) -> Placement:
        """Take over a session whose worker is gone: cold-resume it here from the journal."""
        with _as_application_error():
            await self._manager.ensure_ready(session_id)
        return self._home

    @activity.defn(name=Activity.PREPARE_TURN)
    async def prepare_turn(self, session_id: str) -> None:
        with _as_application_error():
            await self._manager.prepare_turn(session_id)

    @activity.defn(name=Activity.RUN_TURN)
    async def run_turn(self, req: TurnRequest) -> TurnOutcome:
        session_id, message = req.session_id, req.message
        with _as_application_error():
            await self._manager.prepare_turn(session_id)
            await self._refuse_if_interrupted(session_id)
            subscription = self._manager.subscribe_live(session_id)
            tools_started = 0
            send = asyncio.create_task(
                self._manager.send(
                    session_id,
                    message.text,
                    command_id=message.command_id,
                    inbound_trace_id=message.trace_id,
                )
            )
            # Heartbeat well inside the timeout so a live worker is never declared dead.
            heartbeat_every = (activity.info().heartbeat_timeout or timedelta(seconds=30)) / 3
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(
                            subscription.queue.get(), timeout=heartbeat_every.total_seconds()
                        )
                    except TimeoutError:
                        activity.heartbeat(tools_started)
                        if send.done() and (exc := send.exception()) is not None:
                            raise exc from None
                        continue
                    if is_dropped_sentinel(item):
                        raise RuntimeError("live event subscription dropped")
                    tools_started += isinstance(item.payload, ToolStart)
                    activity.heartbeat(tools_started)
                    if _is_terminal(item.payload):
                        await send
                        stop = getattr(item.payload, "stop_reason", type(item.payload).__name__)
                        status = await self._manager.session_status(session_id) or "terminated"
                        return TurnOutcome(message.command_id, str(stop), item.seq, status)
            except asyncio.CancelledError:
                # Abort: stop the runtime's turn and let it wind down before reporting.
                await self._manager.abort(session_id)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(send), ABORT_GRACE_S)
                raise
            except Exception as exc:
                await self._record_failure(session_id, exc, side_effects=tools_started > 0)
                raise
            finally:
                self._manager.unsubscribe_live(session_id, subscription)

    @activity.defn(name=Activity.RESPOND_APPROVAL)
    async def respond_approval(self, req: ApprovalRequest) -> None:
        with _as_application_error():
            await self._manager.ensure_ready(req.session_id)
            await self._manager.respond_approval(req.approval.approval_id, req.approval.choice)

    @activity.defn(name=Activity.TERMINATE)
    async def terminate(self, session_id: str) -> None:
        with _as_application_error():
            await self._manager.terminate(session_id)

    # --- stepped (workflow-driven) githi turns ---------------------------------

    @activity.defn(name=Activity.BEGIN_RUN)
    async def begin_run(self, req: TurnRequest) -> RunPlan:
        with _as_application_error():
            return await self._steps.begin_run(req)

    @activity.defn(name=Activity.MODEL_STEP)
    async def model_step(self, req: StepRequest) -> StepResult:
        with _as_application_error():
            return await self._steps.model_step(req)

    @activity.defn(name=Activity.TOOL_CALL)
    async def tool_call(self, req: ToolRequest) -> bool:
        with _as_application_error():
            return await heartbeating(self._steps.tool_call(req))

    @activity.defn(name=Activity.REQUEST_APPROVAL)
    async def request_approval(self, ask: ApprovalAsk) -> str:
        with _as_application_error():
            return await self._steps.request_approval(ask)

    @activity.defn(name=Activity.RESOLVE_APPROVAL)
    async def resolve_approval(self, decision: ApprovalDecision) -> str:
        with _as_application_error():
            return await self._steps.resolve_approval(decision)

    @activity.defn(name=Activity.END_RUN)
    async def end_run(self, req: EndRunRequest) -> TurnOutcome:
        with _as_application_error():
            return await self._steps.end_run(req)

    def all(self) -> list[Any]:
        return [
            self.create,
            self.adopt,
            self.prepare_turn,
            self.run_turn,
            self.respond_approval,
            self.terminate,
            self.begin_run,
            self.model_step,
            self.tool_call,
            self.request_approval,
            self.resolve_approval,
            self.end_run,
        ]

    async def _refuse_if_interrupted(self, session_id: str) -> None:
        """Never silently re-run a turn a crashed worker left half done: tools may have run."""
        if (open_run := await self._reader.open_run(session_id)) is None:
            return
        detail = (
            f"turn starting at seq {open_run[0]} was interrupted by a worker failure; "
            "re-send to retry"
        )
        await self._writer.append(
            session_id,
            ErrorEvent(error_class="TurnInterrupted", retryable=False, detail=detail),
            turn_id=None,
            trace_id=None,
        )
        raise ApplicationError(detail, type="TurnInterrupted", non_retryable=True)

    async def _record_failure(
        self, session_id: str, exc: Exception, *, side_effects: bool
    ) -> None:
        """Close the run in the journal so clients see it and a retry starts clean.

        After a tool ran, a retry could repeat its side effects, so the failure
        becomes final (TurnInterrupted) instead of retryable.
        """
        retryable = not side_effects and _domain_type(exc) is None
        await self._writer.append(
            session_id,
            ErrorEvent(error_class=type(exc).__name__, retryable=retryable, detail=str(exc)),
            turn_id=None,
            trace_id=None,
        )
        if not retryable and _domain_type(exc) is None:
            raise ApplicationError(
                f"turn failed after tools ran: {exc}", type="TurnInterrupted", non_retryable=True
            ) from exc
