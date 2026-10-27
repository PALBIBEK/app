"""Workflow-driven githi turns: one model call or one tool call per activity.

The workflow decides what happens next; these steps do the work against the
session's live runtime (its pinned seams) and journal every event with a key
derived from the run, step and tool call. A retried or re-homed step therefore
never writes an event twice, and the run's conversation is always rebuilt from
the journal, so tool arguments and results never enter workflow history.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

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
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from supervisor.agent.runtimes.githi.classify import (
    FINAL_STEP_NOTICE,
    is_context_overflow,
    is_final_step,
    is_retryable,
    loop_spec,
    refusal,
    retry_delay,
    retry_spec,
    steps_left_notice,
)
from supervisor.agent.runtimes.githi.loop import AgentLoop
from supervisor.agent.runtimes.githi.render import tool_result_text
from supervisor.agent.runtimes.githi.runtime import GithiRuntime
from supervisor.hands.keys import take_hand, with_hand_argument
from supervisor.harness import RecoveryAction
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
from supervisor.session import SessionManager
from supervisor.session.tool_outbox import idempotency_key
from supervisor.temporal.context_memory import PinnedContextMemory
from supervisor.temporal.contracts import (
    COMPACT,
    RETRY,
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
    for envelope in run:
        event = envelope.payload
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
                    "content": [{"type": "text", "text": tool_result_text(event.result)}],
                }
            )
    return messages


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
            self._pinned.compact(
                session_id,
                run_id,
                await self._memory.prior_history(session_id),
                await self._memory.bounded(session_id),
            )
        )
        await self._announce_background(session_id, run_id)
        manifest = await self._manifest(session_id)
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
            capture=await self._capture_plan(session_id),
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
            state = await self._manager._budget.check(sid)
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
            chat = loop.messages_for_model(history, schemas)
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
            try:
                completion = await heartbeating(
                    asyncio.to_thread(
                        runtime.seams.model_gateway.complete,
                        chat,
                        schemas,
                        model=runtime.model,
                        **choice,
                    )
                )
            except Exception as exc:
                # The provider (or the model layer) says the request does not fit: nothing
                # was answered. The workflow compacts and asks again, once; a step that
                # still overflows after that ends the run saying so. Not the first step:
                # earlier turns were compacted as the run began, so what overflows is
                # the request itself, which a summary cannot shrink.
                if req.step > 0 and req.can_compact and is_context_overflow(exc):
                    return StepResult(calls=[], final=False, kind=COMPACT)
                if req.can_retry:
                    return await self._retry_or_fail(req, exc)
                raise
            usage = AgentLoop.usage_from_completion(completion)
            assistant = MessageEnd(
                role="assistant",
                content=AgentLoop.assistant_content(completion),
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
        spec = loop_spec(manifest)
        plans = [
            self._plan(manifest, call, refused=refusal(spec, req.step, assistant, call) is not None)
            for call in calls
        ]
        return StepResult(calls=plans, final=False)

    async def _retry_or_fail(self, req: StepRequest, exc: Exception) -> StepResult:
        """pi's auto-retry: a call that may pass is asked again after a durable wait.

        Journals ``ModelRetry`` (keyed by step and attempt, so a re-run activity does not
        announce it twice) and answers RETRY with the wait; the workflow sleeps on a
        timer, then runs the step again. A call that will fail the same way, or one out
        of retries, fails the step at once: no activity retries (Temporal's are for a
        lost worker, not a provider's answer).
        """
        spec = retry_spec(await self._manifest(req.session_id))
        attempt = req.attempt + 1
        retryable = is_retryable(exc)
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

    async def compact(self, req: StepRequest) -> bool:
        """Compact the conversation so far, this run included, before ``req.step``'s request."""
        sid, run_id = req.session_id, req.run_id
        await self._brain(sid)
        run = await self._reader.read_turn(sid, run_id)
        history = conversation(await self._memory.prior_history(sid), run)
        return await heartbeating(
            self._pinned.compact_run(
                sid, run_id, req.step, history, await self._reader.read(sid), request=_user_text(run)
            )
        )

    def _plan(self, manifest: Any, call: dict[str, Any], *, refused: bool = False) -> PlannedCall:
        name = str(call["name"])
        if refused:  # answered without running: nothing to approve, safe to repeat
            return PlannedCall(
                call_id=str(call["id"]),
                name=name,
                args_hash=call_hash(name, dict(call.get("arguments") or {})),
                retry=RetryMode.RETRY,
                approval=None,
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
        call, step, assistant = await self._located(sid, run_id, call_id)
        await self._record(
            sid,
            run_id,
            f"{call_id}:start",
            ToolStart(tool_call_id=call_id, tool_name=call["name"], args=call["arguments"]),
        )
        started = time.monotonic()
        tools = await self._memory.tools(sid)
        refused = refusal(loop_spec(await self._manifest(sid)), step, assistant, call)
        if refused is not None:
            result, is_error = denied_result(refused[1], refused[0]), True
        elif req.denied:
            result = denied_result("denied by approval", "APPROVAL_DENIED")
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
                row = await self._manager._get_session_row(sid)
                invocation = {"id": call_id, "name": call["name"], "arguments": args[0]}
                named = hand.key is not None and hand.key != sid
                try:
                    end = await runtime.loop().run_tool(
                        invocation,
                        tenant_id=row.tenant_id,
                        env=await self._manager.tool_env(sid),
                        workspace=self._manager.hand_workspace(hand.key) if named and hand.key else None,
                    )
                except BaseException:
                    self._manager.call_ended(hand)
                    raise
                checked = await self._manager.after_call(
                    sid, hand, call["name"], provider, _as_tool_result(end.result)
                )
                result, is_error = asdict(checked), not checked.ok
        await self._record(
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
