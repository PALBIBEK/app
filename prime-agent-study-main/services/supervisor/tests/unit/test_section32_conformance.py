"""§3.2 conformance — every AgentEvent row projects as specified (or not)."""

from __future__ import annotations

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    CompactionEnd,
    CompactionStart,
    Envelope,
    ErrorEvent,
    JobEnd,
    JobStart,
    MemoryRecall,
    MessageEnd,
    RunEnd,
    RunStart,
    Settled,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.obs.projector import JournalProjector
from tests.support.telemetry_fakes import FakeSpanSink


def _e(seq: int, payload: object, *, turn: str | None = "t1") -> Envelope:
    return Envelope(
        session_id="conf",
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=1000 + seq,
        turn_id=turn,
        trace_id="a" * 32,
        payload=payload,  # type: ignore[arg-type]
    )


def test_section_32_cheat_sheet_projections() -> None:
    sink = FakeSpanSink(validate=False)
    proj = JournalProjector(
        sink=sink, tenant_id="t", harness_id="coding", harness_version="v1"
    )
    events = [
        _e(0, RunStart(), turn=None),
        _e(1, TurnStart()),
        _e(2, Settled()),  # event-only — no new span
        _e(
            3,
            MessageEnd(
                role="assistant",
                content=[{"type": "text", "text": "x"}],
                usage=Usage(1, 1, 0, 0, 0.0),
            ),
        ),
        _e(4, ToolStart(tool_call_id="c1", tool_name="read", args={})),
        _e(
            5,
            ToolEnd(tool_call_id="c1", tool_name="read", result={}, is_error=False),
        ),
        _e(6, ErrorEvent(error_class="protocol", retryable=False, detail="x")),
        _e(
            7,
            ApprovalRequired(
                approval_id="a1",
                prompt="ok?",
                options=["allow", "deny"],
                timeout_s=30.0,
            ),
        ),
        _e(8, ApprovalResolved(approval_id="a1", choice="allow", status="responded")),
        _e(
            9,
            CompactionStart(
                compaction_id="k1", reason="threshold", tokens_before=1000
            ),
            turn=None,
        ),
        _e(
            10,
            CompactionEnd(compaction_id="k1", stop_reason="ok", tokens_after=100),
            turn=None,
        ),
        _e(
            11,
            MemoryRecall(
                block_id="b1", source="semantic", token_estimate=10, injected=False
            ),
        ),
        _e(
            12,
            SubagentStart(
                run_id="child-1",
                child_session_id="cs",
                parent_session_id="conf",
                mode="one_shot",
                provider="pi",
                label="h",
            ),
            turn=None,
        ),
        _e(13, SubagentEnd(run_id="child-1", stop_reason="ok", output=""), turn=None),
        _e(14, JobStart(job_id="j1", kind="subagent"), turn=None),
        _e(15, JobEnd(job_id="j1", stop_reason="ok"), turn=None),
        _e(16, TurnEnd(stop_reason="end")),
        _e(17, RunEnd(messages=[]), turn=None),
    ]
    for e in events:
        proj.project(e)

    names = {s.name for s in sink.spans}
    assert any(n.startswith("invoke_agent") for n in names)
    assert "yal.turn" in names
    assert "chat" in names
    assert any(n.startswith("execute_tool") for n in names)
    assert "yal.approval" in names
    assert "yal.compaction" in names
    assert "search_memory" in names
    assert "yal.job" in names
    # Settled / ErrorEvent must not create dedicated spans
    assert "settled" not in {n.lower() for n in names}
    assert not any("error" == n.lower() for n in names)
    # Sandbox never projected
    assert not any("sandbox" in n for n in names)

    turn_span = next(s for s in sink.spans if s.name == "yal.turn")
    assert turn_span.attributes.get("error.type") == "protocol"
