"""The tool-execution pipeline.

Separated from `agent.py` so the "what happens to one batch of tool calls"
logic can be read and tested on its own. Two invariants govern everything
here:

1. **Every tool call produces exactly one tool result message**, whatever
   happens -- success, crash, block, malformed arguments. A provider rejects
   any assistant message whose `tool_calls` are not each answered, so a
   missing result is not a degraded run, it is a 400 on the next request.
2. **Nothing raises.** A tool that fails is information the model can act
   on; ending the run instead throws away everything done so far over a
   mistyped path.
"""

from __future__ import annotations

import time
from .events import Event, EventType
from .hooks import BeforeToolCall, Hooks
from dataclasses import dataclass, field
from typing import Any, Callable

from .state import AgentState
from ..harness.compaction import DEFAULT_LIMIT_TOKENS, compact
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry
from ..types import (
    AssistantMessage,
    Message,
    StopReason,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from ..harness.messages import convert_to_llm

FINAL_TURN_NOTICE = (
    "This is your FINAL turn -- no tools are available. Give your complete "
    "answer now using what you already know. State your conclusion plainly, "
    "and say which parts are unverified rather than leaving them out."
)


@dataclass
class ExecutedToolBatch:
    """Wire messages plus the batch-level signals that do not survive them.

    `ToolResultMessage` is the provider's flat shape and has no `terminate`
    field, so converting straight to it silently drops the flag and the loop
    can never honour it. pi keeps the richer outcome alongside for exactly
    this reason.
    """

    messages: list[ToolResultMessage] = field(default_factory=list)
    terminate: bool = False
    interrupted: bool = False
    """Set when the batch stopped because the user pressed ESC. Distinct from
    `terminate`, which a tool requests -- the loop reports them differently
    because one is the agent finishing and the other is the user stopping."""

    def __iter__(self):
        return iter(self.messages)

    def __len__(self) -> int:
        return len(self.messages)


def execute_tool_calls(
    message: AssistantMessage,
    tools: ToolRegistry,
    ctx: ToolContext,
    state: AgentState,
    hooks: Hooks,
    interrupt: Any = None,
) -> ExecutedToolBatch:
    results: list[ToolResultMessage] = []
    outcomes: list[Any] = []
    if hasattr(ctx, "extra"):
        ctx.extra["interrupt"] = interrupt
        ctx.extra["on_output"] = lambda stream, text: state.emit(
            Event(EventType.TOOL_PROGRESS, {"stream": stream, "text": text}))

    for call in message.tool_calls:
        # Checked before each call rather than only at the end of the batch:
        # the model routinely emits several calls at once, and a user who
        # pressed ESC should not watch four more commands run.
        if interrupt is not None and interrupt.is_set():
            results.extend(_interrupted_results(message.tool_calls[len(results):]))
            return ExecutedToolBatch(messages=results, terminate=True,
                                     interrupted=True)

        state.record_tool_call(call.name)
        state.emit(Event(EventType.TOOL_START, {"tool": call.name, "args": call.arguments}))

        # Malformed arguments never reach the tool: the model sent something
        # unparseable, so running with silently-empty arguments would do the
        # wrong thing rather than nothing.
        if call.malformed:
            results.append(ToolResultMessage(
                tool_call_id=call.id, is_error=True,
                content=(f"the arguments for {call.name} were not valid JSON and could "
                         "not be parsed. Send the call again with well-formed JSON."),
            ))
            outcomes.append(None)
            continue

        arguments = call.arguments
        decision = hooks.before_tool_call(BeforeToolCall(call=call, state=state))
        if decision is not None:
            if decision.block:
                # Still a result, not a silent skip -- see invariant 1.
                results.append(ToolResultMessage(
                    tool_call_id=call.id, is_error=True,
                    content=decision.reason or f"the {call.name} call was blocked by policy.",
                ))
                state.emit(Event(EventType.TOOL_END,
                                 {"tool": call.name, "blocked": True}))
                outcomes.append(None)
                continue
            if decision.arguments is not None:
                arguments = decision.arguments

        started = time.monotonic()
        result = tools.execute(call.name, arguments, ctx)
        duration_ms = int((time.monotonic() - started) * 1000)
        hooks.after_tool_call(call, result)
        # `.text` flattens the content blocks: a tool result on the wire is a
        # string, so images and structured details stay harness-side. This is
        # the boundary where pi's richer AgentToolResult meets the provider's
        # flat tool-result schema.
        results.append(ToolResultMessage(tool_call_id=call.id, content=result.text,
                                         is_error=result.is_error))
        outcomes.append(result)

        # A tool may hand back tools. Registering here makes them callable
        # from the next turn, which is what runtime capability synthesis needs.
        for name in getattr(result, "added_tool_names", []) or []:
            state.emit(Event(EventType.TOOL_END, {"tool": name, "added": True}))

        state.emit(Event(EventType.TOOL_END,
                         {"tool": call.name, "tool_id": call.id,
                          "is_error": result.is_error, "result": result.text,
                          "duration_ms": duration_ms}))

    return ExecutedToolBatch(messages=results,
                             terminate=should_terminate_batch(outcomes))


# ===========================================================================
# pi's `runLoop` (agent-loop.ts). The first version here was "ask, run tools,
# repeat" — correct for the happy path and missing everything that matters
# when a response is truncated, a tool wants to stop the batch, the user
# intervenes mid-run, or a phase needs to swap the prompt.
# ===========================================================================

from dataclasses import dataclass as _dataclass

from ..types import (
    AgentContext,
    AssistantMessage,
    PrepareNextTurnContext,
    ThinkingLevel,
    ToolResultMessage,
)


@_dataclass
class LoopConfig:
    max_turns: int = 30
    model: str | None = None
    thinking_level: ThinkingLevel | None = None

    context_limit_tokens: int = DEFAULT_LIMIT_TOKENS
    summarizer: Any = None
    """Compaction settings. Present here rather than in the caller because
    compaction must happen *between* turns, which only the loop can see."""

    warn_turns_remaining: int = 0
    force_answer_on_final_turn: bool = False
    """Both default off so the bare `run_loop` stays pi-shaped; `Agent`
    switches them on."""

    on_message: Callable[[Message], None] | None = None
    """Called for every message appended to the transcript. This is how
    session persistence reaches a loop that must not know what a session is."""
    on_compaction: Callable[[list[Message]], None] | None = None

    interrupt: Any = None
    """Anything with `is_set()`. Polled between tools and between stream
    chunks -- the two places a turn actually spends time."""


def should_terminate_batch(results: list) -> bool:
    """Stop after this batch?

    Only when **every** result asks to. One tool must not end a run the
    others in the same message are still working on.

    The explicit length check is not redundant: `all()` over an empty list is
    vacuously true, so without it a turn that called no tools would terminate
    the loop.
    """
    return bool(results) and all(getattr(r, "terminate", False) for r in results)


def _complete(llm, view, turn_tools, state, interrupt, kwargs):
    """One turn's response, streamed if the client supports it.

    Streaming is detected rather than configured: a client either has
    `stream()` or it does not. Requiring it would break every fake LLM in the
    test suite -- and a caller with no UI has no reason to care.

    Text deltas are re-emitted as *agent* events so a renderer subscribes to
    the agent and stays ignorant of providers. The returned `Completion` is
    identical to the blocking one either way, which is what keeps everything
    downstream from needing to know which path ran.
    """
    from ..llm.client import Completion
    from ..llm.streaming import StreamEventType

    if not hasattr(llm, "stream"):
        return llm.complete(view, turn_tools, **kwargs)

    message, usage, finish_reason = None, None, "stop"
    for event in llm.stream(view, turn_tools, interrupt=interrupt, **kwargs):
        if event.type is StreamEventType.TEXT_DELTA:
            state.emit(Event(EventType.TEXT_DELTA, {"delta": event.delta}))
        elif event.type is StreamEventType.THINKING_START:
            state.emit(Event(EventType.THINKING_START, {}))
        elif event.type is StreamEventType.THINKING_DELTA:
            state.emit(Event(EventType.THINKING_DELTA, {"delta": event.delta}))
        elif event.type is StreamEventType.THINKING_END:
            state.emit(Event(EventType.THINKING_END, {"content": event.delta}))
        elif event.type is StreamEventType.DONE:
            message, usage, finish_reason = (event.message, event.usage,
                                             event.finish_reason)

    from ..types import AssistantMessage, Usage

    return Completion(message=message or AssistantMessage(content=""),
                      usage=usage or Usage(), finish_reason=finish_reason)


def _interrupted_results(calls: list) -> list[ToolResultMessage]:
    """Answer the calls that never ran, so the transcript stays valid.

    Every provider rejects an assistant message whose `tool_calls` are not
    each answered. Interrupting after the first of three calls would otherwise
    leave two unanswered, and the *next* thing the user typed would fail with
    a 400 mentioning neither the interrupt nor the missing results.

    The wording says interrupted, not failed, on purpose: a failure invites
    the model to retry, which is precisely what the user pressed ESC to stop.
    """
    return [
        ToolResultMessage(
            tool_call_id=call.id, is_error=False,
            content=("not executed -- the user interrupted the run before this "
                     "call. Do not retry it automatically; wait for their "
                     "instructions."),
        )
        for call in calls
    ]


def fail_truncated_tool_calls(message: AssistantMessage) -> list[ToolResultMessage]:
    """Turn every call in a truncated response into an error result.

    A `length` stop means the provider cut the output off mid-generation, so
    *any* of the tool calls may carry half-written JSON arguments. Executing
    them risks running a partial command — deleting the wrong path because
    the argument was clipped. Results are still produced for each call, or
    the transcript would hold orphaned tool_calls and the next request would
    be rejected.
    """
    return [
        ToolResultMessage(
            tool_call_id=call.id, is_error=True,
            content=("the response was truncated by the token limit, so this "
                     "tool call's arguments may be incomplete and it was not "
                     "executed. Reissue it in a shorter response."),
        )
        for call in message.tool_calls
    ]


def run_loop(*, llm, context: AgentContext, tools, tool_ctx, state, hooks,
             config: LoopConfig) -> AgentContext:
    """pi's two nested loops.

    The inner loop drains tool calls *and* steering messages; the outer loop
    restarts when a follow-up arrives after the agent would have stopped.
    Collapsing them into one loop loses the distinction between "redirect the
    work in progress" and "the work finished, here is more".
    """
    model = config.model
    thinking = config.thinking_level

    def append(message: Message) -> None:
        """The single write path into the transcript.

        `context.messages` and `state.messages` are two views of one
        conversation, and a caller may legitimately pass the *same* list as
        both -- `Agent` does, so that its transcript and the loop's are one
        object. Appending to each unconditionally would then double every
        message, so the identity check is load-bearing rather than defensive.
        """
        if context.messages is not state.messages:
            context.messages.append(message)
        state.append(message)
        if config.on_message:
            config.on_message(message)

    # Drained before the first request too: the user may have typed while the
    # previous turn was still streaming.
    pending = list(hooks.get_steering_messages())

    while True:                                   # outer: follow-up messages
        has_more_tool_calls = True

        while has_more_tool_calls or pending:     # inner: tools + steering
            if state.turns >= config.max_turns:
                state.stop_reason = StopReason.MAX_TURNS
                return context

            if hooks.should_stop(state):
                state.stop_reason = StopReason.STOPPED
                return context

            # Before the request, not only after: ESC pressed while tools were
            # running should not be rewarded with one more paid-for turn.
            if config.interrupt is not None and config.interrupt.is_set():
                state.stop_reason = StopReason.INTERRUPTED
                return context

            for message in pending:
                append(message)
            pending = []

            # In place: `context.messages` may be the very list `state.messages`
            # names, and rebinding one of them would silently split the two
            # views apart mid-run.
            compacted = compact(context.messages, config.context_limit_tokens,
                                summarizer=config.summarizer)
            if compacted != context.messages and config.on_compaction:
                config.on_compaction(compacted)
            context.messages[:] = compacted
            if context.messages is not state.messages:
                state.messages = list(context.messages)

            # The system prompt lives on the context, not in `messages`, so it
            # has to be prepended here -- otherwise `prepare_next_turn`
            # swapping the context has no visible effect and a phase change
            # silently does nothing.
            view = hooks.transform_context(context.messages)
            view = convert_to_llm(view)
            if context.system_prompt:
                view = [SystemMessage(context.system_prompt), *view]

            # Budget notices go on the per-turn *view* only. Written into the
            # transcript they would persist, so a stale "2 turns remaining"
            # would misinform every later turn.
            remaining = config.max_turns - state.turns
            is_final = remaining <= 1 and config.force_answer_on_final_turn
            if is_final:
                view = [*view, UserMessage(FINAL_TURN_NOTICE)]
            elif 0 < config.warn_turns_remaining and remaining <= config.warn_turns_remaining:
                view = [*view, UserMessage(
                    f"[{remaining} turns remaining. Stop gathering and write your "
                    "answer now if you have enough to conclude. Combine any last "
                    "checks into a single call rather than one per item.]")]

            state.emit(Event(EventType.TURN_START, {"turn": state.turns + 1}))

            kwargs = {}
            if model is not None:
                kwargs["model"] = model
            if thinking is not None:
                kwargs["thinking_level"] = thinking
            # Withholding tools on the final turn is structural where a warning
            # is only advice: with an empty tool list the model's sole
            # remaining move is to answer.
            turn_tools = [] if is_final else tools.to_wire()
            completion = _complete(llm, view, turn_tools, state,
                                   config.interrupt, kwargs)

            state.record_turn(completion.usage)
            message = completion.message
            append(message)

            if completion.finish_reason in ("error", "aborted"):
                # "aborted" is the user pressing ESC; "error" is the provider
                # failing. Reporting an interrupt as an error would tell the
                # UI to offer a retry for something deliberately stopped.
                state.stop_reason = (StopReason.INTERRUPTED
                                     if completion.finish_reason == "aborted"
                                     else StopReason.ERROR)
                state.emit(Event(EventType.TURN_END, {"turn": state.turns}))
                return context

            tool_results: list[ToolResultMessage] = []
            has_more_tool_calls = False
            # Bound before the branch: the truncated path below never executes
            # a batch, and the interrupt check afterwards must still have a
            # name to read.
            batch = ExecutedToolBatch()

            if message.tool_calls:
                if completion.finish_reason == "length":
                    tool_results = fail_truncated_tool_calls(message)
                    has_more_tool_calls = True
                else:
                    batch = execute_tool_calls(message, tools, tool_ctx, state,
                                               hooks, config.interrupt)
                    tool_results = batch.messages
                    has_more_tool_calls = not batch.terminate
                for result in tool_results:
                    append(result)

                # After appending, so the synthetic results are in the
                # transcript before the loop unwinds -- leaving them out is
                # the 400 the whole mechanism exists to avoid.
                if batch.interrupted:
                    state.stop_reason = StopReason.INTERRUPTED
                    state.emit(Event(EventType.TURN_END, {"turn": state.turns}))
                    return context

            state.emit(Event(EventType.TURN_END,
                             {"turn": state.turns, "tool_calls": len(message.tool_calls)}))

            update = hooks.prepare_next_turn(PrepareNextTurnContext(
                message=message, tool_results=tool_results, context=context,
                new_messages=state.messages, state=state))
            if update is not None:
                context = update.context or context
                model = update.model or model
                thinking = update.thinking_level or thinking

            if not message.tool_calls and not pending:
                break                              # nothing left to do this pass

            pending = list(hooks.get_steering_messages())

        follow_up = hooks.get_follow_up_messages()
        if follow_up:
            pending = list(follow_up)
            continue                               # the agent was done; it isn't now
        break

    if state.stop_reason is None:
        state.stop_reason = StopReason.DONE
    return context
