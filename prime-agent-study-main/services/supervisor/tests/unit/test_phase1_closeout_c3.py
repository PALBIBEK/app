"""C3 — missing Phase1 §6 tests (resume run id, subagent nest, exporter fault)."""

from __future__ import annotations

import time

import pytest

from supervisor.agent.events import (
    Envelope,
    MessageEnd,
    RunEnd,
    RunStart,
    SubagentEnd,
    SubagentStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.obs.projector import JournalProjector
from supervisor.obs.tracing import make_inmemory_otlp_projector
from supervisor.telemetry_contract.fakes import FakeSpanSink
from supervisor.telemetry_contract.spans import SpanRecord


def _env(
    seq: int,
    payload: object,
    *,
    session_id: str = "s1",
    trace_id: str,
    turn_id: str | None = None,
) -> Envelope:
    return Envelope(
        session_id=session_id,
        seq=seq,
        event_id=f"e{seq}-{trace_id[:4]}",
        ts_ms=1_000 + seq,
        turn_id=turn_id,
        trace_id=trace_id,
        payload=payload,  # type: ignore[arg-type]
    )


def test_resume_creates_new_run_same_session() -> None:
    sink = FakeSpanSink(validate=False)
    proj = JournalProjector(
        sink=sink,
        tenant_id="t1",
        harness_id="coding",
        harness_version="coding-v1",
    )
    t1, t2 = "a" * 32, "b" * 32
    for e in [
        _env(0, RunStart(), trace_id=t1),
        _env(1, TurnStart(), trace_id=t1, turn_id="u1"),
        _env(2, TurnEnd(stop_reason="end"), trace_id=t1, turn_id="u1"),
        _env(3, RunEnd(messages=[]), trace_id=t1),
    ]:
        proj.project(e)
    roots_1 = [
        s
        for s in sink.spans
        if s.attributes.get("gen_ai.operation.name") == "invoke_agent"
        and s.parent_span_id is None
    ]
    assert len(roots_1) == 1
    r1 = roots_1[0]

    for e in [
        _env(4, RunStart(), trace_id=t2),
        _env(5, RunEnd(messages=[]), trace_id=t2),
    ]:
        proj.project(e)
    roots = [
        s
        for s in sink.spans
        if s.attributes.get("gen_ai.operation.name") == "invoke_agent"
        and s.parent_span_id is None
    ]
    assert len(roots) == 2
    r2 = roots[1]
    assert r1.yal_session_id == r2.yal_session_id == "s1"
    assert r1.yal_run_id == t1
    assert r2.yal_run_id == t2
    assert r1.yal_run_id != r2.yal_run_id


def test_subagent_nests_under_parent_run() -> None:
    sink = FakeSpanSink(validate=False)
    proj = JournalProjector(
        sink=sink,
        tenant_id="t1",
        harness_id="coding",
        harness_version="coding-v1",
    )
    parent_tid = "d" * 32
    child_run = "child-run-99"
    for e in [
        _env(0, RunStart(), trace_id=parent_tid),
        _env(
            1,
            SubagentStart(
                run_id=child_run,
                child_session_id="c1",
                parent_session_id="s1",
                mode="one_shot",
                provider="pi",
                label="helper",
            ),
            trace_id=parent_tid,
        ),
        _env(
            2,
            SubagentEnd(run_id=child_run, stop_reason="ok", output="done"),
            trace_id=parent_tid,
        ),
        _env(3, RunEnd(messages=[]), trace_id=parent_tid),
    ]:
        proj.project(e)

    parent = next(
        s
        for s in sink.spans
        if s.attributes.get("gen_ai.operation.name") == "invoke_agent"
        and s.parent_span_id is None
    )
    child = next(s for s in sink.spans if s.yal_run_id == child_run)
    assert child.parent_span_id == parent.span_id
    assert child.yal_session_id == parent.yal_session_id
    assert child.yal_run_id != parent.yal_run_id
    assert child.attributes.get("gen_ai.operation.name") == "invoke_agent"


@pytest.mark.parametrize("failure", ["raise", "timeout", "error_response"])
def test_exporter_failure_does_not_affect_run(failure: str) -> None:
    class FailingSink:
        def export(self, spans: list[SpanRecord]) -> None:
            if failure == "raise":
                raise RuntimeError("otlp down")
            if failure == "timeout":
                time.sleep(0.05)
                raise TimeoutError("export timeout")
            raise RuntimeError("500 from collector")

    proj = JournalProjector(
        sink=FailingSink(),  # type: ignore[arg-type]
        tenant_id="t1",
        harness_id="coding",
        harness_version="coding-v1",
    )
    tid = "e" * 32
    t0 = time.perf_counter()
    # Must complete without raising
    for e in [
        _env(0, RunStart(), trace_id=tid),
        _env(1, TurnStart(), trace_id=tid, turn_id="t"),
        _env(
            2,
            MessageEnd(
                role="assistant",
                content=[{"type": "text", "text": "hi"}],
                usage=Usage(1, 1, 0, 0, 0.0),
            ),
            trace_id=tid,
            turn_id="t",
        ),
        _env(3, TurnEnd(stop_reason="end"), trace_id=tid, turn_id="t"),
        _env(4, RunEnd(messages=[]), trace_id=tid),
    ]:
        proj.project(e)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.5


def test_inmemory_otlp_still_ok_after_c3() -> None:
    """Sanity: in-memory bridge still works alongside C3 fixtures."""
    proj, exporter = make_inmemory_otlp_projector()
    tid = "f" * 32
    proj.project(_env(0, RunStart(), trace_id=tid))
    proj.project(_env(1, RunEnd(messages=[]), trace_id=tid))
    assert exporter.get_finished_spans()
