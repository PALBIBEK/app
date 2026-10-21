"""RED → GREEN: journal → span projector (Phase1 §3.2 / §5.2b)."""

from __future__ import annotations

from supervisor.agent.events import (
    Envelope,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.obs.projector import JournalProjector
from supervisor.telemetry_contract.spans import SpanStatus
from tests.support.telemetry_fakes import FakeSpanSink


def _env(
    seq: int,
    payload: object,
    *,
    session_id: str = "s1",
    trace_id: str = "a" * 32,
    turn_id: str | None = None,
) -> Envelope:
    return Envelope(
        session_id=session_id,
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=1_000 + seq,
        turn_id=turn_id,
        trace_id=trace_id,
        payload=payload,  # type: ignore[arg-type]
    )


def test_full_run_projects_nested_tree() -> None:
    sink = FakeSpanSink()
    proj = JournalProjector(
        sink=sink,
        tenant_id="t1",
        harness_id="coding",
        harness_version="coding-v1",
    )
    turn = "turn-1"
    events = [
        _env(0, RunStart()),
        _env(1, TurnStart(), turn_id=turn),
        _env(
            2,
            MessageEnd(
                role="assistant",
                content=[{"type": "text", "text": "SECRET_BODY"}],
                usage=Usage(10, 96, 0, 1801, 0.008),
            ),
            turn_id=turn,
        ),
        _env(
            3,
            ToolStart(tool_call_id="c1", tool_name="read", args={"path": "/secret"}),
            turn_id=turn,
        ),
        _env(
            4,
            ToolEnd(
                tool_call_id="c1",
                tool_name="read",
                result={"ok": True},
                is_error=False,
            ),
            turn_id=turn,
        ),
        _env(5, TurnEnd(stop_reason="end"), turn_id=turn),
        _env(6, RunEnd(messages=[{"role": "user", "content": "hi"}])),
    ]
    for e in events:
        proj.project(e)

    names = [s.name for s in sink.spans]
    assert "invoke_agent" in names[0] or sink.spans[0].attributes.get("gen_ai.operation.name") == "invoke_agent"
    ops = [s.attributes.get("gen_ai.operation.name") or s.attributes.get("yal.operation.name") for s in sink.spans]
    assert "invoke_agent" in ops
    assert "turn" in ops
    assert "chat" in ops
    assert "execute_tool" in ops

    # No content leakage
    blob = repr(sink.spans)
    assert "SECRET_BODY" not in blob
    assert "/secret" not in blob

    chat = next(s for s in sink.spans if s.attributes.get("gen_ai.operation.name") == "chat")
    assert chat.attributes["gen_ai.usage.input_tokens"] == 10 + 1801
    assert chat.parent_span_id is not None  # under turn


def test_unpaired_tool_closes_unknown_not_error() -> None:
    sink = FakeSpanSink()
    proj = JournalProjector(
        sink=sink,
        tenant_id="t1",
        harness_id="coding",
        harness_version="coding-v1",
    )
    turn = "t1"
    for e in [
        _env(0, RunStart()),
        _env(1, TurnStart(), turn_id=turn),
        _env(
            2,
            ToolStart(tool_call_id="orphan", tool_name="bash", args={"cmd": "rm -rf /"}),
            turn_id=turn,
        ),
        _env(3, RunEnd(messages=[])),
    ]:
        proj.project(e)

    tool = next(s for s in sink.spans if s.attributes.get("gen_ai.operation.name") == "execute_tool")
    assert tool.status == SpanStatus.UNSET
    assert tool.attributes.get("yal.tool.outcome") == "unknown"
    assert tool.attributes.get("yal.tool.unpaired") is True
    assert tool.end_ms is not None


def test_exporter_failure_does_not_raise() -> None:
    class Boom:
        def export(self, spans: list) -> None:
            raise RuntimeError("otlp down")

    proj = JournalProjector(
        sink=Boom(),  # type: ignore[arg-type]
        tenant_id="t1",
        harness_id="coding",
        harness_version="coding-v1",
    )
    proj.project(_env(0, RunStart()))
    proj.project(_env(1, RunEnd(messages=[])))  # must not raise
