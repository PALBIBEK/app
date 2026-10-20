"""D13 §8 T2 — tool start/end produce paired execute_tool spans."""

from __future__ import annotations

import pytest

from supervisor.agent.events import ToolEnd, ToolStart
from tests.d13_acceptance.conftest import (
    HEADERS,
    create_session,
    new_command_id,
    w3c_traceparent,
    wait_for_journal,
)

TRACE_ID = "b1b2c3d4e5f60718293a4b5c6d7e8f91"


@pytest.mark.asyncio
async def test_t2_tool_start_end_paired_execute_tool_spans(supervisor_stack) -> None:
    """T2: When tools fire, ToolStart/ToolEnd journal pairs → paired execute_tool spans."""
    client = supervisor_stack["client"]
    reader = supervisor_stack["reader"]
    sink = supervisor_stack["sink"]

    created = await create_session(client)
    sid = created["session_id"]

    resp = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers={**HEADERS, "traceparent": w3c_traceparent(TRACE_ID)},
        json={"text": "echo please fire tools", "command_id": new_command_id("t2")},
    )
    assert resp.status_code in (200, 202), resp.text

    events = await wait_for_journal(
        reader,
        sid,
        min_events=2,
        require_types={"ToolStart", "ToolEnd", "RunEnd"},
        timeout_secs=5.0,
    )
    starts = [e.payload for e in events if isinstance(e.payload, ToolStart)]
    ends = [e.payload for e in events if isinstance(e.payload, ToolEnd)]
    assert starts, "echo (or product runtime) must emit ToolStart for T2"
    assert ends, "echo (or product runtime) must emit ToolEnd for T2"
    start_ids = {p.tool_call_id for p in starts}
    end_ids = {p.tool_call_id for p in ends}
    assert start_ids == end_ids, (
        f"unpaired tool journal events start={start_ids} end={end_ids}"
    )

    tool_spans = [
        s
        for s in sink.spans
        if (s.attributes or {}).get("gen_ai.operation.name") == "execute_tool"
        and (
            s.yal_session_id == sid
            or (s.attributes or {}).get("yal.session.id") == sid
        )
    ]
    assert tool_spans, "projector must emit execute_tool spans when tools fire"

    # Pairing: each tool_call_id has a closed span (end_ms set) with outcome.
    by_call: dict[str, list] = {}
    for s in tool_spans:
        cid = (s.attributes or {}).get("tool_call_id")
        assert cid, f"execute_tool span missing tool_call_id: {s!r}"
        by_call.setdefault(str(cid), []).append(s)

    assert set(by_call) == start_ids, (
        f"span tool_call_ids {set(by_call)} != journal {start_ids}"
    )
    for cid, spans in by_call.items():
        closed = [s for s in spans if s.end_ms is not None]
        assert closed, f"tool_call_id={cid} never closed (projector must pair ToolEnd)"
        outcome = (closed[-1].attributes or {}).get("yal.tool.outcome")
        assert outcome in ("ok", "error"), (
            f"closed tool span must set yal.tool.outcome, got {outcome!r}"
        )
        assert closed[-1].yal_run_id == TRACE_ID
