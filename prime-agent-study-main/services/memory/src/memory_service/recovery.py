"""Load-time recovery — close interrupted turns; never truncate; fail loud on corrupt."""

from __future__ import annotations

import time
from typing import Any

from memory_service.types import (
    SUPPORTED_FORMAT_VERSIONS,
    FormatUnsupported,
    SessionCorrupt,
    SessionEvent,
)


def assert_format_supported(format_version: int) -> None:
    if format_version not in SUPPORTED_FORMAT_VERSIONS:
        raise FormatUnsupported(
            f"FORMAT_UNSUPPORTED: format_version={format_version}",
            code="FORMAT_UNSUPPORTED",
        )


def detect_seq_gap(events: list[SessionEvent]) -> int | None:
    for i, ev in enumerate(events):
        if ev.seq != i:
            return i
    return None


def find_unanswered_tool_calls(events: list[SessionEvent]) -> list[SessionEvent]:
    results: set[str] = set()
    for ev in events:
        if ev.type == "tool/result":
            data = ev.data if isinstance(ev.data, dict) else {}
            cid = data.get("call_id") or data.get("tool_call_id")
            if cid is not None:
                results.add(str(cid))
    unanswered: list[SessionEvent] = []
    for ev in events:
        if ev.type != "tool/call":
            continue
        data = ev.data if isinstance(ev.data, dict) else {}
        cid = data.get("call_id") or data.get("tool_call_id")
        if cid is None or str(cid) not in results:
            unanswered.append(ev)
    return unanswered


def build_recovery_closers(events: list[SessionEvent]) -> list[SessionEvent]:
    """Synthesize interrupted closers. Never truncates the input prefix."""
    if detect_seq_gap(events) is not None:
        raise SessionCorrupt("SESSION_CORRUPT: seq gap before repair", code="SESSION_CORRUPT")

    unanswered = find_unanswered_tool_calls(events)
    step_open = sum(1 for e in events if e.type == "step/start") > sum(
        1 for e in events if e.type == "step/end"
    )
    mid_turn = any(
        e.type in ("assistant/message", "tool/call", "tool/result", "step/start") for e in events
    ) and (not events or events[-1].type != "turn/end")

    if not unanswered and not step_open and not mid_turn:
        return []

    # If only mid_turn because turn/end missing but tools all answered — still close.
    closers: list[SessionEvent] = []
    next_seq = len(events)
    now = int(time.time() * 1000)
    working = list(events)

    for call in unanswered:
        data = call.data if isinstance(call.data, dict) else {}
        cid = data.get("call_id") or data.get("tool_call_id") or f"unknown-{call.seq}"
        ev = SessionEvent(
            type="tool/result",
            seq=next_seq,
            time=now,
            data={"call_id": str(cid), "status": "interrupted", "recovered": True},
        )
        closers.append(ev)
        working.append(ev)
        next_seq += 1

    if sum(1 for e in working if e.type == "step/start") > sum(
        1 for e in working if e.type == "step/end"
    ):
        ev = SessionEvent(
            type="step/end",
            seq=next_seq,
            time=now,
            data={"kind": "interrupted"},
        )
        closers.append(ev)
        working.append(ev)
        next_seq += 1

    if working and working[-1].type != "turn/end":
        if unanswered or step_open or mid_turn:
            closers.append(
                SessionEvent(
                    type="turn/end",
                    seq=next_seq,
                    time=now,
                    data={"kind": "interrupted"},
                )
            )

    return closers


def event_from_row(seq: int, type: str, time_ms: int, data: Any) -> SessionEvent:
    return SessionEvent(type=type, seq=seq, time=time_ms, data=data)
