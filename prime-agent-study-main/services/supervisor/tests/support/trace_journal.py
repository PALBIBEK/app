"""Journals of one run written by hand, for trace tests: seq and timestamps chosen."""

from __future__ import annotations

from typing import Any

from supervisor.agent.events import Envelope, MessageEnd, ToolEnd, ToolStart, Usage
from supervisor.traces import RunTrace, build_run_trace

__all__ = ["RUN", "SID", "Journal", "model", "ran", "tool", "user"]

SID, RUN = "s1", "run-1"


class Journal:
    """Events of one session, appended with explicit timestamps (ms from 0)."""

    def __init__(self) -> None:
        self.envs: list[Envelope] = []

    def at(self, ms: int, payload: Any, *, run: str | None = RUN) -> Journal:
        seq = len(self.envs)
        self.envs.append(Envelope(SID, seq, f"e{seq}", ms, run, "trace-x", payload))
        return self

    def trace(self, *, running: bool = True) -> RunTrace:
        trace = build_run_trace(SID, RUN, self.envs, agent="reviewer", session_running=running)
        assert trace is not None
        return trace


def user(text: str) -> MessageEnd:
    return MessageEnd(role="user", content=[{"type": "text", "text": text}])


def model(text: str = "", calls: list[tuple[str, str, dict[str, Any]]] = (), cost: float = 0.01) -> MessageEnd:  # type: ignore[assignment]
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_call", "id": cid, "name": name, "arguments": args} for cid, name, args in calls]
    return MessageEnd(role="assistant", content=content, usage=Usage(100, 10, 0, 0, cost), model="m1",
                      finish_reason="tool_calls" if calls else "stop")


def ran(cid: str, name: str, payload: Any = None, *, ok: bool = True, code: str | None = None,
        error: str | None = None, ms: int | None = None) -> ToolEnd:
    return ToolEnd(cid, name, {"ok": ok, "payload": payload, "error": error, "code": code}, is_error=not ok,
                   duration_ms=ms)


def tool(j: Journal, at: int, cid: str, name: str, args: dict[str, Any], result: ToolEnd) -> Journal:
    """A tool call that starts at ``at`` and ends 5 ms later with ``result``."""
    return j.at(at, ToolStart(cid, name, args)).at(at + 5, result)
