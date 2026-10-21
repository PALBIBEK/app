"""Closure policy extras — cascade + backfill (Phase1 §3.2a)."""

from __future__ import annotations

from supervisor.agent.events import Envelope, RunEnd, RunStart, ToolStart, TurnStart
from supervisor.obs.projector import JournalProjector, backfill
from supervisor.telemetry_contract.spans import SpanStatus
from tests.support.telemetry_fakes import FakeSpanSink


def _env(seq: int, payload: object, *, turn_id: str | None = None) -> Envelope:
    return Envelope(
        session_id="s1",
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=1000 + seq,
        turn_id=turn_id,
        trace_id="a" * 32,
        payload=payload,  # type: ignore[arg-type]
    )


def test_cascade_marks_closed_by_on_unpaired_descendants() -> None:
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
            ToolStart(tool_call_id="x", tool_name="bash", args={}),
            turn_id=turn,
        ),
        _env(3, RunEnd(messages=[])),
    ]:
        proj.project(e)

    tool = next(s for s in sink.spans if s.attributes.get("gen_ai.operation.name") == "execute_tool")
    assert tool.attributes.get("yal.closed_by") == "run_end"
    assert tool.attributes.get("yal.tool.unpaired") is True
    assert tool.status == SpanStatus.UNSET


def test_backfill_replays_journal_range() -> None:
    sink = FakeSpanSink()
    proj = JournalProjector(
        sink=sink,
        tenant_id="t1",
        harness_id="coding",
        harness_version="coding-v1",
    )
    events = [_env(0, RunStart()), _env(1, RunEnd(messages=[]))]
    backfill(proj, events)
    assert any(
        s.attributes.get("gen_ai.operation.name") == "invoke_agent" for s in sink.spans
    )
