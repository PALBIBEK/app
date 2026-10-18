"""Githi AgentLoop — one user turn → platform AgentEvents.

Emits supervisor vocabulary (``RunStart`` … ``RunEnd``). The model is reached
only through ``RuntimeSeams.model_gateway`` and tools only through
``RuntimeSeams.tool_provider``.

Without a gateway, an offline turn still emits a paired ``ToolStart`` /
``ToolEnd`` (a workspace probe), so session and SSE plumbing can be exercised
end to end with no model.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
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

_log = logging.getLogger(__name__)
_MAX_TOOL_ITERATIONS = 8


class AgentLoopError(Exception):
    """Unrecoverable failure inside the agent loop."""


class AgentLoop:
    """Turn runner for ``GithiRuntime``; ``temporal.steps`` drives its pieces one step at a time."""

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
    ) -> None:
        self._seams = seams
        self._model = model
        self._session_dir = session_dir
        self._should_abort = should_abort
        self._session_id = session_id
        self._tree = tree
        self._working_directory = working_directory or session_dir or "."
        self._on_mutate = on_mutate

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
            assistant_text = (completion.message.content if completion.message else "") or ""

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
            raise AgentLoopError(f"tool loop exceeded {_MAX_TOOL_ITERATIONS} iterations")

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
                tool_result = await asyncio.to_thread(provider.invoke, ctx, name, args)
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
