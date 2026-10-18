"""Harness wire → canonical events. The membrane.

This file is the only place camelCase harness RPC keys are read. Convert them here;
anywhere else they are a bug.
"""

from __future__ import annotations

import logging
from typing import Any

from supervisor.agent.events import (
    AgentEvent,
    ApprovalRequired,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)

_log = logging.getLogger(__name__)
_unknown_logged: set[str] = set()


def _usage(raw: dict[str, Any] | None) -> Usage | None:
    if not raw:
        return None
    cost = raw.get("cost") or {}
    return Usage(
        input_tokens=int(raw.get("input", 0)),
        output_tokens=int(raw.get("output", 0)),
        cache_read=int(raw.get("cacheRead", 0)),
        cache_write=int(raw.get("cacheWrite", 0)),
        cost_total=float(cost.get("total", 0)),
    )


def translate(raw: dict[str, Any]) -> AgentEvent | None:
    kind = raw.get("type")
    if kind in ("response", "message_start", "message_update"):
        return None
    if kind == "agent_start":
        return RunStart()
    if kind == "turn_start":
        return TurnStart()
    if kind == "turn_end":
        msg = raw.get("message") or {}
        return TurnEnd(stop_reason=str(msg.get("stopReason") or "stop"))
    if kind == "message_end":
        msg = raw.get("message") or {}
        role = msg.get("role")
        if role not in ("assistant", "user", "toolResult"):
            return None
        return MessageEnd(
            role=str(role),
            content=list(msg.get("content") or []),
            usage=_usage(msg.get("usage")) if role == "assistant" else None,
        )
    if kind == "tool_execution_start":
        return ToolStart(
            tool_call_id=str(raw.get("toolCallId") or ""),
            tool_name=str(raw.get("toolName") or ""),
            args=dict(raw.get("args") or {}),
        )
    if kind == "tool_execution_end":
        result = raw.get("result")
        if not isinstance(result, dict):
            result = {}
        return ToolEnd(
            tool_call_id=str(raw.get("toolCallId") or ""),
            tool_name=str(raw.get("toolName") or ""),
            result=result,
            is_error=bool(raw.get("isError")),
        )
    if kind == "agent_end":
        return RunEnd(messages=list(raw.get("messages") or []))
    if kind == "extension_ui_request":
        return ApprovalRequired(
            approval_id=str(raw.get("id") or raw.get("requestId") or ""),
            prompt=str(raw.get("message") or raw.get("prompt") or ""),
            options=[str(o) for o in (raw.get("options") or ["yes", "no"])],
            timeout_s=float(raw.get("timeoutS") or raw.get("timeout_s") or 300),
            harness_request_id=str(raw.get("id") or raw.get("requestId") or "") or None,
        )
    if kind not in _unknown_logged:
        _unknown_logged.add(str(kind))
        _log.debug("unmapped harness event type=%r", kind)
    return None
