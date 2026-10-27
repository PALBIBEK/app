"""A run's journal becomes a tree of typed spans: nesting, timing, outcomes and the rollup.

Each test journals one run by hand (seq and timestamps chosen) and reads the trace back.
"""

from __future__ import annotations

from typing import Any

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    CompactionEnd,
    CompactionStart,
    Envelope,
    ErrorEvent,
    HandCheckpointed,
    HandProvisioned,
    MemoryInjected,
    MessageEnd,
    RunEnd,
    RunStart,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.obs.projector import JournalProjector
from supervisor.telemetry_contract.spans import SpanRecord
from supervisor.traces import RunStatus, RunTrace, SpanKind, SpanStatus, build_run_trace, span_id
from supervisor.traces.build import DETAIL_LIMIT

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


def approved_bash_run() -> Journal:
    """Ask, think 1 s, wait 10 s on a person, run bash 2 s, answer."""
    return (
        Journal()
        .at(0, RunStart())
        .at(0, user("run the tests"))
        .at(10, TurnStart())
        .at(1_000, model(calls=[("c1", "bash", {"command": "pytest -q"})]))
        .at(1_000, ApprovalRequired("a1", "Allow bash?", ["allow", "deny"], 300,
                                    metadata={"tool": "bash", "tool_call_id": "c1"}))
        .at(11_000, ApprovalResolved("a1", "allow", "responded", responded_by="alice"))
        .at(11_000, ToolStart("c1", "bash", {"command": "pytest -q"}))
        .at(13_000, ran("c1", "bash", {"exit_code": 0, "stdout": "3 passed\n", "stderr": ""}, ms=2_000))
        .at(13_000, TurnStart())
        .at(14_000, model("All 3 tests pass.", cost=0.02))
        .at(14_000, TurnEnd("stop"))
        .at(14_000, RunEnd(messages=[]))
    )


def by_kind(trace: RunTrace, kind: SpanKind) -> list:
    return [s for s in trace.spans if s.kind is kind]


def test_a_run_nests_steps_model_calls_tools_and_the_approval_that_gated_a_tool() -> None:
    trace = approved_bash_run().trace()
    (root,) = by_kind(trace, SpanKind.AGENT)
    step1, step2 = by_kind(trace, SpanKind.STEP)
    llm1, llm2 = by_kind(trace, SpanKind.LLM)
    (tool,) = by_kind(trace, SpanKind.TOOL)
    (approval,) = by_kind(trace, SpanKind.APPROVAL)

    assert root.parent_id is None and root.name == "reviewer" and root.preview == "run the tests"
    assert step1.parent_id == step2.parent_id == root.id
    assert llm1.parent_id == tool.parent_id == step1.id and llm2.parent_id == step2.id
    assert approval.parent_id == tool.id  # the approval sits under the call it gated
    assert tool.start_ms == 1_000 and tool.end_ms == 13_000 and tool.attrs["exec_ms"] == 2_000
    assert tool.attrs["exit_code"] == 0 and tool.preview == "$ pytest -q"
    assert tool.attrs["outcome"] == "exit 0 · 3 passed"
    assert approval.attrs["wait_ms"] == 10_000 and approval.preview == "allowed by alice after 10s"
    assert llm1.start_ms == 10 and llm1.end_ms == 1_000  # from the step's start to the reply
    assert llm1.preview == "asks for bash" and llm2.preview == "All 3 tests pass."


def test_the_rollup_says_how_it_went_and_where_the_time_went() -> None:
    r = approved_bash_run().trace().rollup
    assert r.status is RunStatus.OK and r.reason is None
    assert (r.started_ms, r.ended_ms, r.duration_ms) == (0, 14_000, 14_000)
    # Waiting on a person is not latency: 10 s human, 2 s tool, the model's ~2 s, the rest other.
    assert (r.time.human_ms, r.time.tool_ms, r.time.model_ms, r.time.other_ms) == (10_000, 2_000, 1_990, 10)
    assert (r.steps, r.llm_calls, r.tool_calls, r.approvals, r.denials) == (2, 2, 1, 1, 0)
    assert r.cost == 0.03 and r.input_tokens == 200 and r.models == ["m1"] and r.tools == {"bash": 1}
    assert r.input == "run the tests" and r.output == "All 3 tests pass."


def test_span_ids_are_the_same_every_time_the_trace_is_built() -> None:
    first, second = approved_bash_run().trace(), approved_bash_run().trace()
    assert [s.id for s in first.spans] == [s.id for s in second.spans]
    assert first.spans[0].id == span_id(SID, RUN, "run")
    assert first.trace_id == second.trace_id and len(first.trace_id) == 32


