"""RED → GREEN: yal.run.id resolver (Phase1 §3.3a)."""

from __future__ import annotations

from supervisor.agent.events import Envelope, RunStart, SubagentStart
from supervisor.telemetry_contract.run_id import resolve_run_id


def test_derives_from_envelope_trace_id_for_top_level_run() -> None:
    env = Envelope(
        session_id="s1",
        seq=0,
        event_id="e1",
        ts_ms=1,
        turn_id=None,
        trace_id="a" * 32,
        payload=RunStart(),
    )
    assert resolve_run_id(env) == "a" * 32


def test_prefers_explicit_subagent_run_id() -> None:
    env = Envelope(
        session_id="s1",
        seq=5,
        event_id="e2",
        ts_ms=2,
        turn_id=None,
        trace_id="b" * 32,
        payload=SubagentStart(
            run_id="child-run-1",
            child_session_id="c1",
            parent_session_id="s1",
            mode="one_shot",
            provider="pi",
        ),
    )
    assert resolve_run_id(env) == "child-run-1"


def test_fallback_session_seq_when_no_trace() -> None:
    env = Envelope(
        session_id="s1",
        seq=7,
        event_id="e3",
        ts_ms=3,
        turn_id=None,
        trace_id=None,
        payload=RunStart(),
    )
    assert resolve_run_id(env) == "s1:7"
