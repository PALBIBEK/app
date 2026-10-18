"""RED → GREEN: EventStream assigns/propagates W3C trace_id (Phase1 §5.2a)."""

from __future__ import annotations

import re

from supervisor.agent.events import MessageEnd, RunEnd, RunStart, TurnEnd, TurnStart, Usage
from supervisor.agent.stream import EventStream

_TRACE_HEX = re.compile(r"^[0-9a-f]{32}$")


def test_run_start_assigns_32_hex_trace_id() -> None:
    stream = EventStream(session_id="s1")
    env = stream.emit(RunStart())
    assert env is not None
    assert env.trace_id is not None
    assert _TRACE_HEX.match(env.trace_id)


def test_subsequent_events_share_run_trace_id() -> None:
    stream = EventStream(session_id="s1")
    start = stream.emit(RunStart())
    assert start is not None
    tid = start.trace_id
    turn = stream.emit(TurnStart())
    msg = stream.emit(
        MessageEnd(
            role="assistant",
            content=[],
            usage=Usage(1, 1, 0, 0, 0.0),
        )
    )
    stream.emit(TurnEnd(stop_reason="end"))
    end = stream.emit(RunEnd(messages=[]))
    assert turn is not None and turn.trace_id == tid
    assert msg is not None and msg.trace_id == tid
    assert end is not None and end.trace_id == tid


def test_run_end_clears_trace_for_next_run() -> None:
    stream = EventStream(session_id="s1")
    a = stream.emit(RunStart())
    stream.emit(RunEnd(messages=[]))
    b = stream.emit(RunStart())
    assert a is not None and b is not None
    assert a.trace_id != b.trace_id


def test_seed_inbound_traceparent_honored_on_run_start() -> None:
    inbound = "c" * 32
    stream = EventStream(session_id="s1")
    stream.seed_trace_id(inbound)
    env = stream.emit(RunStart())
    assert env is not None
    assert env.trace_id == inbound
