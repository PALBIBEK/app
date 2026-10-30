"""Workflow-driven githi turns: one model call or one tool call per activity.

The workflow decides what happens next; these steps do the work against the
session's live runtime (its pinned seams) and journal every event with a key
derived from the run, step and tool call. A retried or re-homed step therefore
never writes an event twice, and the run's conversation is always rebuilt from
the journal, so tool arguments and results never enter workflow history.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import posixpath
import threading
import time
from collections.abc import Awaitable
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final

from temporalio import activity
from temporalio.exceptions import ApplicationError

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    BackgroundResults,
    CompactionEnd,
    ErrorEvent,
    MemoryInjected,
    MemoryRecall,
    MessageEnd,
    ModelRetry,
    ProjectContext,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    WorkspaceRestored,
)
from supervisor.agent.runtimes.githi.classify import (
    FINAL_STEP_NOTICE,
    is_context_overflow,
    is_final_step,
    is_refused_key,
    is_retryable,
    loop_spec,
    refusal,
    retry_delay,
    retry_spec,
    steps_left_notice,
    unique_call_ids,
)
from supervisor.agent.runtimes.githi.loop import AgentLoop
from supervisor.agent.runtimes.githi.prompt import in_pi_order
from supervisor.agent.runtimes.githi.render import tool_result_failed, tool_result_text
from supervisor.agent.runtimes.githi.runtime import GithiRuntime
from supervisor.hands.keys import take_hand, with_hand_argument
from supervisor.harness import BudgetSpec, RecoveryAction
from supervisor.harness.core.enums import ToolClass as HarnessToolClass
from supervisor.journal.partials import PartialOutputs, PartialSink
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import keyed_event_id
from supervisor.memory.binding import Strategy
from supervisor.memory.classification import OneShot
from supervisor.memory.history import message_text
from supervisor.memory.platform import gateway_one_shot
from supervisor.memory.tools import PLATFORM_TOOLS
from supervisor.memory.turn import SessionMemory
from supervisor.models.messages import REASONING
from supervisor.policy.guards import call_hash
from supervisor.seams import ChatMessage, ToolResult
from supervisor.seams.interfaces.tool_provider import needs_workspace
from supervisor.session import SessionManager, SessionMoved
from supervisor.session.project_context import read_project_context
from supervisor.session.tool_outbox import idempotency_key
from supervisor.temporal.context_memory import PinnedContextMemory
from supervisor.temporal.contracts import (
    COMPACT,
    RETRY,
    ApprovalAsk,
    ApprovalDecision,
    ApprovalNeeded,
    CaptureConfig,
    Compacted,
    EndRunRequest,
    GuardSettings,
    LostCall,
    PlaceInputs,
    PlannedCall,
    RetryMode,
    RunPlan,
    StepRequest,
    StepResult,
    ToolBatch,
    ToolRequest,
    TurnOutcome,
    TurnRequest,
    run_id_for,
)
from supervisor.temporal.subagent_tools import (
    SUBAGENT_RESULT,
    SUBAGENT_TOOLS,
    SubagentTools,
    announcement,
    render,
)

TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"
TOOL_NOT_RUN = "TOOL_NOT_RUN"

WIND_DOWN_S: Final = 7.0
"""How long a cancelled activity lets its work wind down (a stopped tool kills its command
and records what it did) before it reports the cancel."""

STOP_GRACE_S: Final = 5.0
"""How long a stopped tool call waits for its tool to end before leaving it unknown."""

STOP_WAIT_S: Final = 8.0
"""How long a stopped run's close waits for its running calls to record what they did
(they hear the stop on their next heartbeat, then kill their command): longer than a
heartbeat plus ``STOP_GRACE_S``. A call whose worker is gone never records, and is answered
as unknown once this passes."""

TOOL_CALL_LIMIT_S: Final = float(os.environ.get("TOOL_CALL_TIMEOUT_S", "1800"))
"""How long one tool call may run (``TOOL_CALL_TIMEOUT_S``, 30 minutes by default, as
pi-temporal's): past it the call is stopped (its command killed) and the model told; the
run goes on. A tool's own limit (bash's) is usually far shorter."""

TOOL_TIMEOUT: Final = "TOOL_TIMEOUT"

HEARTBEAT_EVERY: Final = timedelta(seconds=2)
"""How often a running activity heartbeats (at most): a stop reaches it within this."""


async def heartbeating[T](work: Awaitable[T]) -> T:
    """Await ``work`` while heartbeating, so a dead worker is noticed and aborts arrive.

    A cancel (a stop, or the worker shutting down) cancels ``work`` and lets it wind down
    for up to ``WIND_DOWN_S`` before reporting, so what it records lands first.
    """
    task = asyncio.ensure_future(work)
    timeout = activity.info().heartbeat_timeout or timedelta(seconds=30)
    try:
        while True:
            every = min(timeout.total_seconds() / 3, HEARTBEAT_EVERY.total_seconds())
            done, _ = await asyncio.wait({task}, timeout=every)
            if done:
                return task.result()
            activity.heartbeat()
    except asyncio.CancelledError:
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), WIND_DOWN_S)
        raise
    finally:
        task.cancel()

logger = logging.getLogger(__name__)


def conversation(prior: list[dict[str, Any]], run: list[Any]) -> list[dict[str, Any]]:
    """The run's messages, rebuilt from its journalled events on top of earlier history.

    What the run shows the model besides its own messages (notebook facts, what the
    pinned recall backend found, background subagents that finished) comes right
    before the run's user message, so it is part of the history
    later runs carry (what the next run's delta assumes is held).

    An assistant message keeps the model that wrote it, and — when it carries
    native reasoning or signed text — its journalled parts, so that model is shown
    its own reasoning again on the next call.
    """
    messages = list(prior)
    user_at: int | None = None
    notes: list[str] = []  # WorkspaceRestored, waiting for their round's results
    for envelope in run:
        event = envelope.payload
        if isinstance(event, WorkspaceRestored):
            # Shown once the round it happened in has its results (a note between a
            # reply's calls and their results would break the exchange).
            notes.append(event.text)
            continue
        if notes and isinstance(event, MessageEnd):
            messages += [{"role": "user", "content": [{"type": "text", "text": n}]} for n in notes]
            notes = []
        if isinstance(event, CompactionEnd) and event.in_run and event.stop_reason == "ok":
            # A compaction inside this run: its messages are the whole conversation up to
            # here (summary + what it kept), and the run carries on from them.
            messages, user_at = list(event.messages or []), None
            continue
        if isinstance(event, (MemoryInjected, MemoryRecall, BackgroundResults)):
            shown = announcement(event) if isinstance(event, BackgroundResults) else event.text
            if shown:
                at = len(messages) if user_at is None else user_at
                messages.insert(
                    at, {"role": "system", "content": [{"type": "text", "text": shown}]}
                )
            continue
        if isinstance(event, MessageEnd) and event.role == "user" and user_at is None:
            user_at = len(messages)
        if isinstance(event, MessageEnd) and event.role in ("user", "assistant"):
            text = [p for p in event.content if p.get("type") == "text"]
            calls = [
                {"id": p["id"], "name": p["name"], "arguments": p.get("arguments", {})}
                for p in event.content
                if p.get("type") == "tool_call"
            ]
            message: dict[str, Any] = {"role": event.role, "content": text}
            if calls:
                message["tool_calls"] = calls
            if event.role == "assistant" and event.model:
                message["model"] = event.model
            if any(p.get("type") == REASONING or p.get("signature") for p in event.content):
                message["parts"] = event.content
            messages.append(message)
        elif isinstance(event, ToolEnd):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": event.tool_call_id,
                    "content": [
                        {"type": "text", "text": tool_result_text(event.result, event.tool_name)}
                    ],
                    # Read back by the provider layer as the result's error flag (pi's
                    # isError: a failed tool, or a command that exited non-zero).
                    **(
                        {"is_error": True}
                        if event.is_error or tool_result_failed(event.result)
                        else {}
                    ),
                }
            )
    messages = _in_call_order(messages)
    return messages + [{"role": "user", "content": [{"type": "text", "text": n}]} for n in notes]


def _past_session_bound(budget: BudgetSpec | None, req: TurnRequest) -> bool:
    """Whether the session has used more than its budget allows (tokens or time)."""
    if budget is None:
        return False
    tokens, seconds = budget.max_tokens_per_session, budget.max_seconds_per_session
    return (tokens is not None and req.tokens_used > tokens) or (
        seconds is not None and req.seconds_used > seconds
    )


def _usage_tokens(usage: Any) -> int:
    """Input plus output tokens of a journalled usage (a ``Usage``, or its dict as read
    back), so a retried step reports what the first attempt spent."""
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    return int(usage.input_tokens + usage.output_tokens)


FILE_TOOLS = frozenset({"read", "write", "edit"})


def call_lane(name: str, args: dict[str, Any]) -> str | None:
    """The calls a call must not overtake (pi's file mutation queue): the file a file tool
    names, normalised so ``./a``, ``a`` and ``/workspace/a`` are one; the memory and
    subagent tools keep their order among themselves. None: it runs whenever it can."""
    if name in PLATFORM_TOOLS or name in SUBAGENT_TOOLS or name == SUBAGENT_RESULT:
        return "platform"
    path = (args.get("path") or args.get("file_path")) if name in FILE_TOOLS else None
    if not isinstance(path, str) or not path.strip():
        return None
    text = path.strip()
    for root in ("/workspace/", "/workspace"):
        if text == root.rstrip("/") or text.startswith(root):
            text = text[len(root) :]
            break
    return "file:" + (posixpath.normpath(text or ".").lstrip("/") or ".")


UNKNOWN_OUTCOME = (
    "The outcome of this tool call is unknown. The session stopped before its result was "
    "recorded, so it is not known whether the call ran. It can have taken effect. Check the "
    "current state before you try again."
)
"""What the model reads for a call that started and left no result (pi's
``unknownToolCallOutcome``, as pi-temporal reports a lost or stopped call)."""

NOT_RUN_OUTCOME = "This tool call did not run. The turn stopped before it started."
"""What the model reads for a call nothing started (pi-temporal's ``notRunOutcome``)."""

BEHIND_UNKNOWN = (
    "This tool call did not run: an earlier call of this round that changes the same file "
    "(or the same memory or subagents) has an unknown outcome, and this call was made "
    "assuming it took effect. Check the current state, then make this call again if it is "
    "still needed."
)
"""A change queued behind a lost change in its lane (pi's file mutation queue keeps
order; ours also keeps a change from landing on a state it was not planned for)."""

SKIPPED_FOR_STEERING = (
    "not run: the user sent a new message while this round was running. Read it first, "
    "then make this call again if it is still needed."
)
"""pi's "Skipped due to queued user message": the rest of a round gives way to the user."""


def _project(run: list[Any]) -> list[tuple[str, str]]:
    """The project instructions this run read as it began (none: empty)."""
    found = [env.payload for env in run if isinstance(env.payload, ProjectContext)]
    return [(found[-1].path, found[-1].text)] if found else []


def _call_ids(run: list[Any]) -> set[str]:
    """Tool-call ids the run's replies already used."""
    return {
        str(p.get("id"))
        for env in run
        if isinstance(env.payload, MessageEnd) and env.payload.role == "assistant"
        for p in env.payload.content
        if p.get("type") == "tool_call"
    }


def _streams(gateway: Any) -> bool:
    """Whether the gateway's ``complete`` hands over the reply as it streams (``on_delta``)."""
    try:
        return "on_delta" in inspect.signature(gateway.complete).parameters
    except (TypeError, ValueError):
        return False


def _in_call_order(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each reply's tool results in the order it made the calls.

    Calls run together finish in any order; the model reads its results back in the
    order it asked, as it would had they run one after another.
    """
    out = list(messages)
    for i, message in enumerate(out):
        order = {c["id"]: n for n, c in enumerate(message.get("tool_calls") or [])}
        if len(order) < 2:
            continue
        end = i + 1
        while end < len(out) and out[end].get("role") == "tool":
            end += 1
        out[i + 1 : end] = sorted(out[i + 1 : end], key=lambda m: order.get(m.get("tool_call_id"), len(order)))
    return out


def _user_text(run: list[Any]) -> str:
    for envelope in run:
        event = envelope.payload
        if isinstance(event, MessageEnd) and event.role == "user":
            return message_text(event)
    return ""


def _as_tool_result(result: dict[str, Any]) -> ToolResult:
    return ToolResult(
        ok=bool(result.get("ok")),
        payload=result.get("payload"),
        error=result.get("error"),
        code=result.get("code"),
    )


def denied_result(error: str, code: str) -> dict[str, Any]:
    """A refused tool call's result in the same shape as a tool's own (F16)."""
    return {"ok": False, "payload": None, "error": error, "code": code}


def _platform_result(text: str, is_error: bool) -> dict[str, Any]:
    """A memory tool's result in the same shape as a catalog tool's."""
    return {
        "ok": not is_error,
        "payload": text,
        "error": text if is_error else None,
        "code": "MEMORY_TOOL_ERROR" if is_error else None,
    }


class GithiSteps:
    """Step implementations bound to one worker's SessionManager.

    The step-wise twin of the manager's runtime-driven turn, so it works with the
    manager's internals (registry, budget, approvals, _record) directly.
    """

    def __init__(self, manager: SessionManager, reader: JournalReader) -> None:
        self._manager = manager
        self._reader = reader
        self._memory = SessionMemory(manager.memory, reader, one_shot_for=self.one_shot_for)
        self._pinned = PinnedContextMemory(manager, record=self._record, keyed=self._keyed)
        self._subagents = SubagentTools(manager, reader)
        self._partials = PartialOutputs(manager._db)

    @property
    def memory(self) -> SessionMemory:
        return self._memory

    async def one_shot_for(self, session_id: str) -> OneShot:
        """One-shot calls (classification, extraction) with the session's own model, paid
        for by the session (its cost cap); the platform's gateway when it has none."""
        row = await self._manager._get_session_row(session_id)
        seams = self._manager._seams_for_session(session_id, row)
        if seams is None or seams.model_gateway is None:
            return self._manager.memory.one_shot
        return gateway_one_shot(
            seams.model_gateway,
            model=self._manager.session_model(row),
            charge=lambda cost: self._manager.charge_cost(session_id, cost),
        )

    # --- run lifecycle ---------------------------------------------------------

    async def begin_run(self, req: TurnRequest) -> RunPlan:
        session_id, message = req.session_id, req.message
        run_id = run_id_for(session_id, message.command_id)
        await self._manager.prepare_turn(session_id)
        runtime = self._manager._registry.get(session_id)
        if not self._is_stepped(runtime):
            return RunPlan(run_id, stepped=False)
        await self._close_interrupted_run(session_id, run_id)
        self._manager._budget.begin_turn(session_id)
        await self._record(session_id, run_id, "start", RunStart(), trace_id=message.trace_id)
        await self._record(
            session_id,
            run_id,
            "user",
            MessageEnd(role="user", content=[{"type": "text", "text": message.text}]),
        )
        await self._memory.checkpoint(session_id, run_id, self._record)
        manifest = await self._manifest(session_id)
        if _past_session_bound(manifest.budget, req):
            # The run ends at its first step, so it does no work first (pi-temporal checks
            # before anything runs).
            return self._plan_run(run_id, manifest, capture=None)
        if (project := await read_project_context(self._manager, session_id)) is not None:
            await self._record(
                session_id, run_id, "project", ProjectContext(project.path, project.text, project.digest)
            )
        # Between turns: compact what this run carries over, per the pinned memory profile.
        compaction = await heartbeating(
            self._pinned.compact(
                session_id,
                run_id,
                await self._memory.prior_history(session_id),
                await self._memory.bounded(session_id),
                retries=retry_spec(manifest),
            )
        )
        await self._announce_background(session_id, run_id)
        return self._plan_run(
            run_id,
            manifest,
            capture=await self._capture_plan(session_id),
            tokens=_usage_tokens(compaction.usage) if compaction else 0,
        )

    def _plan_run(self, run_id: str, manifest: Any, *, capture: Any, tokens: int = 0) -> RunPlan:
        """``tokens``: what opening the run spent (a compaction's summary)."""
        guards = manifest.guards
        guard = (
            GuardSettings(str(guards.repetition), str(guards.oscillation), guards.stall_iterations)
            if guards is not None
            else None
        )
        return RunPlan(
            run_id,
            stepped=True,
            guard=guard,
            max_steps=loop_spec(manifest).max_steps,
            capture=capture,
            max_run_s=loop_spec(manifest).max_run_seconds,
            hard_run_s=loop_spec(manifest).hard_run_seconds,
            max_run_tokens=manifest.budget.max_tokens_per_turn if manifest.budget else None,
            max_session_tokens=manifest.budget.max_tokens_per_session if manifest.budget else None,
            max_session_s=manifest.budget.max_seconds_per_session if manifest.budget else None,
            tokens=tokens,
        )

    async def _announce_background(self, session_id: str, run_id: str) -> None:
        """Tell the model, once, about background subagents that finished since it last ran."""
        if await self._keyed(session_id, run_id, "background") is not None:
            return
        if results := await self._subagents.unannounced(session_id):
            await self._record(session_id, run_id, "background", BackgroundResults(run_id, results))

    async def _capture_plan(self, session_id: str) -> CaptureConfig | None:
        """Automatic capture after this turn (QM per-turn strategy), if the session has it."""
        scope = await self._memory.scope(session_id)
        if scope is None:
            return None
        b = scope.binding
        if b.strategy is not Strategy.PER_TURN or b.writable is None:
            return None
        return CaptureConfig(quiet_s=b.capture_quiet_s, max_turns=b.capture_max_turns)

    async def end_run(self, req: EndRunRequest) -> TurnOutcome:
        sid, run_id = req.session_id, req.run_id
        if req.stop_reason == "aborted" or req.error_class == "RunDeadline":
            # Stopped where it was: its running calls kill their commands and record what
            # they did; give them a moment, so the model reads that rather than "unknown".
            stopped = [c for c in [req.lost_call_id, *req.open_call_ids] if c is not None]
            await self._await_ends(sid, run_id, stopped)
        # A reply cut off as it streamed (a stop, a failed or lost step) is not shown again.
        await self._clear_partial(sid)
        if req.lost_call_id is not None:
            await self.close_lost_call(LostCall(sid, run_id, req.lost_call_id, req.lost_reason))
        for call_id in req.open_call_ids:  # each a no-op once its call ended
            await self.close_lost_call(LostCall(sid, run_id, call_id, req.lost_reason))
        why = f"the run ended ({req.stop_reason}) before the call's result was recorded"
        for call_id in await self._unanswered(sid, run_id):
            # Asked for, never answered: a stop between the model's reply and its round
            # (pi-temporal's recovery seal answers these too). Not run, unless it started.
            await self.close_lost_call(LostCall(sid, run_id, call_id, why))
        if req.close_session and await self._manager.session_status(sid) == "active":
            runtime = self._manager._registry.get(sid)
            await self._manager._policy_abort(
                sid, runtime, error_class=req.error_class or "Policy", detail=req.detail or ""
            )
        elif req.error_class is not None:
            await self._record(
                sid,
                run_id,
                "error",
                ErrorEvent(error_class=req.error_class, retryable=False, detail=req.detail or ""),
            )
        await self._record(sid, run_id, "turn_end", TurnEnd(stop_reason=req.stop_reason))
        history = conversation(
            await self._memory.prior_history(sid), await self._reader.read_turn(sid, run_id)
        )
        last_seq = await self._record(sid, run_id, "end", RunEnd(messages=history))
        status = await self._manager.session_status(sid) or "terminated"
        return TurnOutcome(req.command_id, req.stop_reason, last_seq, status)

    async def _unanswered(self, session_id: str, run_id: str) -> list[str]:
        """The calls the run's model asked for that have no result yet, in order."""
        asked: list[str] = []
        ended: set[str] = set()
        for envelope in await self._reader.read_turn(session_id, run_id):
            event = envelope.payload
            if isinstance(event, MessageEnd) and event.role == "assistant":
                asked += [
                    str(part["id"])
                    for part in event.content
                    if isinstance(part, dict) and part.get("type") == "tool_call"
                ]
            elif isinstance(event, ToolEnd):
                ended.add(event.tool_call_id)
        return [call_id for call_id in asked if call_id not in ended]

    # --- model ---------------------------------------------------------------

    async def model_step(self, req: StepRequest) -> StepResult:
        """One model call: the session's brain, on whichever worker picked it up.

        Older histories run it beside the sandbox and checkpoint while the model
        works (``req.checkpoint``); the step returns only once that is done, so the
        next tool never runs ahead of it (Crab's barrier, without making the model
        wait). Newer ones run CHECKPOINT on the sandbox's worker alongside.
        """
        if not req.checkpoint:
            return await self._model_step(req)
        saving = asyncio.create_task(self._manager.checkpoint(req.session_id))
        try:
            return await self._model_step(req)
        finally:
            await saving

    async def _model_step(self, req: StepRequest) -> StepResult:
        sid, run_id = req.session_id, req.run_id
        key = f"{req.step}:assistant"
        if (assistant := await self._keyed(sid, run_id, key)) is None:
            # Cost here; tokens per run and per session are the workflow's to count (a
            # model step runs on any worker, so a worker's own count would not see them all).
            state = await self._manager._budget.check(sid, turn_tokens=False)
            if state.exceeded:
                return await self._budget_stop(sid, state.exceeded_reason)
            runtime = await self._brain(sid)
            loop = runtime.loop()
            await self._record(sid, run_id, f"{req.step}:turn", TurnStart())
            run = await self._reader.read_turn(sid, run_id)
            if req.step == 0:
                await self._memory.recall(sid, run_id, _user_text(run), self._record)
                await self._pinned.recall(sid, run_id, _user_text(run))
                run = await self._reader.read_turn(sid, run_id)
            schemas = await self._tool_schemas(sid, loop)
            history = conversation(await self._memory.prior_history(sid), run)
            # githi's prepare phase: a request past the pinned limit is not sent; the
            # workflow compacts (once per step) and asks again. Not on the first step:
            # the run has added nothing yet, earlier turns were compacted as it began,
            # and the request itself is never summarised away.
            if (
                req.step > 0
                and req.can_compact
                and not req.compacted
                and await self._pinned.over_limit(sid, history, run)
            ):
                return StepResult(calls=[], final=False, kind=COMPACT)
            chat = loop.messages_for_model(history, schemas, project=_project(run))
            files = self._manager.files
            if files is not None and self._manager._hands.recipe(sid) is not None and (note := await files.context_note(sid)):
                # Where this session's attachments are and where outputs go, after the pinned
                # prompt (which stays as published) and before the conversation.
                at = 1 if chat and chat[0].role == "system" else 0
                chat.insert(at, ChatMessage(role="system", content=note))
            spec = loop_spec(await self._manifest(sid))
            left = spec.max_steps - req.step
            final = is_final_step(spec, req.step)
            if final:
                chat.append(ChatMessage(role="system", content=FINAL_STEP_NOTICE))
            elif 0 < left <= spec.warn_steps_remaining:
                chat.append(ChatMessage(role="system", content=steps_left_notice(left)))
            assert runtime.seams is not None and runtime.seams.model_gateway is not None
            choice: dict[str, Any] = {"tool_choice": "none"} if final and schemas else {}
            gateway: Any = runtime.seams.model_gateway
            # The reply as it streams, for clients to show while it is written (Pi
            # Durable's PartialOutput); only where the gateway can hand it over.
            sink = PartialSink(self._partials, sid, run_id, req.step) if _streams(gateway) else None
            try:
                call = asyncio.ensure_future(
                    asyncio.to_thread(
                        gateway.complete,
                        chat,
                        schemas,
                        model=runtime.model,
                        **choice,
                        **({"on_delta": sink.on_delta} if sink is not None else {}),
                    )
                )
                flushing = asyncio.ensure_future(sink.flushing(call)) if sink is not None else None
                try:
                    completion = await heartbeating(call)
                finally:
                    if flushing is not None:
                        flushing.cancel()
            except Exception as exc:
                if sink is not None:  # what streamed is no reply: the step asks again or ends
                    await self._clear_partial(sid)
                # The provider (or the model layer) says the request does not fit: nothing
                # was answered. The workflow compacts and asks again, once; a step that
                # still overflows after that ends the run saying so. Not the first step:
                # earlier turns were compacted as the run began, so what overflows is
                # the request itself, which a summary cannot shrink.
                if req.step > 0 and req.can_compact and is_context_overflow(exc):
                    return StepResult(calls=[], final=False, kind=COMPACT)
                if req.can_retry:
                    # The gateway refused the session's key, not the request: with the key
                    # replaced, the step is asked again as after a busy provider.
                    renewed = is_refused_key(exc) and await self._manager.renew_gateway_key(
                        sid, refused=getattr(gateway, "token", None)
                    )
                    return await self._retry_or_fail(req, exc, renewed=renewed)
                raise
            usage = AgentLoop.usage_from_completion(completion)
            assistant = MessageEnd(
                role="assistant",
                content=unique_call_ids(AgentLoop.assistant_content(completion), _call_ids(run), req.step),
                usage=usage,
                model=completion.model,
                finish_reason=completion.finish_reason,
            )
            # Cost first: a crash before the journal write re-runs a call the provider
            # has billed, so both are counted; the reverse order could drop one.
            await self._manager._budget.record(
                sid, Decimal(str(usage.cost_total)), usage.total_tokens
            )
            await self._record(sid, run_id, key, assistant)
            if sink is not None:
                await self._clear_partial(sid)
        # Checked on replay too, so a retried step cannot hand out calls past the limit.
        tokens = _usage_tokens(assistant.usage)
        if (spent := await self._manager._budget.check(sid, turn_tokens=False)).exceeded:
            return await self._budget_stop(sid, spent.exceeded_reason, tokens=tokens)
        calls = [p for p in assistant.content if p.get("type") == "tool_call"]
        if not calls:
            return StepResult(calls=[], final=True, tokens=tokens)
        manifest = await self._manifest(sid)
        spec = loop_spec(manifest)
        plans = [
            self._plan(manifest, call, refused=refusal(spec, req.step, assistant, call) is not None)
            for call in calls
        ]
        return StepResult(calls=plans, final=False, lanes=True, tokens=tokens)

    async def _retry_or_fail(self, req: StepRequest, exc: Exception, *, renewed: bool = False) -> StepResult:
        """pi's auto-retry: a call that may pass is asked again after a durable wait.

        Journals ``ModelRetry`` (keyed by step and attempt, so a re-run activity does not
        announce it twice) and answers RETRY with the wait; the workflow sleeps on a
        timer, then runs the step again. A call that will fail the same way, or one out
        of retries, fails the step at once: no activity retries (Temporal's are for a
        lost worker, not a provider's answer). ``renewed``: the session's key was just
        replaced after the gateway refused it, so the same call may now pass.
        """
        spec = retry_spec(await self._manifest(req.session_id))
        attempt = req.attempt + 1
        retryable = renewed or is_retryable(exc)
        delay = retry_delay(spec, attempt, exc) if retryable else None
        if delay is None:
            if not retryable:
                raise ApplicationError(str(exc), type=type(exc).__name__, non_retryable=True) from exc
            raise ApplicationError(
                f"the model provider kept failing ({req.attempt} retries): {exc}",
                type="ModelUnavailable",
                non_retryable=True,
            ) from exc
        await self._record(
            req.session_id,
            req.run_id,
            f"{req.step}:retry:{attempt}",
            ModelRetry(
                step=req.step,
                attempt=attempt,
                max_attempts=spec.max_retries,
                delay_s=delay,
                error_class=type(exc).__name__,
                detail=str(exc)[:500],
            ),
        )
        return StepResult(calls=[], final=False, kind=RETRY, delay_s=delay)

    async def _clear_partial(self, session_id: str) -> None:
        try:
            await self._partials.clear(session_id)
        except Exception:
            logger.debug("partial output clear failed session=%s", session_id, exc_info=True)

    async def compact(self, req: StepRequest) -> bool:
        """Compact the conversation so far, this run included, before ``req.step``'s request."""
        return (await self.compact_counted(req)).ok

    async def compact_counted(self, req: StepRequest) -> Compacted:
        """``compact``, with what its summary spent, for the run's and the session's bounds."""
        sid, run_id = req.session_id, req.run_id
        await self._brain(sid)
        run = await self._reader.read_turn(sid, run_id)
        history = conversation(await self._memory.prior_history(sid), run)
        end = await heartbeating(
            self._pinned.compact_run(
                sid,
                run_id,
                req.step,
                history,
                await self._reader.read(sid),
                request=_user_text(run),
                retries=retry_spec(await self._manifest(sid)),
            )
        )
        if end is None:
            return Compacted(ok=False)
        # A failed summary keeps the conversation whole and the run asks again: past
        # the compaction threshold it still fits the window; if the provider says it
        # does not, that step ends the run (it compacted once already).
        return Compacted(ok=end.stop_reason in ("ok", "error"), tokens=_usage_tokens(end.usage))

    def _plan(self, manifest: Any, call: dict[str, Any], *, refused: bool = False) -> PlannedCall:
        name = str(call["name"])
        if refused:  # answered without running: nothing to approve, safe to repeat
            return PlannedCall(
                call_id=str(call["id"]),
                name=name,
                args_hash=call_hash(name, dict(call.get("arguments") or {})),
                retry=RetryMode.RETRY,
                approval=None,
                parallel=True,
            )
        approvals = manifest.approvals
        needed = (
            ApprovalNeeded(
                prompt=f"Allow the agent to run {name!r}?",
                options=["allow", "deny"],
                timeout_s=float(approvals.timeout_s),
                on_timeout=str(approvals.on_timeout),
            )
            if approvals is not None and name in approvals.require_approval
            else None
        )
        retry = (
            RetryMode.RETRY
            # Memory tools are idempotent: capture dedupes, edits and deletes are by id;
            # reading a subagent's result is pure. Starting a subagent runs once.
            if name in PLATFORM_TOOLS
            or name == SUBAGENT_RESULT
            or (
                name not in SUBAGENT_TOOLS
                and manifest.recovery.action_for(name) is RecoveryAction.AUTO_RESUME
            )
            else RetryMode.ONCE
        )
        return PlannedCall(
            call_id=str(call["id"]),
            name=name,
            args_hash=call_hash(name, dict(call.get("arguments") or {})),
            retry=retry,
            approval=needed,
            # Read-only by its pinned class; platform and subagent tools keep their order.
            parallel=(
                name not in PLATFORM_TOOLS
                and name not in SUBAGENT_TOOLS
                and name != SUBAGENT_RESULT
                and manifest.recovery.classify(name) is HarnessToolClass.PURE
            ),
            lane=call_lane(name, dict(call.get("arguments") or {})),
        )

    async def _budget_stop(
        self, session_id: str, reason: str | None, *, tokens: int = 0
    ) -> StepResult:
        """Budget is spent: close the session to new turns (it reopens if limits change).
        ``tokens``: what the step's model call used, which the provider billed all the same."""
        if await self._manager.session_status(session_id) == "active":
            await self._manager._policy_abort(
                session_id,
                self._manager._registry.get(session_id),
                error_class="BudgetExceeded",
                detail=reason or "budget exceeded",
                status="budget_exceeded",
            )
        return StepResult(calls=[], final=True, stop_reason="budget_exceeded", tokens=tokens)

    # --- tools ---------------------------------------------------------------

    async def place_inputs(self, req: PlaceInputs) -> None:
        """Journal steering messages as the user's, before step ``req.step`` (pi's steer)."""
        for message in req.messages:
            await self._record(
                req.session_id,
                req.run_id,
                f"{req.step}:steer:{message.command_id}",
                MessageEnd(role="user", content=[{"type": "text", "text": message.text}]),
                trace_id=message.trace_id,
            )

    async def tool_batch(self, batch: ToolBatch) -> list[bool]:
        """Run a round's read-only calls together; whether each errored, in order.

        Each is ``tool_call``, keyed as one, so a retried batch re-runs only the calls
        that had not ended, and the journal holds each result once.
        """
        denied, skipped = set(batch.denied), set(batch.skipped)
        return list(
            await asyncio.gather(
                *(
                    self.tool_call(
                        ToolRequest(
                            batch.session_id,
                            batch.run_id,
                            call_id,
                            denied=call_id in denied,
                            skipped=call_id in skipped,
                        )
                    )
                    for call_id in batch.call_ids
                )
            )
        )

    async def tool_call(self, req: ToolRequest) -> bool:
        """Run one tool call; returns whether it errored. Safe to repeat (keyed events)."""
        sid, run_id, call_id = req.session_id, req.run_id, req.call_id
        if (done := await self._keyed(sid, run_id, f"{call_id}:end")) is not None:
            return bool(done.is_error)
        call, step, assistant = await self._located(sid, run_id, call_id)
        if activity.in_activity() and activity.is_cancelled():
            raise asyncio.CancelledError  # stopped before it started: the stop answers it
        await self._still_held(sid)
        # The start is the claim (pi-temporal's dispatch claim): taken atomically, so a
        # call closed unrun never starts late, and a retried attempt of a call that did
        # start runs again only as its recovery class allows (Temporal retries it or not).
        if not await self._claim(
            sid,
            run_id,
            f"{call_id}:start",
            ToolStart(tool_call_id=call_id, tool_name=call["name"], args=call["arguments"]),
        ):
            start = await self._keyed(sid, run_id, f"{call_id}:start")
            if getattr(start, "not_run", False):
                return True  # closed before it ran: its end says so
        started = time.monotonic()
        tools = await self._memory.tools(sid)
        refused = refusal(loop_spec(await self._manifest(sid)), step, assistant, call)
        if refused is not None:
            result, is_error = denied_result(refused[1], refused[0]), True
        elif req.skipped:
            result = denied_result(SKIPPED_FOR_STEERING, "SKIPPED_FOR_USER_MESSAGE")
            is_error = True
        elif req.denied:
            result = denied_result("denied by approval", "APPROVAL_DENIED")
            is_error = True
        elif req.behind_unknown:
            result, is_error = denied_result(BEHIND_UNKNOWN, TOOL_NOT_RUN), True
        elif tools.handles(call["name"]) and not await self._catalog_has(sid, call["name"]):
            output = await tools.call(call["name"], call["arguments"])
            result, is_error = _platform_result(output.text, output.is_error), output.is_error
        elif call["name"] in SUBAGENT_TOOLS and not await self._catalog_has(sid, call["name"]):
            outcome = await self._subagents.call(
                sid, run_id, call_id, call["name"], call["arguments"]
            )
            result, is_error = render(outcome), outcome.is_error
        else:
            runtime = await self._runtime(sid)
            provider = runtime.seams.tool_provider if runtime.seams else None
            if (denial := self._manager.tool_denial(sid, call["name"], provider)) is not None:
                result, is_error = denied_result(denial, "FILE_EFFECTS_DENIED"), True
            elif (
                hand := await self._manager.hand_for_call(
                    sid,
                    call["name"],
                    provider,
                    (args := take_hand(dict(call["arguments"]), self._manager.hand_names(sid)))[1],
                )
            ).unavailable:
                result = denied_result(hand.unavailable.error or "", hand.unavailable.code or "")
                is_error = True
            else:
                result, is_error = await self._run_in_hand(
                    sid, run_id, call_id, call, args, hand, runtime, provider, started
                )
        await self._still_held(sid)  # a result of a sandbox that no longer serves is refused
        if not await self._claim(
            sid,
            run_id,
            f"{call_id}:end",
            ToolEnd(
                tool_call_id=call_id,
                tool_name=call["name"],
                result=result,
                is_error=is_error,
                duration_ms=int((time.monotonic() - started) * 1000),
            ),
        ):
            # A closer answered it first (a stop, a lost worker): its answer stands, so
            # the call has one result.
            done = await self._keyed(sid, run_id, f"{call_id}:end")
            return bool(done.is_error) if done is not None else is_error
        return is_error

    async def _run_in_hand(
        self,
        sid: str,
        run_id: str,
        call_id: str,
        call: dict[str, Any],
        args: Any,
        hand: Any,
        runtime: Any,
        provider: Any,
        started: float,
    ) -> tuple[dict[str, Any], bool]:
        """Run a catalog tool in the session's sandbox, as ``tool_call`` asks: stopped with
        the run (its command killed, what it did recorded) or past its time limit."""
        row = await self._manager._get_session_row(sid)
        invocation = {"id": call_id, "name": call["name"], "arguments": args[0]}
        named = hand.key is not None and hand.key != sid
        stop = threading.Event()
        running = asyncio.ensure_future(
            runtime.loop().run_tool(
                invocation,
                tenant_id=row.tenant_id,
                env=await self._manager.tool_env(sid),
                workspace=self._manager.hand_workspace(hand.key) if named and hand.key else None,
                stop=stop,
            )
        )
        over_limit = False
        try:
            try:
                end = await asyncio.wait_for(asyncio.shield(running), TOOL_CALL_LIMIT_S)
            except TimeoutError:
                # Past the per-call limit (pi-temporal's tool timeout): stop it as a
                # stop would, so a hung tool frees its worker slot, and tell the model;
                # the run goes on. A tool that ignores the stop is left behind.
                over_limit = True
                stop.set()
                end = await asyncio.wait_for(asyncio.shield(running), STOP_GRACE_S)
        except TimeoutError:
            self._manager.call_ended(hand)
            end = None
        except asyncio.CancelledError:
            # The run was stopped (the user, or its hard deadline): kill what the
            # tool runs and record what it did (pi-temporal waits for a stopped
            # tool to report), so nothing lands after the stop and the model reads
            # the real result, not "unknown". A worker shutting down is not a stop:
            # the call is left as a crash leaves it, for recovery elsewhere.
            if not activity.in_activity() or activity.is_worker_shutdown():
                self._manager.call_ended(hand)
                raise
            stop.set()
            try:
                end = await asyncio.wait_for(asyncio.shield(running), STOP_GRACE_S)
            except BaseException:  # noqa: BLE001 — not ended in time (or cancelled again): left unknown
                self._manager.call_ended(hand)
                raise asyncio.CancelledError from None
            checked = await self._manager.after_call(
                sid, hand, call["name"], provider, _as_tool_result(end.result)
            )
            await self._claim(
                sid,
                run_id,
                f"{call_id}:end",
                ToolEnd(
                    tool_call_id=call_id,
                    tool_name=call["name"],
                    result=asdict(checked),
                    is_error=tool_result_failed(asdict(checked)),
                    duration_ms=int((time.monotonic() - started) * 1000),
                ),
            )
            raise
        except BaseException:
            self._manager.call_ended(hand)
            raise
        if end is not None:
            checked = await self._manager.after_call(
                sid, hand, call["name"], provider, _as_tool_result(end.result)
            )
            result, is_error = asdict(checked), not checked.ok
        if over_limit:
            result = denied_result(
                f"The tool call ran past its time limit ({TOOL_CALL_LIMIT_S:g} s) and was "
                "stopped. Anything it started was killed; check what it changed before "
                "you run it again.",
                TOOL_TIMEOUT,
            )
            is_error = True
        return result, is_error


    # --- approvals -----------------------------------------------------------

    async def request_approval(self, ask: ApprovalAsk) -> str:
        """Park the call for a human; the workflow holds the durable timer."""
        approvals = self._manager._approvals
        request_key = f"{ask.run_id}:{ask.call_id}"
        existing = await approvals.get_by_harness_request(ask.session_id, request_key)
        approval_id = (
            existing.approval_id
            if existing is not None
            else await approvals.request(
                ask.session_id,
                ask.run_id,
                ask.needed.prompt,
                ask.needed.options,
                int(ask.needed.timeout_s),
                harness_request_id=request_key,
            )
        )
        await self._record(
            ask.session_id,
            ask.run_id,
            f"{ask.call_id}:approval",
            ApprovalRequired(
                approval_id=approval_id,
                prompt=ask.needed.prompt,
                options=ask.needed.options,
                timeout_s=ask.needed.timeout_s,
                harness_request_id=request_key,
                metadata={"tool": ask.tool_name, "tool_call_id": ask.call_id},
            ),
        )
        return approval_id

    async def resolve_approval(self, decision: ApprovalDecision) -> str:
        """Settle an approval (human choice or timeout); returns the choice that stands."""
        approvals = self._manager._approvals
        current = await approvals.get(decision.approval_id)
        if current is not None and current.status != "pending":
            return str(current.choice)  # someone (sweeper or human) already settled it
        approval = (
            await approvals.time_out(decision.approval_id, decision.choice)
            if decision.timed_out
            else await approvals.respond(
                decision.approval_id, decision.choice, responded_by=decision.responded_by
            )
        )
        await self._manager._record(
            decision.session_id,
            ApprovalResolved(
                approval_id=approval.approval_id,
                choice=str(approval.choice),
                status=str(approval.status),
                responded_by=approval.responded_by,
            ),
            turn_id=approval.turn_id,
            trace_id=None,
            event_key=f"approval:{approval.approval_id}:resolved",
        )
        return str(approval.choice)

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _is_stepped(runtime: object) -> bool:
        """githi with a model gateway is driven step by step; anything else runs whole."""
        gateway = getattr(getattr(runtime, "seams", None), "model_gateway", None)
        return isinstance(runtime, GithiRuntime) and gateway is not None

    async def _runtime(self, session_id: str) -> GithiRuntime:
        """The session's runtime, resumed here if cold: for work beside its sandbox."""
        await self._manager.ensure_ready(session_id)
        await self._manager.refresh_credentials(session_id)  # its subagents call the model too
        runtime = self._manager._registry.get(session_id)
        assert isinstance(runtime, GithiRuntime), "stepped turns need the githi runtime"
        return runtime

    async def _brain(self, session_id: str) -> GithiRuntime:
        """What a model step needs (seams, model, prompt, tool schemas), on any worker."""
        runtime = await self._manager.brain(session_id)
        await self._manager.refresh_credentials(session_id)
        assert isinstance(runtime, GithiRuntime), "stepped turns need the githi runtime"
        return runtime

    async def _tool_schemas(self, session_id: str, loop: AgentLoop) -> list[dict[str, Any]]:
        """The session's catalog tools, then the memory and subagent tools it does not shadow."""
        hands = self._manager.hand_names(session_id)
        provider = loop.tool_provider
        schemas = [
            with_hand_argument(s, hands) if needs_workspace(provider, s["function"]["name"]) else s
            for s in loop.tool_schemas()
        ]
        names = {s["function"]["name"] for s in schemas}
        platform = (await self._memory.tools(session_id)).schemas()
        platform += await self._subagents.schemas(session_id)
        # pi's own tools first, in pi's order (read, bash, edit, write …), as its prompt lists them.
        return in_pi_order(schemas + [s for s in platform if s["function"]["name"] not in names])

    async def _catalog_has(self, session_id: str, name: str) -> bool:
        return (await self._runtime(session_id)).loop().offers(name)

    async def _manifest(self, session_id: str) -> Any:
        row = await self._manager._get_session_row(session_id)
        return self._manager._pinned_manifest(row)

    async def _record(
        self, session_id: str, run_id: str, key: str, event: Any, *, trace_id: str | None = None
    ) -> int:
        return await self._manager._record(
            session_id, event, turn_id=run_id, trace_id=trace_id, event_key=f"{run_id}:{key}"
        )

    async def _still_held(self, session_id: str) -> None:
        """Raise SessionMoved once another worker holds the session (supervisor.session.holds)."""
        if not await self._manager.holds.held(session_id):
            raise SessionMoved(f"session {session_id} is held by another worker now")

    async def _claim(self, session_id: str, run_id: str, key: str, event: Any) -> bool:
        """Journal ``event`` under ``key`` if no event holds it yet; whether this one won."""
        seq = await self._manager._record_once(
            session_id, event, turn_id=run_id, trace_id=None, event_key=f"{run_id}:{key}"
        )
        return seq is not None

    async def _keyed(self, session_id: str, run_id: str, key: str) -> Any | None:
        envelope = await self._reader.event(
            session_id, keyed_event_id(session_id, f"{run_id}:{key}")
        )
        return None if envelope is None else envelope.payload

    async def _call(self, session_id: str, run_id: str, call_id: str) -> dict[str, Any]:
        return (await self._located(session_id, run_id, call_id))[0]

    async def _located(
        self, session_id: str, run_id: str, call_id: str
    ) -> tuple[dict[str, Any], int, MessageEnd]:
        """The call, the step that made it (each step journals one assistant message), and
        that assistant message."""
        step = -1
        for envelope in await self._reader.read_turn(session_id, run_id):
            event = envelope.payload
            if isinstance(event, MessageEnd) and event.role == "assistant":
                step += 1
                for part in event.content:
                    if part.get("type") == "tool_call" and part.get("id") == call_id:
                        call = {"name": str(part["name"]), "arguments": dict(part["arguments"])}
                        return call, step, event
        raise LookupError(f"tool call {call_id} not found in run {run_id}")

    async def _await_ends(self, sid: str, run_id: str, call_ids: list[str]) -> None:
        """Wait up to ``STOP_WAIT_S`` for each started call of ``call_ids`` to record its end."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STOP_WAIT_S
        for call_id in call_ids:
            start = await self._keyed(sid, run_id, f"{call_id}:start")
            if start is None or getattr(start, "not_run", False):
                continue  # never started: nothing will record it
            while await self._keyed(sid, run_id, f"{call_id}:end") is None:
                if loop.time() >= deadline:
                    return
                await asyncio.sleep(0.25)

    async def close_lost_call(self, lost: LostCall) -> None:
        """Journal an unknown outcome for a call that started; a no-op once it has ended.

        Keyed like tool_call's own ToolEnd, so whichever lands first is the only one.
        A direct invoke (no run) is completed through the tool outbox, which locks
        its row: a zombie worker finishing later gets this outcome back, not a
        second ToolEnd, and a re-sent invoke returns it instead of running again.
        """
        sid, run_id, call_id = lost.session_id, lost.run_id, lost.call_id
        error = UNKNOWN_OUTCOME
        if run_id is None:
            outbox = self._manager._tool_outbox
            row = await outbox.get_by_key(idempotency_key(sid, call_id))
            if row is not None:  # None: the invoke never began, so nothing is open
                await outbox.complete(
                    sid,
                    tool_call_id=call_id,
                    tool_name=row.tool_name,
                    result=ToolResult(ok=False, error=error, code=TOOL_OUTCOME_UNKNOWN),
                    trace_id=None,
                )
            return
        if await self._keyed(sid, run_id, f"{call_id}:end") is not None:
            return  # it ended after all
        # Not run only if this claim of its start wins (nothing started it, and now nothing
        # can); otherwise it may have run.
        call = await self._call(sid, run_id, call_id)
        claimed = await self._claim(
            sid,
            run_id,
            f"{call_id}:start",
            ToolStart(tool_call_id=call_id, tool_name=call["name"], args=call["arguments"], not_run=True),
        )
        start = None if claimed else await self._keyed(sid, run_id, f"{call_id}:start")
        started = not claimed and not getattr(start, "not_run", False)
        await self._claim(  # lost: the call ended after all, with its own result
            sid,
            run_id,
            f"{call_id}:end",
            ToolEnd(
                tool_call_id=call_id,
                tool_name=call["name"],
                result=(
                    {"error": error, "code": TOOL_OUTCOME_UNKNOWN, "reason": lost.reason}
                    if started
                    else {"error": NOT_RUN_OUTCOME, "code": TOOL_NOT_RUN, "reason": lost.reason}
                ),
                is_error=True,
            ),
        )

    async def _close_interrupted_run(self, session_id: str, run_id: str) -> None:
        """A run left open by a crashed whole-turn activity is closed before a new one starts."""
        open_run = await self._reader.open_run(session_id)
        if open_run is None or open_run[1] == run_id:
            return
        await self._manager._record(
            session_id,
            ErrorEvent(
                error_class="TurnInterrupted",
                retryable=False,
                detail=f"turn starting at seq {open_run[0]} was interrupted by a worker failure",
            ),
            turn_id=open_run[1],
            trace_id=None,
            event_key=f"interrupted:{open_run[0]}",
        )
