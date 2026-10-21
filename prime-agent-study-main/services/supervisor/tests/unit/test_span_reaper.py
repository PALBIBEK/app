"""Closure reaper — idle / max_age / session_terminal / reconstruct (OTLP-bridge Step 2)."""

from __future__ import annotations

from supervisor.agent.events import Envelope, RunEnd, RunStart, ToolStart, TurnStart
from supervisor.obs.projector import JournalProjector
from supervisor.obs.reaper import ReaperConfig, SpanReaper
from supervisor.telemetry_contract.spans import SpanStatus
from tests.support.telemetry_fakes import FakeSpanSink

TRACE = "b" * 32


def _env(seq: int, payload: object, *, ts_ms: int, turn_id: str | None = None) -> Envelope:
    return Envelope(
        session_id="s1",
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=ts_ms,
        turn_id=turn_id,
        trace_id=TRACE,
        payload=payload,  # type: ignore[arg-type]
    )


def _proj() -> tuple[JournalProjector, FakeSpanSink]:
    sink = FakeSpanSink(validate=False)
    return (
        JournalProjector(
            sink=sink,
            tenant_id="t1",
            harness_id="coding",
            harness_version="coding-v1",
        ),
        sink,
    )


def test_idle_timeout_closes_unpaired_unknown() -> None:
    proj, sink = _proj()
    t0 = 1_000_000
    for e in [
        _env(0, RunStart(), ts_ms=t0),
        _env(1, TurnStart(), ts_ms=t0 + 1, turn_id="t"),
        _env(
            2,
            ToolStart(tool_call_id="x", tool_name="bash", args={}),
            ts_ms=t0 + 2,
            turn_id="t",
        ),
    ]:
        proj.project(e)

    cfg = ReaperConfig(run_idle_timeout_ms=60_000, span_max_age_ms=24 * 3600_000)
    reaper = SpanReaper(projector=proj, config=cfg)
    reason = reaper.tick(now_ms=t0 + 60_000 + 10)
    assert reason == "idle_timeout"
    assert not proj.has_open_run

    tool = next(s for s in sink.spans if s.attributes.get("gen_ai.operation.name") == "execute_tool")
    assert tool.status == SpanStatus.UNSET
    assert tool.attributes.get("yal.tool.outcome") == "unknown"
    assert tool.attributes.get("yal.closed_by") == "idle_timeout"
    assert proj.closed_by_counts.get("idle_timeout") == 1


def test_max_age_fires() -> None:
    proj, _sink = _proj()
    t0 = 1_000_000
    proj.project(_env(0, RunStart(), ts_ms=t0))
    cfg = ReaperConfig(run_idle_timeout_ms=10**12, span_max_age_ms=1000)
    reaper = SpanReaper(projector=proj, config=cfg)
    assert reaper.tick(now_ms=t0 + 1001) == "max_age"
    assert proj.closed_by_counts.get("max_age") == 1


def test_force_close_idempotent() -> None:
    proj, _sink = _proj()
    proj.project(_env(0, RunStart(), ts_ms=1))
    assert proj.force_close(end_ms=2, closed_by="idle_timeout") is True
    assert proj.force_close(end_ms=3, closed_by="idle_timeout") is False
    assert proj.closed_by_counts.get("idle_timeout") == 1


def test_reconstruct_then_reaper_after_restart() -> None:
    """kill -9 simulation: rebuild from journal, then idle-close."""
    journal = [
        _env(0, RunStart(), ts_ms=100),
        _env(1, TurnStart(), ts_ms=101, turn_id="t"),
        _env(
            2,
            ToolStart(tool_call_id="orphan", tool_name="bash", args={}),
            ts_ms=102,
            turn_id="t",
        ),
        # no RunEnd — crash
    ]
    proj, sink = _proj()
    proj.reconstruct(journal)
    assert proj.has_open_run

    cfg = ReaperConfig(run_idle_timeout_ms=50, span_max_age_ms=10**12)
    reaper = SpanReaper(projector=proj, config=cfg)
    assert reaper.tick(now_ms=200) == "idle_timeout"

    tool = next(s for s in sink.spans if s.attributes.get("yal.tool.unpaired") is True)
    assert tool.attributes.get("yal.closed_by") == "idle_timeout"


def test_session_terminal_closes_immediately() -> None:
    proj, _sink = _proj()
    proj.project(_env(0, RunStart(), ts_ms=1))
    reaper = SpanReaper(
        projector=proj,
        config=ReaperConfig(),
        session_terminal=True,
    )
    assert reaper.tick(now_ms=2) == "session_terminal"


def test_normal_run_end_counts_as_terminal() -> None:
    proj, _sink = _proj()
    proj.project(_env(0, RunStart(), ts_ms=1))
    proj.project(_env(1, RunEnd(messages=[]), ts_ms=2))
    assert proj.closed_by_counts.get("terminal") == 1
    assert not proj.has_open_run
