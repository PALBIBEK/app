"""Githi's turn machinery, built only from the session's pinned seams.

A githi turn with a model is driven step by step by the session workflow
(``temporal.steps``): one activity per model call and per tool call. This
module is the one place those steps get their pieces from, so every model
call is assembled the same way:

* the system prompt from the ``prompt_assembler`` seam,
* the tools from the ``tool_provider`` seam, run in the session's pinned
  workspace (its sandbox when it has one),
* the model call itself through the ``model_gateway`` seam, with the session's
  pinned model.

Without a gateway there is no model to step, and ``run_turn`` plays an offline
turn instead: a workspace probe that exercises the session and SSE plumbing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from supervisor.agent.events import (
    AgentEvent,
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
from supervisor.seams import ChatMessage, PromptBindings, ToolInvokeContext
from supervisor.tools import Workspace

_log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8
"""Model steps a single run may take before the workflow ends it."""


class AgentLoopError(Exception):
    """Unrecoverable failure inside the agent loop."""


class AgentLoop:
    """One session's turn pieces: prompt, tool schemas, tool calls, model output parsing."""

    def __init__(
        self,
        *,
        seams: RuntimeSeams | None,
        session_id: str | None,
        workspace: Workspace | None = None,
        on_mutate: Callable[[], None] | None = None,
        should_abort: Callable[[], bool] = lambda: False,
    ) -> None:
        self._seams = seams
        self._session_id = session_id
        self._workspace = workspace
        self._on_mutate = on_mutate
        self._should_abort = should_abort

    # --- whole turn (offline only) ---------------------------------------------

    async def run_turn(
        self,
        user_text: str,
        history: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        """An offline turn. Turns with a model run under the session workflow, never here."""
        if self._seams is not None and self._seams.model_gateway is not None:
            raise AgentLoopError(
                "githi turns with a model are driven step by step by the session workflow"
            )
        messages = [*history, {"role": "user", "content": [{"type": "text", "text": user_text}]}]
        yield RunStart()
        yield TurnStart()
        if self._should_abort():
            yield TurnEnd(stop_reason="aborted")
            return
        async for event in self._offline_turn(user_text, messages):
            yield event

    async def _offline_turn(
        self,
        user_text: str,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        probe = await self._probe()
        yield ToolStart(tool_call_id=probe.id, tool_name="workspace_probe", args={"path": "."})
        yield ToolEnd(
            tool_call_id=probe.id,
            tool_name="workspace_probe",
            result={"entries": probe.entries, "error": probe.error, "path": "."},
            is_error=probe.error is not None,
        )
        if self._should_abort():
            yield TurnEnd(stop_reason="aborted")
            return
        text = (
            f"[githi offline] received {len(user_text)} chars; "
            f"workspace_probe entries={len(probe.entries)}. "
            "Pin a model gateway for LLM turns."
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        reply: dict[str, Any] = {"role": "assistant", "content": content}
        yield MessageEnd(role="assistant", content=content, usage=Usage(0, 0, 0, 0, 0.0))
        yield TurnEnd(stop_reason="stop")
        yield RunEnd(messages=[*messages, reply])

    async def _probe(self) -> _Probe:
        probe = _Probe(uuid4().hex)
        if self._workspace is None:
            probe.error = "no workspace bound to this session"
            return probe
        try:
            probe.entries = [str(e) for e in await self._workspace.list_dir("")][:50]
        except Exception as exc:  # noqa: BLE001 — reported in the probe result
            probe.error = str(exc)
        return probe

    # --- pieces of a stepped turn --------------------------------------------------

    def messages_for_model(self, messages: list[dict[str, Any]]) -> list[ChatMessage]:
        """The conversation as the model sees it, led by the pinned system prompt."""
        chat = self.to_chat_messages(messages)
        assembler = self._seams.prompt_assembler if self._seams else None
        if assembler is None:
            return chat
        prompt = assembler.assemble(PromptBindings(cwd="/workspace"))
        return [ChatMessage(role="system", content=prompt.text), *chat]

    def tool_schemas(self) -> list[dict[str, Any]]:
        """The pinned tools in the model's function-calling shape."""
        provider = self._seams.tool_provider if self._seams else None
        if provider is None:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": d.name,
                    "description": d.description or "",
                    "parameters": d.parameters or {"type": "object", "properties": {}},
                },
            }
            for d in provider.list_tools()
        ]

    def offers(self, name: str) -> bool:
        return any(s["function"]["name"] == name for s in self.tool_schemas())

    async def run_tool(self, call: dict[str, Any], *, tenant_id: str | None = None) -> ToolEnd:
        """Run one tool call through the tool seam, in the session's workspace."""
        call_id = str(call.get("id") or uuid4().hex)
        name = str(call.get("name") or "unknown")
        raw_args = call.get("arguments")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        provider = self._seams.tool_provider if self._seams else None
        if provider is None:
            result: dict[str, Any] = {"error": "no tool_provider seam configured"}
            return ToolEnd(tool_call_id=call_id, tool_name=name, result=result, is_error=True)

        extra: dict[str, Any] = {"session_id": self._session_id, "tenant_id": tenant_id}
        if self._workspace is not None:
            extra["workspace"] = self._workspace
        if self._on_mutate is not None:
            extra["on_mutate"] = self._on_mutate
        try:
            outcome = await asyncio.to_thread(
                provider.invoke, ToolInvokeContext(trace_id=call_id, extra=extra), name, args
            )
        except Exception as exc:  # noqa: BLE001 — a provider bug is the model's to read, not a crash
            _log.exception("tool %s failed", name)
            result = {"ok": False, "error": str(exc), "code": "TOOL_INTERNAL"}
            return ToolEnd(tool_call_id=call_id, tool_name=name, result=result, is_error=True)
        result = {
            "ok": outcome.ok,
            "payload": outcome.payload,
            "error": outcome.error,
            "code": outcome.code,
        }
        return ToolEnd(tool_call_id=call_id, tool_name=name, result=result, is_error=not outcome.ok)

    # --- model output parsing --------------------------------------------------------

    @staticmethod
    def to_chat_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        for m in messages:
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
                    role=str(m.get("role") or "user"),
                    content=text,
                    tool_calls=m.get("tool_calls") or None,
                    tool_call_id=m.get("tool_call_id"),
                )
            )
        return out

    @staticmethod
    def extract_tool_calls(completion: Any) -> list[dict[str, Any]]:
        msg = getattr(completion, "message", None)
        raw = getattr(msg, "tool_calls", None) if msg is not None else None
        if not raw:
            raw = getattr(completion, "tool_calls", None)
        calls: list[dict[str, Any]] = []
        for item in raw or ():
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            fn: dict[str, Any] = function if isinstance(function, dict) else {}
            args = item.get("arguments") or fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            calls.append(
                {
                    "id": item.get("id") or uuid4().hex,
                    "name": item.get("name") or fn.get("name"),
                    "arguments": args if isinstance(args, dict) else {},
                }
            )
        return calls

    @staticmethod
    def usage_from_completion(completion: Any) -> Usage:
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


@dataclass(slots=True)
class _Probe:
    id: str
    entries: list[str] = field(default_factory=list)
    error: str | None = None
