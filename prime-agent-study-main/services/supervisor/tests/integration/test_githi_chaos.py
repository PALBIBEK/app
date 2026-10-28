"""Chaos: the worker doing the work is killed again and again during one multi-step run.

The pattern of Temporal's pi-temporal demo (a worker killed every few seconds; the task
submitted once and never again): the run must still finish, every step journalled
once, and the files must be what the steps wrote.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from supervisor.agent.events import ToolEnd, ToolStart, TurnEnd, WorkspaceRestored
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.dir_storage import shared_workspace_store
from tests.support.githi_env import Env, event_types, payloads, run_ended, spawn_worker
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]

STEPS = [("write", {"file_path": f"part{n}.txt", "content": f"part {n} done"}) for n in range(6)]
KILL_AFTER_S = [1.3, 2.1, 0.9, 1.7]  # fixed, so a failure reproduces


async def test_a_run_survives_its_worker_being_killed_again_and_again(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    shared = env.blob_dir / "checkpoints"  # every worker saves and restores files here
    store = shared_workspace_store(env.factory, shared)  # type: ignore[arg-type]
    observer = await env.state(workspace_store=store)
    await env.composition(observer, "githi-v1")
    spec = {
        "scripted_calls": [list(s) for s in STEPS],
        "one_at_a_time": True,
        "slow_tool": ["write", 0.7],
        "workspace_dir": str(shared),
    }
    worker, _ = await spawn_worker(env, spec)
    sid = await env.create(observer)
    await env.sessions.submit(sid, Message("c1", "write the six parts"))
    kills = 0
    try:
        for pause in KILL_AFTER_S:
            await asyncio.sleep(pause)
            if run_ended()(await observer.reader.read(sid)):
                break
            worker.kill()  # SIGKILL, mid-whatever
            await worker.wait()
            kills += 1
            worker, _ = await spawn_worker(env, spec)
        events = await env.wait_for(observer, sid, run_ended(), timeout=120)
    finally:
        worker.kill()
        await worker.wait()

    assert kills >= 3, "the run ended before the chaos began"
    assert event_types(events).count("RunStart") == 1, "submitted once, run once"
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
    ends = payloads(events, ToolEnd)
    steps = sorted(e.tool_call_id for e in ends if e.tool_call_id.startswith("step-"))
    assert steps == [f"step-{n}" for n in range(6)], "each step answered once"
    assert not any(e.is_error for e in ends)
    # A step whose change a rebuilt sandbox lost was named to the model, and redone.
    for restored in payloads(events, WorkspaceRestored):
        for lost in restored.lost:
            n = lost.split("step-")[1].rstrip(")")
            assert f"redo-{n}" in {e.tool_call_id for e in ends}, restored
    starts = [e.tool_call_id for e in payloads(events, ToolStart)]
    assert len(starts) == len(set(starts)), "a step's start is journalled once, however often it ran"

    # The files are what the steps wrote, read back by a fresh worker (from the checkpoints).
    gateway = model(*[("read", {"file_path": f"part{n}.txt"}) for n in range(6)])
    state = await env.state(workspace_store=store)
    async with session_worker(env.client, state, task_queue=env.queue):
        await env.sessions.submit(sid, Message("c2", "read them back"))
        events = await env.wait_for(state, sid, run_ended(2))
    reads = [e for e in payloads(events, ToolEnd) if e.tool_name == "read"]
    # (read-only calls run together: they end in any order)
    assert sorted(r.result.get("payload") for r in reads) == [f"part {n} done" for n in range(6)]
    assert gateway is not None
