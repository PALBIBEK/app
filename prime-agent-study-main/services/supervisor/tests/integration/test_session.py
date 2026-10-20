"""Step 5 integration — lifecycle, crash recovery, fork, leak checks."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from supervisor.agent.events import RunEnd, ToolEnd, ToolStart
from supervisor.session.recovery import ResumeState
from tests.support.waits import counted, journalled


async def _drain_to_run_end(m, sid: str) -> None:
    q = m.live_queue(sid)
    for _ in range(50):
        env = await asyncio.wait_for(q.get(), timeout=2)
        if isinstance(env.payload, RunEnd):
            return


@pytest.mark.asyncio
async def test_level1_echo_full_lifecycle_seq_continues(session_env) -> None:
    m = session_env["manager"]
    reader = session_env["reader"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    await m.send(sid, "one", command_id="c1")
    await _drain_to_run_end(m, sid)
    first = await journalled(reader, sid, counted("RunEnd"))
    last = first[-1].seq

    # Drop warm runtime without terminating the durable session (cold path).
    await m._stop_runtime_keep_session(sid)
    result = await m.resume(sid)
    assert result.state == ResumeState.COLD

    await m.send(sid, "two", command_id="c2")
    await _drain_to_run_end(m, sid)
    all_events = await journalled(reader, sid, counted("RunEnd", 2))
    seqs = [e.seq for e in all_events]
    assert seqs == list(range(len(seqs)))
    assert all_events[len(first)].seq == last + 1
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_level2_crash_recovery_needs_review_then_cold(session_env) -> None:
    m = session_env["manager"]
    writer = session_env["writer"]
    reader = session_env["reader"]
    session_env["registry"]
    catalog = session_env["catalog"]
    from supervisor.session.manager import SessionManager
    from supervisor.session.registry import SessionRegistry

    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    await writer.append(
        sid,
        ToolStart(tool_call_id="orphan", tool_name="git_commit", args={}),
        turn_id="t",
        trace_id=None,
    )
    # Drop manager entirely — new instance, empty registry.
    m2 = SessionManager(
        session_factory=session_env["factory"],
        writer=writer,
        reader=reader,
        registry=SessionRegistry(),
        catalog=catalog,
        sandbox_provider=session_env["provider"],
    )
    result = await m2.resume(sid)
    assert result.state == ResumeState.NEEDS_REVIEW

    await writer.append(
        sid,
        ToolEnd(tool_call_id="orphan", tool_name="git_commit", result={}, is_error=False),
        turn_id="t",
        trace_id=None,
    )
    result = await m2.resume(sid)
    assert result.state == ResumeState.COLD
    await m2.terminate(sid)


@pytest.mark.asyncio
async def test_level3_fork_independent_prefix_copy(session_env) -> None:
    m = session_env["manager"]
    reader = session_env["reader"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    await m.send(sid, "one", command_id="c1")
    await _drain_to_run_end(m, sid)
    await journalled(reader, sid, counted("RunEnd"))
    await m.send(sid, "two", command_id="c2")
    await _drain_to_run_end(m, sid)
    await journalled(reader, sid, counted("RunEnd", 2))

    original_before = await m.snapshot_events(sid)
    # Fork at end of first turn (last seq of first RunEnd).
    first_run_end_seq = None
    for env in await reader.read(sid, since=-1):
        if isinstance(env.payload, RunEnd) and first_run_end_seq is None:
            first_run_end_seq = env.seq
            break
    assert first_run_end_seq is not None

    fork_id = await m.fork(sid, at_seq=first_run_end_seq)
    assert fork_id != sid

    fork_events = await reader.read(fork_id, since=-1)
    assert [e.seq for e in fork_events] == list(range(len(fork_events)))
    assert len(fork_events) == first_run_end_seq + 1

    original_after = await m.snapshot_events(sid)
    assert original_before == original_after
    await m.terminate(sid)
    await m.terminate(fork_id)


SRC = Path(__file__).resolve().parents[2] / "src" / "supervisor"
SESSION = SRC / "session"
CAMEL = re.compile(r"[a-z][A-Z]")




