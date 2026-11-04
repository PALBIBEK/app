"""Model calls exported to OpenTelemetry: timed, named after their model, and only for the
assistant (a user's message is not a model call), the same spans as the product's trace."""

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
from supervisor.traces.build import build_run_trace
from tests.support.telemetry_fakes import FakeSpanSink

TID = "9" * 32


def _events() -> list[Envelope]:
    def env(seq: int, ts: int, turn: str | None, payload: object) -> Envelope:
        return Envelope("s-m", seq, f"e{seq}", ts, turn, TID, payload)  # type: ignore[arg-type]

    return [
        env(0, 1_000, None, RunStart()),
        env(1, 1_000, "t1", MessageEnd(role="user", content=[{"type": "text", "text": "fix it"}])),
        env(2, 1_010, "t1", TurnStart()),
        env(3, 3_510, "t1", MessageEnd(role="assistant", content=[{"type": "text", "text": "looking"}],
                                     usage=Usage(10, 5, 0, 0, 0.001), model="gpt-x", finish_reason="tool_calls")),
        env(4, 3_520, "t1", ToolStart(tool_call_id="c1", tool_name="read", args={})),
        env(5, 3_600, "t1", ToolEnd(tool_call_id="c1", tool_name="read", result={"content": "x"}, is_error=False)),
        env(6, 4_800, "t1", MessageEnd(role="assistant", content=[{"type": "text", "text": "done"}],
                                     model="gpt-x", finish_reason="stop")),
        env(7, 4_810, "t1", TurnEnd(stop_reason="end")),
        env(8, 4_820, None, RunEnd(messages=[])),
    ]


def test_a_model_call_is_timed_and_names_its_model() -> None:
    sink = FakeSpanSink(validate=False)
    projector = JournalProjector(sink=sink, tenant_id="t", harness_id="h", harness_version="v1")
    for e in _events():
        projector.project(e)
    chats = [s for s in sink.spans if s.attributes.get("gen_ai.operation.name") == "chat"]
    assert [s.name for s in chats] == ["chat gpt-x", "chat gpt-x"], "the user's message is not a model call"
    first, second = chats
    assert (first.start_ms, first.end_ms) == (1_010, 3_510), "from its step's start to its end"
    assert (second.start_ms, second.end_ms) == (3_600, 4_800), "from the tool result before it"
    assert first.attributes["gen_ai.request.model"] == first.attributes["gen_ai.response.model"] == "gpt-x"
    assert first.attributes["gen_ai.response.finish_reasons"] == ["tool_calls"]
    assert second.attributes["gen_ai.response.finish_reasons"] == ["stop"]


def test_the_exported_model_calls_are_the_product_traces() -> None:
    sink = FakeSpanSink(validate=False)
    projector = JournalProjector(sink=sink, tenant_id="t", harness_id="h", harness_version="v1")
    for e in _events():
        projector.project(e)
    exported = {s.span_id: (s.start_ms, s.end_ms) for s in sink.spans if s.attributes.get("yal.span.kind") == "llm"}
    trace = build_run_trace("s-m", "t1", _events())
    assert trace is not None
    product = {s.id: (s.start_ms, s.end_ms) for s in trace.spans if s.kind == "llm"}
    assert exported == product
