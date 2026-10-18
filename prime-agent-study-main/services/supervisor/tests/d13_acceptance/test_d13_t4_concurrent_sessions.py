"""D13 §8 T4 — two concurrent sessions must not cross-talk in the projector."""

from __future__ import annotations

import asyncio

import pytest

from supervisor.agent.events import Envelope, RunEnd, RunStart, ToolStart, TurnStart
from supervisor.obs import tracing as tracing_mod
from tests.d13_acceptance.conftest import (
    HEADERS,
    create_session,
    new_command_id,
    wait_for_journal,
    w3c_traceparent,
)

TRACE_A = "d1b2c3d4e5f60718293a4b5c6d7e8f93"
TRACE_B = "d1b2c3d4e5f60718293a4b5c6d7e8f94"


def _env(
    session_id: str,
    seq: int,
    payload: object,
    *,
    trace_id: str,
    turn_id: str | None = None,
    ts_ms: int = 1_000_000,
) -> Envelope:
    return Envelope(
        session_id=session_id,
        seq=seq,
        event_id=f"{session_id}-{seq}",
        ts_ms=ts_ms + seq,
        turn_id=turn_id,
        trace_id=trace_id,
        payload=payload,  # type: ignore[arg-type]
    )


def test_t4_per_session_projector_api_exists() -> None:
    """D13 §5 A: projector state must be addressable per session (not only a process global)."""
    has_api = (
        hasattr(tracing_mod, "ProjectorHub")
        or hasattr(tracing_mod, "get_projector_for_session")
        or hasattr(tracing_mod, "ProjectorRegistry")
        or hasattr(tracing_mod, "session_projectors")
        or hasattr(tracing_mod, "get_or_create_session_projector")
    )
    hub = tracing_mod.get_projector()
    hub_ok = hub is not None and (
        hasattr(hub, "session_projector")
        or hasattr(hub, "ensure_session")
        or hasattr(hub, "force_close_session")
    )
    assert has_api or hub_ok, (
        "per-session projector API missing (ProjectorHub / get_projector_for_session) — "
        "global singleton maps cross-talk under concurrency (D13 §5 A / T4)"
    )


@pytest.mark.asyncio
async def test_t4_interleaved_open_runs_do_not_cascade_across_sessions(
    supervisor_stack,
) -> None:
    """T4: Session B RunEnd must not cascade-close Session A's open tool spans.

    A process-global JournalProjector fails this — B's run_end sweeps A's _tools map.
    """
    sink = supervisor_stack["sink"]
    hub = tracing_mod.get_projector()
    assert hub is not None

    # Prefer explicit per-session projectors when the hub exposes them.
    get_for = getattr(tracing_mod, "get_projector_for_session", None)
    if callable(get_for):
        proj_a = get_for("sess-a-t4")
        proj_b = get_for("sess-b-t4")
    elif hasattr(hub, "ensure_session"):
        proj_a = hub.ensure_session("sess-a-t4")
        proj_b = hub.ensure_session("sess-b-t4")
    else:
        proj_a = hub
        proj_b = hub

    assert proj_a is not None and proj_b is not None

    for e in [
        _env("sess-a-t4", 0, RunStart(), trace_id=TRACE_A),
        _env("sess-a-t4", 1, TurnStart(), trace_id=TRACE_A, turn_id="ta"),
        _env(
            "sess-a-t4",
            2,
            ToolStart(tool_call_id="tool-A-only", tool_name="bash", args={}),
            trace_id=TRACE_A,
            turn_id="ta",
        ),
    ]:
        proj_a.project(e)

    for e in [
        _env("sess-b-t4", 0, RunStart(), trace_id=TRACE_B),
        _env("sess-b-t4", 1, TurnStart(), trace_id=TRACE_B, turn_id="tb"),
        _env(
            "sess-b-t4",
            2,
            ToolStart(tool_call_id="tool-B-only", tool_name="bash", args={}),
            trace_id=TRACE_B,
            turn_id="tb",
        ),
        _env("sess-b-t4", 3, RunEnd(messages=[]), trace_id=TRACE_B),
    ]:
        proj_b.project(e)

    # A's tool must remain open — not swept by B's RunEnd on a shared open-span map.
    a_tools = [
        s
        for s in sink.spans
        if (s.attributes or {}).get("tool_call_id") == "tool-A-only"
    ]
    assert a_tools, "expected execute_tool span for session A"
    swept = [
        s
        for s in a_tools
        if (s.attributes or {}).get("yal.tool.unpaired") is True
        or (
            s.end_ms is not None
            and (s.attributes or {}).get("yal.closed_by") == "run_end"
            and s.yal_run_id == TRACE_A
        )
    ]
    assert not swept, (
        "session B RunEnd cascade-closed session A tool spans — "
        "projector must be per-session (D13 T4 / §5 A)"
    )
    assert any(s.end_ms is None for s in a_tools), (
        "session A open-tool state must survive session B terminal"
    )