def test_a_denied_call_is_marked_denied_not_failed() -> None:
    trace = (
        Journal()
        .at(0, RunStart())
        .at(0, TurnStart())
        .at(500, model(calls=[("c1", "bash", {"command": "rm -rf build"})]))
        .at(500, ApprovalRequired("a1", "Allow bash?", ["allow", "deny"], 60,
                                  metadata={"tool": "bash", "tool_call_id": "c1"}))
        .at(60_500, ApprovalResolved("a1", "deny", "timed_out"))
        .at(60_500, ToolStart("c1", "bash", {"command": "rm -rf build"}))
        .at(60_510, ran("c1", "bash", ok=False, code="APPROVAL_DENIED", error="denied by approval"))
        .at(60_600, TurnEnd("stop"))
        .at(60_600, RunEnd(messages=[]))
        .trace()
    )
    (tool,) = by_kind(trace, SpanKind.TOOL)
    (approval,) = by_kind(trace, SpanKind.APPROVAL)
    assert tool.status is SpanStatus.OK and tool.attrs["denied"] and tool.attrs["exec_ms"] == 0
    assert tool.preview == "not run: denied by approval"
    assert approval.attrs["denied"] and approval.preview == "no answer in 1 min, so deny"
    assert trace.rollup.status is RunStatus.OK
    assert (trace.rollup.denials, trace.rollup.tool_errors, trace.rollup.errors) == (1, 0, 0)
    assert trace.rollup.time.tool_ms == 0 and trace.rollup.time.human_ms == 60_000


def test_a_failed_tool_marks_every_span_above_it() -> None:
    trace = (
        Journal()
        .at(0, RunStart())
        .at(0, TurnStart())
        .at(100, model(calls=[("c1", "read", {"file_path": "missing.py"})]))
        .at(100, ToolStart("c1", "read", {"file_path": "missing.py"}))
        .at(120, ran("c1", "read", ok=False, code="NOT_FOUND", error="no such file: missing.py"))
        .at(200, TurnEnd("stop"))
        .at(200, RunEnd(messages=[]))
        .trace()
    )
    (tool,) = by_kind(trace, SpanKind.TOOL)
    (step,) = by_kind(trace, SpanKind.STEP)
    (root,) = by_kind(trace, SpanKind.AGENT)
    assert tool.status is SpanStatus.ERROR
    assert tool.error == {"class": "NOT_FOUND", "message": "no such file: missing.py"}
    assert tool.preview == "read missing.py" and tool.attrs["outcome"] == "no such file: missing.py"
    assert step.error_below and root.error_below and not tool.error_below
    assert trace.rollup.status is RunStatus.OK  # a tool failing is not the run failing
    assert (trace.rollup.tool_errors, trace.rollup.errors) == (1, 1)


def test_how_a_run_ended_names_its_status_and_reason() -> None:
    def ending(*events: Any) -> RunTrace:
        j = Journal().at(0, RunStart()).at(0, TurnStart())
        for i, e in enumerate(events, start=1):
            j.at(i * 10, e)
        return j.at(999, RunEnd(messages=[])).trace()

    failed = ending(ErrorEvent("GatewayError", False, "model unavailable"), TurnEnd("error"))
    assert failed.rollup.status is RunStatus.ERROR
    assert failed.rollup.reason == "GatewayError: model unavailable"
    assert failed.spans[0].status is SpanStatus.ERROR and failed.rollup.errors == 1

    spent = ending(ErrorEvent("BudgetExceeded", False, "cost cap $5.00 reached"), TurnEnd("budget_exceeded"))
    assert spent.rollup.status is RunStatus.BUDGET_EXCEEDED and spent.rollup.reason == "cost cap $5.00 reached"

    stopped = ending(TurnEnd("aborted"))
    assert stopped.rollup.status is RunStatus.CANCELLED


def test_an_unfinished_run_is_running_waiting_or_interrupted() -> None:
    def open_run() -> Journal:
        return (
            Journal()
            .at(0, RunStart())
            .at(0, TurnStart())
            .at(100, model(calls=[("c1", "bash", {"command": "make"})]))
            .at(100, ApprovalRequired("a1", "Allow bash?", ["allow", "deny"], 300,
                                      metadata={"tool": "bash", "tool_call_id": "c1"}))
        )

    waiting = open_run().trace()
    assert waiting.rollup.status is RunStatus.AWAITING_APPROVAL and waiting.rollup.ended_ms is None
    assert waiting.spans[0].status is SpanStatus.RUNNING and waiting.rollup.duration_ms is None

    lost = open_run().trace(running=False)
    assert lost.rollup.status is RunStatus.INTERRUPTED
    open_spans = [s for s in lost.spans if s.kind in (SpanKind.TOOL, SpanKind.APPROVAL, SpanKind.STEP)]
    assert all(s.status is SpanStatus.UNFINISHED and s.end_ms == 100 for s in open_spans)


