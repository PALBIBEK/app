"""Githi AgentLoop — one user turn → platform AgentEvents.

Emits supervisor vocabulary (``RunStart`` … ``RunEnd``).
LLM only via ``RuntimeSeams.model_gateway``; tools via ``tool_provider``.

Enhanced with features from githi/src/githi/agent/loop.py:
- Steering and follow-up message handling
- Context compaction with summarization
- Turn budget warnings
- Force answer on final turn
- Interrupt handling
- Full hook integration

Phase 1.5 (WP-A1): without a gateway, offline turn still emits paired
``ToolStart``/``ToolEnd`` (workspace probe) for SSE/session E2E proof.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from supervisor.agent.events import (
    AgentEvent,
    ErrorEvent,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.agent.seams import RuntimeSeams
from supervisor.seams import ChatMessage, ToolInvokeContext

from supervisor.agent.runtimes.githi.hooks import (
    Hooks,
    EMPTY_HOOKS,
    BeforeToolCall,
    ToolCall,
    ToolDecision,
    PrepareNextTurnContext,
)
from supervisor.agent.runtimes.githi.compaction import (
    compact,
    needs_compaction,
    DEFAULT_LIMIT_TOKENS,
    Summarizer,
)
from supervisor.agent.runtimes.githi.messages import convert_to_llm
from supervisor.agent.runtimes.githi.state import (
    MutableAgentState,
    StopReason,
    EventType,
    RuntimeEvent,
)

_log = logging.getLogger(__name__)
_MAX_TOOL_ITERATIONS = 8

# Turn budget notices
FINAL_TURN_NOTICE = (
    "This is your FINAL turn -- no tools are available. Give your complete "
    "answer now using what you already know. State your conclusion plainly, "
    "and say which parts are unverified rather than leaving them out."
)


@dataclass
class LoopConfig:
    """Configuration for the agent loop.

    From githi/src/githi/agent/loop.py.
    """
    max_turns: int = 30
    model: str | None = None
    thinking_level: Any = None

    context_limit_tokens: int = DEFAULT_LIMIT_TOKENS
    summarizer: Summarizer | None = None
    """Compaction settings. Present here rather than in the caller because
    compaction must happen *between* turns, which only the loop can see."""

    warn_turns_remaining: int = 3
    """Start telling the model how many turns are left once it gets this close."""

    force_answer_on_final_turn: bool = True
    """On the last turn, offer no tools at all."""

    on_message: Callable[[dict], None] | None = None
    """Called for every message appended to the transcript."""

    on_compaction: Callable[[list[dict]], None] | None = None

    interrupt: Any = None
    """Anything with `is_set()`. Polled between tools and between stream chunks."""


class AgentLoopError(Exception):
    """Unrecoverable failure inside the agent loop."""


@dataclass
class ExecutedToolBatch:
    """Results from executing a batch of tool calls.

    From githi/src/githi/agent/loop.py.
    """
    results: list[dict] = field(default_factory=list)
    terminate: bool = False
    interrupted: bool = False


def execute_tool_calls_sync(
    tool_calls: list[dict],
    tool_provider,
    hooks: Hooks,
    state: MutableAgentState,
    working_directory: str,
    session_id: str | None,
    tree: Any | None,
    on_mutate: Callable[[], None] | None,
    interrupt: Any = None,
) -> ExecutedToolBatch:
    """Execute a batch of tool calls synchronously.

    Every tool call produces exactly one tool result, whatever happens.
    """
    results: list[dict] = []

    for call_dict in tool_calls:
        # Check interrupt before each call
        if interrupt is not None and interrupt.is_set():
            # Fill remaining with interrupted results
            remaining = tool_calls[len(results):]
            for remaining_call in remaining:
                results.append({
                    "tool_call_id": remaining_call.get("id", ""),
                    "content": "not executed -- the user interrupted the run",
                    "is_error": False,
                })
            return ExecutedToolBatch(results=results, terminate=True, interrupted=True)

        call = ToolCall(
            id=str(call_dict.get("id") or uuid4().hex),
            name=str(call_dict.get("name") or "unknown"),
            arguments=call_dict.get("arguments", {}),
            malformed=bool(call_dict.get("malformed")),
        )

        state.record_tool_call(call.name)
        state.emit_event(EventType.TOOL_START, {"tool": call.name, "args": call.arguments})

        # Check for malformed arguments
        if call.malformed:
            results.append({
                "tool_call_id": call.id,
                "content": f"the arguments for {call.name} were not valid JSON",
                "is_error": True,
            })
            continue

        # Check hook for blocking/rewriting
        decision = hooks.before_tool_call(BeforeToolCall(call=call, state=state))
        if decision is not None and decision.block:
            results.append({
                "tool_call_id": call.id,
                "content": decision.reason or f"the {call.name} call was blocked by policy",
                "is_error": True,
            })
            state.emit_event(EventType.TOOL_END, {"tool": call.name, "blocked": True})
            continue

        arguments = decision.arguments if (decision and decision.arguments) else call.arguments

        # Execute the tool
        try:
            extra: dict[str, Any] = {"working_directory": working_directory}
            if session_id is not None:
                extra["session_id"] = session_id
            if tree is not None:
                extra["tree"] = tree
            if on_mutate is not None:
                extra["on_mutate"] = on_mutate
            ctx = ToolInvokeContext(extra=extra)
            tool_result = tool_provider.invoke(ctx, call.name, arguments)
            result_dict = {
                "tool_call_id": call.id,
                "content": getattr(tool_result, "text", str(tool_result)),
                "is_error": getattr(tool_result, "is_error", False),
            }
        except Exception as exc:
            _log.exception("tool %s failed", call.name)
            result_dict = {
                "tool_call_id": call.id,
                "content": f"Tool error: {exc}",
                "is_error": True,
            }

        results.append(result_dict)
        hooks.after_tool_call(call, result_dict)
        state.emit_event(EventType.TOOL_END, {
            "tool": call.name,
            "is_error": result_dict.get("is_error", False),
        })

    # Check if batch should terminate
    terminate = all(
        getattr(r, "terminate", False) for r in results
    ) if results else False

    return ExecutedToolBatch(results=results, terminate=terminate)


class AgentLoop:
    """Stateful turn runner owned by ``GithiRuntime``.

    Enhanced with features from githi/src/githi/agent/loop.py:
    - Hooks for tool call interception
    - Compaction with summarization
    - Turn budget warnings
    - Steering and follow-up messages
    """

    def __init__(
        self,
        *,
        seams: RuntimeSeams | None,
        model: str | None,
        session_dir: str | None,
        should_abort: Callable[[], bool],
        session_id: str | None = None,
        tree: Any | None = None,
        working_directory: str | None = None,
        on_mutate: Callable[[], None] | None = None,
        hooks: Hooks | None = None,
        config: LoopConfig | None = None,
    ) -> None:
        self._seams = seams
        self._model = model
        self._session_dir = session_dir
        self._should_abort = should_abort
        self._session_id = session_id
        self._tree = tree
        self._working_directory = working_directory or session_dir or "."
        self._on_mutate = on_mutate
        self._hooks = hooks or EMPTY_HOOKS
        self._config = config or LoopConfig()

    async def run_turn(
        self,
        user_text: str,
        history: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        user_msg = {
            "role": "user",
            "content": [{"type": "text", "text": user_text}],
        }
        messages = list(history) + [user_msg]

        yield RunStart()
        if self._should_abort():
            yield TurnEnd(stop_reason="aborted")
            return

        yield TurnStart()
        if self._should_abort():
            yield TurnEnd(stop_reason="aborted")
            return

        gateway = self._seams.model_gateway if self._seams else None
        if gateway is None:
            async for event in self._offline_turn(user_text, messages):
                yield event
            return

        try:
            async for event in self._gateway_turn(gateway, messages):
                yield event
        except AgentLoopError as exc:
            _log.exception("githi agent loop failed")
            yield ErrorEvent(
                error_class=type(exc).__name__,
                retryable=False,
                detail=str(exc),
            )
            yield TurnEnd(stop_reason="error")
            yield RunEnd(messages=messages)

    async def _offline_turn(
        self,
        user_text: str,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        probe_id = uuid4().hex
        root = self._session_dir or "."
        yield ToolStart(
            tool_call_id=probe_id,
            tool_name="workspace_probe",
            args={"path": root},
        )
        if self._should_abort():
            yield TurnEnd(stop_reason="aborted")
            return

        entries: list[str] = []
        error: str | None = None
        try:
            entries = sorted(p.name for p in Path(root).iterdir())[:50]
        except OSError as exc:
            error = str(exc)

        yield ToolEnd(
            tool_call_id=probe_id,
            tool_name="workspace_probe",
            result={"entries": entries, "error": error, "path": root},
            is_error=error is not None,
        )
        if self._should_abort():
            yield TurnEnd(stop_reason="aborted")
            return

        text = (
            f"[githi offline] received {len(user_text)} chars; "
            f"workspace_probe entries={len(entries)}. "
            "Configure RuntimeSeams.model_gateway for LLM turns (WP-A3)."
        )
        assistant_msg = {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        }
        yield MessageEnd(
            role="assistant",
            content=assistant_msg["content"],
            usage=Usage(0, 0, 0, 0, 0.0),
        )
        yield TurnEnd(stop_reason="stop")
        yield RunEnd(messages=messages + [assistant_msg])

    async def _gateway_turn(
        self,
        gateway: Any,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        chat = self._to_chat_messages(messages)
        tools_schema = self._tool_schemas()
        assistant_text = ""
        usage = Usage(0, 0, 0, 0, 0.0)
        final_messages = list(messages)

        for _ in range(_MAX_TOOL_ITERATIONS):
            if self._should_abort():
                yield TurnEnd(stop_reason="aborted")
                return

            completion = await asyncio.to_thread(
                gateway.complete,
                chat,
                tools_schema,
                model=self._model,
            )
            usage = self._usage_from_completion(completion)
            assistant_text = (
                completion.message.content if completion.message else ""
            ) or ""

            tool_calls = self._extract_tool_calls(completion)
            if not tool_calls:
                break

            final_messages.append(
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": assistant_text}],
                    "tool_calls": tool_calls,
                }
            )
            # The model must see its own tool calls before their results.
            chat.append(
                ChatMessage(role="assistant", content=assistant_text, tool_calls=tool_calls)
            )
            for call in tool_calls:
                if self._should_abort():
                    yield TurnEnd(stop_reason="aborted")
                    return
                async for event in self._run_tool(call):
                    yield event
                chat.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(call.get("_result", {})),
                        tool_call_id=str(call.get("id")),
                    )
                )
        else:
            raise AgentLoopError(
                f"tool loop exceeded {_MAX_TOOL_ITERATIONS} iterations"
            )

        assistant_msg = {
            "role": "assistant",
            "content": [{"type": "text", "text": assistant_text}],
        }
        final_messages.append(assistant_msg)
        yield MessageEnd(
            role="assistant",
            content=assistant_msg["content"],
            usage=usage,
        )
        yield TurnEnd(stop_reason="stop")
        yield RunEnd(messages=final_messages)

    async def _run_tool(self, call: dict[str, Any]) -> AsyncIterator[AgentEvent]:
        tool_call_id = str(call.get("id") or uuid4().hex)
        name = str(call.get("name") or "unknown")
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        yield ToolStart(tool_call_id=tool_call_id, tool_name=name, args=args)

        provider = self._seams.tool_provider if self._seams else None
        if provider is None:
            result: dict[str, Any] = {"error": "no tool_provider seam configured"}
            is_error = True
        else:
            try:
                extra: dict[str, Any] = {
                    "working_directory": self._working_directory,
                }
                if self._session_id is not None:
                    extra["session_id"] = self._session_id
                if self._tree is not None:
                    extra["tree"] = self._tree
                if self._on_mutate is not None:
                    extra["on_mutate"] = self._on_mutate
                ctx = ToolInvokeContext(extra=extra)
                tool_result = await asyncio.to_thread(
                    provider.invoke, ctx, name, args
                )
                result = {
                    "ok": bool(getattr(tool_result, "ok", False)),
                    "payload": getattr(tool_result, "payload", None),
                    "error": getattr(tool_result, "error", None),
                    "code": getattr(tool_result, "code", None),
                }
                is_error = not bool(result["ok"])
            except Exception as exc:  # noqa: BLE001 — surface as ToolEnd
                _log.exception("tool %s failed", name)
                result = {"error": str(exc)}
                is_error = True

        call["_result"] = result
        yield ToolEnd(
            tool_call_id=tool_call_id,
            tool_name=name,
            result=result,
            is_error=is_error,
        )

    def _tool_schemas(self) -> list[dict[str, Any]]:
        provider = self._seams.tool_provider if self._seams else None
        if provider is None:
            return []
        try:
            descriptors = provider.list_tools()
        except Exception:  # noqa: BLE001
            _log.exception("list_tools failed")
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": d.name,
                    "description": getattr(d, "description", "") or "",
                    "parameters": getattr(d, "parameters", None)
                    or {"type": "object", "properties": {}},
                },
            }
            for d in descriptors
        ]

    @staticmethod
    def _to_chat_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        for m in messages:
            role = str(m.get("role") or "user")
            content = m.get("content")
            if isinstance(content, list):
                text = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            else:
                text = str(content or "")
            out.append(
                ChatMessage(
                    role=role,
                    content=text,
                    tool_calls=m.get("tool_calls") or None,
                    tool_call_id=m.get("tool_call_id"),
                )
            )
        return out

    @staticmethod
    def _extract_tool_calls(completion: Any) -> list[dict[str, Any]]:
        msg = getattr(completion, "message", None)
        raw = getattr(msg, "tool_calls", None) if msg is not None else None
        if not raw:
            raw = getattr(completion, "tool_calls", None)
        if not raw:
            return []
        calls: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = item.get("name") or fn.get("name")
            args = item.get("arguments") or fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            calls.append(
                {
                    "id": item.get("id") or uuid4().hex,
                    "name": name,
                    "arguments": args if isinstance(args, dict) else {},
                }
            )
        return calls

    @staticmethod
    def _usage_from_completion(completion: Any) -> Usage:
        raw = getattr(completion, "usage", None) or {}
        if not isinstance(raw, dict):
            return Usage(0, 0, 0, 0, 0.0)
        return Usage(
            int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0),
            int(raw.get("completion_tokens") or raw.get("output_tokens") or 0),
            int(raw.get("cache_read") or raw.get("cache_read_tokens") or 0),
            int(raw.get("cache_write") or raw.get("cache_write_tokens") or 0),
            float(raw.get("cost_total") or raw.get("cost") or 0.0),
        )

    async def run_loop(
        self,
        user_text: str,
        history: list[dict[str, Any]],
        state: MutableAgentState | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Full agent loop with steering, follow-up, and compaction.

        Enhanced version that includes:
        - Steering message handling (mid-turn corrections)
        - Follow-up message handling (restart after completion)
        - Context compaction with summarization
        - Turn budget warnings
        - Force answer on final turn
        """
        config = self._config
        hooks = self._hooks
        state = state or MutableAgentState()

        user_msg = {
            "role": "user",
            "content": [{"type": "text", "text": user_text}],
        }
        messages = list(history) + [user_msg]
        state.messages = messages

        if config.on_message:
            config.on_message(user_msg)

        yield RunStart()

        # Drain any pending steering messages before starting
        pending = list(hooks.get_steering_messages())

        while True:  # Outer loop: follow-up messages
            has_more_tool_calls = True

            while has_more_tool_calls or pending:  # Inner loop: tools + steering
                # Check turn limit
                if state.turns >= config.max_turns:
                    state.stop_reason = StopReason.MAX_TURNS
                    yield RunEnd(messages=messages)
                    return

                # Check custom stop condition
                if hooks.should_stop(state):
                    state.stop_reason = StopReason.STOPPED
                    yield RunEnd(messages=messages)
                    return

                # Check interrupt
                if config.interrupt is not None and config.interrupt.is_set():
                    state.stop_reason = StopReason.INTERRUPTED
                    yield RunEnd(messages=messages)
                    return

                # Append pending messages
                for msg in pending:
                    if isinstance(msg, dict):
                        messages.append(msg)
                        if config.on_message:
                            config.on_message(msg)
                pending = []

                # Compact if needed
                compacted = compact(
                    messages,
                    config.context_limit_tokens,
                    summarizer=config.summarizer,
                )
                if compacted != messages and config.on_compaction:
                    config.on_compaction(compacted)
                messages[:] = compacted
                state.messages = messages

                # Add turn budget warning if needed
                remaining = config.max_turns - state.turns
                is_final = remaining <= 1 and config.force_answer_on_final_turn
                view = list(messages)
                if is_final:
                    view.append({"role": "user", "content": FINAL_TURN_NOTICE})
                elif 0 < config.warn_turns_remaining and remaining <= config.warn_turns_remaining:
                    view.append({
                        "role": "user",
                        "content": f"[{remaining} turns remaining. Stop gathering and write your "
                                   "answer now if you have enough to conclude.]",
                    })

                yield TurnStart()
                state.record_turn()
                state.emit_event(EventType.TURN_START, {"turn": state.turns})

                if self._should_abort():
                    yield TurnEnd(stop_reason="aborted")
                    state.stop_reason = StopReason.INTERRUPTED
                    yield RunEnd(messages=messages)
                    return

                gateway = self._seams.model_gateway if self._seams else None
                if gateway is None:
                    # Offline mode
                    async for event in self._offline_turn(user_text, messages):
                        yield event
                    state.stop_reason = StopReason.DONE
                    return

                # Get tool schemas (empty on final turn)
                tools_schema = [] if is_final else self._tool_schemas()

                # Call gateway
                try:
                    chat = self._to_chat_messages(view)
                    completion = await asyncio.to_thread(
                        gateway.complete,
                        chat,
                        tools_schema,
                        model=config.model or self._model,
                    )
                except Exception as exc:
                    _log.exception("gateway completion failed")
                    yield ErrorEvent(
                        error_class=type(exc).__name__,
                        retryable=True,
                        detail=str(exc),
                    )
                    yield TurnEnd(stop_reason="error")
                    state.stop_reason = StopReason.ERROR
                    yield RunEnd(messages=messages)
                    return

                usage = self._usage_from_completion(completion)
                assistant_text = (
                    completion.message.content if completion.message else ""
                ) or ""

                tool_calls = self._extract_tool_calls(completion)
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": [{"type": "text", "text": assistant_text}],
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls

                messages.append(assistant_msg)
                if config.on_message:
                    config.on_message(assistant_msg)

                # Execute tool calls if any
                tool_results: list[dict] = []
                if tool_calls:
                    for call in tool_calls:
                        if self._should_abort():
                            yield TurnEnd(stop_reason="aborted")
                            state.stop_reason = StopReason.INTERRUPTED
                            yield RunEnd(messages=messages)
                            return
                        async for event in self._run_tool(call):
                            yield event
                        tool_result = {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(call.get("_result", {})),
                        }
                        tool_results.append(tool_result)
                        messages.append(tool_result)
                        if config.on_message:
                            config.on_message(tool_result)

                yield MessageEnd(
                    role="assistant",
                    content=assistant_msg["content"],
                    usage=usage,
                )

                has_more_tool_calls = bool(tool_calls)
                state.emit_event(EventType.TURN_END, {
                    "turn": state.turns,
                    "tool_calls": len(tool_calls),
                })
                yield TurnEnd(stop_reason="stop" if not tool_calls else "tool_use")

                if not tool_calls and not pending:
                    break

                pending = list(hooks.get_steering_messages())

            # Check for follow-up messages
            follow_up = hooks.get_follow_up_messages()
            if follow_up:
                pending = list(follow_up)
                continue
            break

        if state.stop_reason is None:
            state.stop_reason = StopReason.DONE
        yield RunEnd(messages=messages)