@pytest.mark.asyncio
async def test_t4_concurrent_sessions_no_projector_cross_talk(supervisor_stack) -> None:
    """T4: Two concurrent sessions → spans/journal stay session-scoped (no global map bleed)."""
    client = supervisor_stack["client"]
    reader = supervisor_stack["reader"]
    sink = supervisor_stack["sink"]

    created_a = await create_session(client)
    created_b = await create_session(client)
    sid_a = created_a["session_id"]
    sid_b = created_b["session_id"]
    assert sid_a != sid_b

    async def _run(sid: str, trace_id: str, text: str) -> list:
        resp = await client.post(
            f"/v1/sessions/{sid}/messages",
            headers={**HEADERS, "traceparent": w3c_traceparent(trace_id)},
            json={"text": text, "command_id": new_command_id(sid[:8])},
        )
        assert resp.status_code in (200, 202), resp.text
        return await wait_for_journal(
            reader,
            sid,
            min_events=1,
            require_types={"RunEnd"},
            timeout_secs=6.0,
        )

    events_a, events_b = await asyncio.gather(
        _run(sid_a, TRACE_A, "concurrent-A-payload-UNIQUE"),
        _run(sid_b, TRACE_B, "concurrent-B-payload-UNIQUE"),
    )
    assert any(isinstance(e.payload, RunEnd) for e in events_a)
    assert any(isinstance(e.payload, RunEnd) for e in events_b)

    for e in events_a:
        assert e.trace_id == TRACE_A, f"session A journal polluted: {e.trace_id!r}"
        assert e.session_id == sid_a
    for e in events_b:
        assert e.trace_id == TRACE_B, f"session B journal polluted: {e.trace_id!r}"
        assert e.session_id == sid_b

    assert sink.spans, "expected projected spans for both sessions"

    spans_a = [s for s in sink.spans if s.yal_session_id == sid_a]
    spans_b = [s for s in sink.spans if s.yal_session_id == sid_b]
    assert spans_a, "missing spans for session A — per-session projector required"
    assert spans_b, "missing spans for session B — per-session projector required"

    for s in spans_a:
        assert s.yal_run_id == TRACE_A, (
            f"session A span {s.name!r} has foreign run_id={s.yal_run_id!r}"
        )
        assert s.yal_session_id == sid_a
    for s in spans_b:
        assert s.yal_run_id == TRACE_B, (
            f"session B span {s.name!r} has foreign run_id={s.yal_run_id!r}"
        )
        assert s.yal_session_id == sid_b

    tools_a = {
        (s.attributes or {}).get("tool_call_id")
        for s in spans_a
        if (s.attributes or {}).get("gen_ai.operation.name") == "execute_tool"
    }
    tools_b = {
        (s.attributes or {}).get("tool_call_id")
        for s in spans_b
        if (s.attributes or {}).get("gen_ai.operation.name") == "execute_tool"
    }
    tools_a.discard(None)
    tools_b.discard(None)
    if tools_a and tools_b:
        assert tools_a.isdisjoint(tools_b), (
            f"tool_call_id cross-talk A={tools_a} B={tools_b}"
        )