def test_a_file_change_carries_what_was_removed_and_added() -> None:
    args = {"file_path": "src/dates.py", "old_text": "a[:19]", "new_text": "fromisoformat(a)"}
    trace = (
        Journal()
        .at(0, RunStart()).at(0, TurnStart())
        .at(10, model(calls=[("c1", "edit", args)]))
        .at(10, ToolStart("c1", "edit", args))
        .at(20, ran("c1", "edit", {"path": "src/dates.py"}))
        .at(30, TurnEnd("stop")).at(30, RunEnd(messages=[]))
        .trace()
    )
    (tool,) = by_kind(trace, SpanKind.TOOL)
    assert tool.preview == "edit src/dates.py"
    assert tool.detail["change"] == {"path": "src/dates.py", "removed": "a[:19]", "added": "fromisoformat(a)",
                                     "replace_all": False}


def test_sandbox_events_between_a_runs_events_become_markers_and_other_runs_are_ignored() -> None:
    trace = (
        Journal()
        .at(0, RunStart()).at(0, TurnStart())
        .at(5, HandProvisioned("h1", "local_docker", "first_use"), run=None)
        .at(6, ToolStart("other", "bash", {}), run="run-2")
        .at(10, model("done"))
        .at(12, HandCheckpointed("h1", files=2, removed=1), run=None)
        .at(20, TurnEnd("stop")).at(20, RunEnd(messages=[]))
        .trace()
    )
    (step,) = by_kind(trace, SpanKind.STEP)
    assert [m.text for m in step.markers] == [
        "default sandbox started on local_docker (first use)",
        "default sandbox saved: 2 files changed, 1 removed",
    ]
    assert not by_kind(trace, SpanKind.TOOL)


def test_subagents_compaction_and_recall_are_spans_of_their_own() -> None:
    trace = (
        Journal()
        .at(0, RunStart()).at(0, TurnStart())
        .at(5, MemoryInjected(run_id=RUN, text="- prefers pytest", shown=[{"id": "f1"}]))
        .at(6, MemoryInjected(run_id=RUN, text="", shown=[]))
        .at(10, CompactionStart("k1", "threshold", 90_000))
        .at(20, CompactionEnd("k1", "ok", tokens_after=12_000))
        .at(30, SubagentStart("sub1", "child", SID, "one_shot", "nested", label="researcher"))
        .at(90, SubagentEnd("sub1", "stop", output="Found 3 call sites"))
        .at(100, TurnEnd("stop")).at(100, RunEnd(messages=[]))
        .trace()
    )
    (memory,) = by_kind(trace, SpanKind.MEMORY)  # an empty recall is not worth a row
    (compaction,) = by_kind(trace, SpanKind.COMPACTION)
    sub = [s for s in by_kind(trace, SpanKind.AGENT) if s.parent_id is not None]
    assert memory.preview == "1 fact recalled"
    assert compaction.preview == "90000 → 12000 tokens" and compaction.duration_ms == 10
    assert len(sub) == 1 and sub[0].name == "researcher" and sub[0].preview == "Found 3 call sites"
    assert sub[0].attrs["child_session_id"] == "child"


def test_long_content_is_cut_in_detail_and_left_out_of_the_tree() -> None:
    huge = "x" * (DETAIL_LIMIT + 500)
    trace = (
        Journal()
        .at(0, RunStart()).at(0, TurnStart())
        .at(10, model(calls=[("c1", "read", {"file_path": "big.txt"})]))
        .at(10, ToolStart("c1", "read", {"file_path": "big.txt"}))
        .at(20, ran("c1", "read", huge))
        .at(30, TurnEnd("stop")).at(30, RunEnd(messages=[]))
        .trace()
    )
    (tool,) = by_kind(trace, SpanKind.TOOL)
    kept = tool.detail["result"]["payload"]
    assert kept.startswith("x" * 100) and kept.endswith("[500 more characters]")
    assert tool.attrs["outcome"] == "1 line"
    assert all("detail" not in s for s in trace.to_dict()["spans"])
    assert "detail" in trace.to_dict(full=True)["spans"][0]


def test_a_run_with_no_events_has_no_trace() -> None:
    assert build_run_trace(SID, RUN, [], agent="reviewer") is None


class _Sink:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []

    def export(self, spans: list[SpanRecord]) -> None:
        self.spans.extend(spans)


def test_the_otlp_projector_gives_spans_the_ids_and_kinds_the_trace_view_uses() -> None:
    journal = approved_bash_run()
    sink = _Sink()
    projector = JournalProjector(sink=sink, tenant_id="t1", harness_id="reviewer", harness_version="v1")
    projector.project_many(journal.envs)
    trace = journal.trace()
    exported = {s.span_id: s for s in sink.spans}
    for kind in (SpanKind.AGENT, SpanKind.TOOL, SpanKind.LLM, SpanKind.APPROVAL, SpanKind.STEP):
        for span in by_kind(trace, kind):
            assert span.id in exported, (kind, span.name)
            assert exported[span.id].attributes["yal.span.kind"] == kind.value
    tool = by_kind(trace, SpanKind.TOOL)[0]
    assert exported[tool.id].attributes["openinference.span.kind"] == "TOOL"
