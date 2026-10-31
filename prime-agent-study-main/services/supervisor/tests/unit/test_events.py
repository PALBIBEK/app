"""Thin shape locks for the canonical event vocabulary (write first — red)."""

from __future__ import annotations

import dataclasses
import re
from typing import get_args, get_origin

import pytest

import supervisor.agent.events as events_mod
from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    BackgroundResults,
    CompactionEnd,
    CompactionStart,
    Envelope,
    ErrorEvent,
    HandCheckpointed,
    HandLost,
    HandProvisioned,
    HandStopped,
    JobEnd,
    JobStart,
    MemoryContext,
    MemoryInjected,
    MemoryRecall,
    MessageEnd,
    ModelRetry,
    ProjectContext,
    RunEnd,
    RunStart,
    Settled,
    SubagentEnd,
    SubagentNotice,
    SubagentStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
    WorkspaceRestored,
)


def test_envelope_constructible_and_immutable() -> None:
    env = Envelope(
        session_id="s1",
        seq=1,
        event_id="e1",
        ts_ms=1_700_000_000_000,
        turn_id=None,
        trace_id=None,
        payload=RunStart(),
    )
    assert env.session_id == "s1"
    assert env.seq == 1
    assert isinstance(env.payload, RunStart)
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.seq = 2  # type: ignore[misc]


def test_tool_start_and_end_share_tool_call_id() -> None:
    call_id = "toolu_bdrk_01DZisUPVW4XQoDoi9XQKN6J"
    start = ToolStart(tool_call_id=call_id, tool_name="read", args={"path": "a.txt"})
    end = ToolEnd(
        tool_call_id=call_id,
        tool_name="read",
        result={"content": [{"type": "text", "text": "hello world\n"}]},
        is_error=False,
    )
    assert start.tool_call_id == end.tool_call_id == call_id


def test_usage_accepts_numbers_from_fixture() -> None:
    # From tests/fixtures/pi-events.jsonl message_end (assistant, stopReason toolUse).
    usage = Usage(
        input_tokens=10,
        output_tokens=96,
        cache_read=0,
        cache_write=1801,
        cost_total=0.00822375,
    )
    assert usage.input_tokens == 10
    assert usage.output_tokens == 96
    assert usage.cache_read == 0
    assert usage.cache_write == 1801
    assert usage.cost_total == 0.00822375


def test_no_camel_case_field_names() -> None:
    """Cheap grep: field names must be snake_case — membrane must not leak."""
    camel = re.compile(r"[a-z][A-Z]")
    for name, obj in vars(events_mod).items():
        if not dataclasses.is_dataclass(obj):
            continue
        for f in dataclasses.fields(obj):
            assert camel.search(f.name) is None, f"{name}.{f.name} looks camelCase"


def test_handwritten_canonical_transcript_shapes() -> None:
    """Every value from the captured transcript has a home."""
    usage = Usage(
        input_tokens=10,
        output_tokens=96,
        cache_read=0,
        cache_write=1801,
        cost_total=0.00822375,
    )
    call_id = "toolu_bdrk_01DZisUPVW4XQoDoi9XQKN6J"
    payloads = [
        RunStart(),
        TurnStart(),
        MessageEnd(
            role="user",
            content=[{"type": "text", "text": "read a.txt and tell me what it says"}],
            usage=None,
        ),
        MessageEnd(
            role="assistant",
            content=[
                {
                    "type": "thinking",
                    "thinking": (
                        'The user wants me to read a file called "a.txt" and tell '
                        "them what it contains. I should use the read function to "
                        "read the contents of the file."
                    ),
                },
                {
                    "type": "toolCall",
                    "id": call_id,
                    "name": "read",
                    "arguments": {"path": "a.txt"},
                },
            ],
            usage=usage,
        ),
        ToolStart(tool_call_id=call_id, tool_name="read", args={"path": "a.txt"}),
        ToolEnd(
            tool_call_id=call_id,
            tool_name="read",
            result={"content": [{"type": "text", "text": "hello world\n"}]},
            is_error=False,
        ),
        TurnEnd(stop_reason="toolUse"),
        MessageEnd(
            role="assistant",
            content=[{"type": "text", "text": "The file `a.txt` contains: hello world"}],
            usage=Usage(
                input_tokens=0,
                output_tokens=0,
                cache_read=0,
                cache_write=0,
                cost_total=0.0,
            ),
        ),
        Settled(),
        RunEnd(messages=[{"role": "user"}, {"role": "assistant"}]),
        ErrorEvent(error_class="protocol", retryable=False, detail="boom"),
    ]
    for i, payload in enumerate(payloads):
        env = Envelope(
            session_id="sess-fixture",
            seq=i + 1,
            event_id=f"evt-{i + 1}",
            ts_ms=1_786_534_857_943,
            turn_id="turn-1",
            trace_id="trace-1",
            payload=payload,
        )
        assert env.payload is payload


def test_agent_event_union_covers_all_payloads() -> None:
    origin = get_origin(events_mod.AgentEvent)
    assert origin is not None
    members = set(get_args(events_mod.AgentEvent))
    expected = {
        RunStart,
        RunEnd,
        TurnStart,
        TurnEnd,
        MessageEnd,
        ToolStart,
        ToolEnd,
        Settled,
        ModelRetry,
        ProjectContext,
        WorkspaceRestored,
        SubagentNotice,
        ErrorEvent,
        ApprovalRequired,
        ApprovalResolved,
        SubagentStart,
        SubagentEnd,
        JobStart,
        JobEnd,
        CompactionStart,
        MemoryInjected,
        MemoryContext,
        BackgroundResults,
        HandProvisioned,
        HandCheckpointed,
        HandLost,
        HandStopped,
        CompactionEnd,
        MemoryRecall,
    }
    assert members == expected


def test_subagent_start_end_share_run_id() -> None:
    run_id = "run-abc"
    start = SubagentStart(
        run_id=run_id,
        child_session_id="child-1",
        parent_session_id="parent-1",
        mode="one_shot",
        provider="memory",
        label="research",
    )
    end = SubagentEnd(run_id=run_id, stop_reason="ok", output="done")
    assert start.run_id == end.run_id == run_id


def test_job_start_end_share_job_id() -> None:
    jid = "job-1"
    assert JobStart(job_id=jid, kind="subagent").job_id == JobEnd(
        job_id=jid, stop_reason="ok"
    ).job_id


def test_compaction_start_end_share_compaction_id() -> None:
    cid = "cmp-1"
    start = CompactionStart(
        compaction_id=cid,
        reason="threshold",
        tokens_before=100_000,
    )
    end = CompactionEnd(compaction_id=cid, stop_reason="ok", tokens_after=20_000)
    assert start.compaction_id == end.compaction_id == cid
