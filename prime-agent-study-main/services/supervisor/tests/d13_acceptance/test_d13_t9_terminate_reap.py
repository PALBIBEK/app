"""D13 §8 T9 — terminate reaps open spans / session_terminal (or reconstruct) wired."""

from __future__ import annotations

import inspect

import pytest

from supervisor.agent.events import Envelope, RunStart, ToolStart, TurnStart
from supervisor.obs import tracing as tracing_mod
from supervisor.obs.reaper import SpanReaper
from tests.d13_acceptance.conftest import HEADERS, create_session

TRACE_ID = "f1b2c3d4e5f60718293a4b5c6d7e8f96"


def _open_run_envs(session_id: str, *, t0: int = 1_000_000) -> list[Envelope]:
    """Journal fragment with open run + unpaired tool (crash / mid-turn)."""
    return [
        Envelope(
            session_id=session_id,
            seq=0,
            event_id="e0",
            ts_ms=t0,
            turn_id=None,
            trace_id=TRACE_ID,
            payload=RunStart(),
        ),
        Envelope(
            session_id=session_id,
            seq=1,
            event_id="e1",
            ts_ms=t0 + 1,
            turn_id="t",
            trace_id=TRACE_ID,
            payload=TurnStart(),
        ),
        Envelope(
            session_id=session_id,
            seq=2,
            event_id="e2",
            ts_ms=t0 + 2,
            turn_id="t",
            trace_id=TRACE_ID,
            payload=ToolStart(
                tool_call_id="orphan-t9",
                tool_name="bash",
                args={"cmd": "sleep 999"},
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_t9_terminate_reaps_open_spans_session_terminal(
    supervisor_stack,
) -> None:
    """T9: DELETE session must reap open spans via session_terminal (or equivalent reconstruct).

    Unit-only SpanReaper(session_terminal=True) is insufficient — terminate path must wire it.
    """
    client = supervisor_stack["client"]
    sink = supervisor_stack["sink"]
    proj = tracing_mod.get_projector()
    assert proj is not None, "projector must be configured for T9"

    created = await create_session(client)
    sid = created["session_id"]

    # Simulate mid-run open spans on the live projector for this session.
    for env in _open_run_envs(sid):
        proj.project(env)
    assert proj.has_open_run, "precondition: open run on projector"

    resp = await client.delete(f"/v1/sessions/{sid}", headers=HEADERS)
    assert resp.status_code in (200, 204), resp.text

    # Production: terminate signals session_terminal → force_close open spans.
    assert not proj.has_open_run, (
        "terminate must reap open run spans (session_terminal / force_close); "
        "projector still has_open_run — wire terminate → SpanReaper(session_terminal=True) "
        "or equivalent per-session close"
    )
    assert proj.closed_by_counts.get("session_terminal", 0) >= 1 or any(
        (s.attributes or {}).get("yal.closed_by") == "session_terminal" for s in sink.spans
    ), (
        "expected closed_by=session_terminal on terminate reap "
        f"(counts={proj.closed_by_counts})"
    )


def test_t9_terminate_or_manager_source_wires_session_terminal() -> None:
    """T9 source contract: terminate / lifecycle must reference session_terminal or reconstruct."""
    from supervisor.session import manager as manager_mod

    mgr_src = inspect.getsource(manager_mod.SessionManager.terminate)
    assert (
        "session_terminal" in mgr_src
        or "force_close" in mgr_src
        or "get_projector" in mgr_src
        or "span_reaper" in mgr_src.lower()
        or "reap" in mgr_src.lower()
        or "reconstruct" in mgr_src
    ), (
        "SessionManager.terminate must wire span reap / session_terminal / force_close / "
        "projector close — currently unit-only SpanReaper path is not enough (D13 §5 A / T9)"
    )
    assert hasattr(SpanReaper, "tick")
    assert "session_terminal" in inspect.getsource(SpanReaper)
