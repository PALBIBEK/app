"""Workflow-driven githi turns: one model call or one tool call per activity.

The workflow decides what happens next; these steps do the work against the
session's live runtime (its pinned seams) and journal every event with a key
derived from the run, step and tool call. A retried or re-homed step therefore
never writes an event twice, and the run's conversation is always rebuilt from
the journal, so tool arguments and results never enter workflow history.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from temporalio import activity

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    ErrorEvent,
    MemoryInjected,
    MemoryRecall,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from supervisor.agent.runtimes.githi.loop import MAX_TOOL_ITERATIONS, AgentLoop
from supervisor.agent.runtimes.githi.runtime import GithiRuntime
from supervisor.harness import RecoveryAction
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import keyed_event_id
from supervisor.memory.binding import Strategy
from supervisor.memory.classification import OneShot
from supervisor.memory.history import message_text
from supervisor.memory.platform import gateway_one_shot
from supervisor.memory.tools import PLATFORM_TOOLS
from supervisor.memory.turn import SessionMemory
from supervisor.policy.guards import call_hash
from supervisor.seams import ToolResult
from supervisor.session import SessionManager
from supervisor.session.tool_outbox import idempotency_key
from supervisor.temporal.context_memory import PinnedContextMemory
from supervisor.temporal.contracts import (
    ApprovalAsk,
    ApprovalDecision,
    ApprovalNeeded,
    CaptureConfig,
    EndRunRequest,
    GuardSettings,
    LostCall,
    PlannedCall,
    RetryMode,
    RunPlan,
    StepRequest,
    StepResult,
    ToolRequest,
    TurnOutcome,
    TurnRequest,
)
from supervisor.temporal.subagent_tools import (
    SUBAGENT_RESULT,
    SUBAGENT_TOOLS,
    SubagentTools,
    render,
)

TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"


def run_id_for(session_id: str, command_id: str) -> str:
    """One run per (session, command): a retried or re-homed begin_run finds the same run."""
    return uuid5(NAMESPACE_URL, f"yal-run:{session_id}:{command_id}").hex


async def heartbeating[T](work: Awaitable[T]) -> T:
    """Await ``work`` while heartbeating, so a dead worker is noticed and aborts arrive."""
    task = asyncio.ensure_future(work)
    timeout = activity.info().heartbeat_timeout or timedelta(seconds=30)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=timeout.total_seconds() / 3)
            if done:
                return task.result()
            activity.heartbeat()
    finally:
        task.cancel()


def conversation(prior: list[dict[str, Any]], run: list[Any]) -> list[dict[str, Any]]:
    """The run's messages, rebuilt from its journalled events on top of earlier history.

    Memory shown for the run (notebook facts, and whatever the pinned recall backend
    found) comes right before the run's user message, so it is part of the history
    later runs carry (what the next run's delta assumes is held).
    """
    messages = list(prior)
    user_at: int | None = None
    for envelope in run:
        event = envelope.payload
        if isinstance(event, (MemoryInjected, MemoryRecall)):
            if event.text:
                at = len(messages) if user_at is None else user_at
                messages.insert(
                    at, {"role": "system", "content": [{"type": "text", "text": event.text}]}
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
            messages.append(message)
        elif isinstance(event, ToolEnd):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": event.tool_call_id,
                    "content": [{"type": "text", "text": json.dumps(event.result)}],
                }
            )
    return messages


def _user_text(run: list[Any]) -> str:
    for envelope in run:
        event = envelope.payload
        if isinstance(event, MessageEnd) and event.role == "user":
            return message_text(event)
    return ""


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

    @property
    def memory(self) -> SessionMemory:
        return self._memory

    async def one_shot_for(self, session_id: str) -> OneShot:
        """One-shot calls (classification, extraction) with the session's own model."""
        row = await self._manager._get_session_row(session_id)
        seams = self._manager._seams_for_session(session_id, row)
        if seams is None or seams.model_gateway is None:
            return self._manager.memory.one_shot
        return gateway_one_shot(seams.model_gateway, model=self._manager.session_model(row))

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
        # Between turns: compact what this run carries over, per the pinned memory profile.
        await heartbeating(
            self._pinned.compact(session_id, run_id, await self._memory.prior_history(session_id))
        )
        guards = (await self._manifest(session_id)).guards
        guard = (
            GuardSettings(str(guards.repetition), str(guards.oscillation), guards.stall_iterations)
            if guards is not None
            else None
        )
        return RunPlan(
            run_id,
            stepped=True,
            guard=guard,
            max_steps=MAX_TOOL_ITERATIONS,
            capture=await self._capture_plan(session_id),
        )

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
        if req.lost_call_id is not None:
            await self.close_lost_call(LostCall(sid, run_id, req.lost_call_id, req.lost_reason))
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

    # --- model ---------------------------------------------------------------

    async def model_step(self, req: StepRequest) -> StepResult:
        sid, run_id = req.session_id, req.run_id
        key = f"{req.step}:assistant"
        if (assistant := await self._keyed(sid, run_id, key)) is None:
            state = await self._manager._budget.check(sid)
            if state.exceeded:
                return await self._budget_stop(sid, state.exceeded_reason)
            runtime = await self._runtime(sid)
            loop = runtime.loop()
            await self._record(sid, run_id, f"{req.step}:turn", TurnStart())
            run = await self._reader.read_turn(sid, run_id)
            if req.step == 0:
                await self._memory.recall(sid, run_id, _user_text(run), self._record)
                await self._pinned.recall(sid, run_id, _user_text(run))
                run = await self._reader.read_turn(sid, run_id)
            chat = loop.messages_for_model(
                conversation(await self._memory.prior_history(sid), run)
            )
            schemas = await self._tool_schemas(sid, loop)
            assert runtime.seams is not None and runtime.seams.model_gateway is not None
            completion = await heartbeating(
                asyncio.to_thread(
                    runtime.seams.model_gateway.complete, chat, schemas, model=runtime.model
                )
            )
            calls = AgentLoop.extract_tool_calls(completion)
            usage = AgentLoop.usage_from_completion(completion)
            text = (completion.message.content if completion.message else "") or ""
            assistant = MessageEnd(
                role="assistant",
                content=[{"type": "text", "text": text}]
                + [{"type": "tool_call", **call} for call in calls],
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
        # Checked on replay too, so a retried step cannot hand out calls past the limit.
        if (spent := await self._manager._budget.check(sid)).exceeded:
            return await self._budget_stop(sid, spent.exceeded_reason)
        calls = [p for p in assistant.content if p.get("type") == "tool_call"]
        if not calls:
            return StepResult(calls=[], final=True)
        manifest = await self._manifest(sid)
        return StepResult(calls=[self._plan(manifest, call) for call in calls], final=False)

    def _plan(self, manifest: Any, call: dict[str, Any]) -> PlannedCall:
        name = str(call["name"])
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
        )

    async def _budget_stop(self, session_id: str, reason: str | None) -> StepResult:
        """Budget is spent: close the session to new turns (it reopens if limits change)."""
        if await self._manager.session_status(session_id) == "active":
            await self._manager._policy_abort(
                session_id,
                self._manager._registry.get(session_id),
                error_class="BudgetExceeded",
                detail=reason or "budget exceeded",
                status="budget_exceeded",
            )
        return StepResult(calls=[], final=True, stop_reason="budget_exceeded")

    # --- tools ---------------------------------------------------------------

    async def tool_call(self, req: ToolRequest) -> bool:
        """Run one tool call; returns whether it errored. Safe to repeat (keyed events)."""
        sid, run_id, call_id = req.session_id, req.run_id, req.call_id
        if (done := await self._keyed(sid, run_id, f"{call_id}:end")) is not None:
            return bool(done.is_error)
        call = await self._call(sid, run_id, call_id)
        await self._record(
            sid,
            run_id,
            f"{call_id}:start",
            ToolStart(tool_call_id=call_id, tool_name=call["name"], args=call["arguments"]),
        )
        tools = await self._memory.tools(sid)
        if req.denied:
            result: dict[str, Any] = {"error": "denied by approval", "code": "APPROVAL_DENIED"}
            is_error = True
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
                result, is_error = {"error": denial, "code": "FILE_EFFECTS_DENIED"}, True
            else:
                row = await self._manager._get_session_row(sid)
                invocation = {"id": call_id, "name": call["name"], "arguments": call["arguments"]}
                end = await runtime.loop().run_tool(invocation, tenant_id=row.tenant_id)
                result, is_error = end.result, end.is_error
        await self._record(
            sid,
            run_id,
            f"{call_id}:end",
            ToolEnd(tool_call_id=call_id, tool_name=call["name"], result=result, is_error=is_error),
        )
        return is_error

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
            else await approvals.respond(decision.approval_id, decision.choice)
        )
        await self._manager._record(
            decision.session_id,
            ApprovalResolved(
                approval_id=approval.approval_id,
                choice=str(approval.choice),
                status=str(approval.status),
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
        await self._manager.ensure_ready(session_id)
        runtime = self._manager._registry.get(session_id)
        assert isinstance(runtime, GithiRuntime), "stepped turns need the githi runtime"
        return runtime

    async def _tool_schemas(self, session_id: str, loop: AgentLoop) -> list[dict[str, Any]]:
        """The session's catalog tools, then the memory and subagent tools it does not shadow."""
        schemas = loop.tool_schemas()
        names = {s["function"]["name"] for s in schemas}
        platform = (await self._memory.tools(session_id)).schemas()
        platform += await self._subagents.schemas(session_id)
        return schemas + [s for s in platform if s["function"]["name"] not in names]

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

    async def _keyed(self, session_id: str, run_id: str, key: str) -> Any | None:
        envelope = await self._reader.event(
            session_id, keyed_event_id(session_id, f"{run_id}:{key}")
        )
        return None if envelope is None else envelope.payload

    async def _call(self, session_id: str, run_id: str, call_id: str) -> dict[str, Any]:
        for envelope in await self._reader.read_turn(session_id, run_id):
            event = envelope.payload
            if isinstance(event, MessageEnd) and event.role == "assistant":
                for part in event.content:
                    if part.get("type") == "tool_call" and part.get("id") == call_id:
                        return {"name": str(part["name"]), "arguments": dict(part["arguments"])}
        raise LookupError(f"tool call {call_id} not found in run {run_id}")

    async def close_lost_call(self, lost: LostCall) -> None:
        """Journal an unknown outcome for a call that started; a no-op once it has ended.

        Keyed like tool_call's own ToolEnd, so whichever lands first is the only one.
        A direct invoke (no run) is completed through the tool outbox, which locks
        its row: a zombie worker finishing later gets this outcome back, not a
        second ToolEnd, and a re-sent invoke returns it instead of running again.
        """
        sid, run_id, call_id = lost.session_id, lost.run_id, lost.call_id
        error = f"outcome unknown: {lost.reason}"
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
        await self._record(
            sid,
            run_id,
            f"{call_id}:end",
            ToolEnd(
                tool_call_id=call_id,
                tool_name=await self._tool_name(sid, run_id, call_id),
                result={"error": error, "code": TOOL_OUTCOME_UNKNOWN},
                is_error=True,
            ),
        )

    async def _tool_name(self, session_id: str, run_id: str, call_id: str) -> str:
        return (await self._call(session_id, run_id, call_id))["name"]

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
