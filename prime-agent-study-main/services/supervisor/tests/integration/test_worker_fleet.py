"""The worker fleet over HTTP (real Temporal, scripted model): who serves, how busy, what waits."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from supervisor.temporal.contracts import CreateSession, workflow_id
from supervisor.temporal.fleet import Load, WorkerBeat, heartbeat_loop
from supervisor.temporal.sessions import SessionWorkflows
from supervisor.temporal.worker import session_worker
from tests.support.api import supervisor_api
from tests.support.githi_env import LS, Env, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = pytest.mark.asyncio(loop_scope="session")



async def _until(api: AsyncClient, ok: Callable[[dict], bool], timeout: float = 20) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            body = (await api.get("/v1/ops/workers")).json()
            if ok(body):
                return body
            await asyncio.sleep(0.2)


async def test_a_worker_reports_its_slots_and_load_and_leaves_cleanly(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS)  # the model lists files: a step that takes a while
    state = await env.state()
    state.workflows = env.sessions
    load, stop = Load(), asyncio.Event()

    def beat() -> WorkerBeat:
        return WorkerBeat("w-1", "test-host", env.queue, "home", max_runs=3, busy=load.busy, started_at_ms=1, last_seen_ms=_now())

    with slow_tool("ls", 3):
        async with (
            session_worker(env.client, state, task_queue=env.queue, max_concurrent_runs=3, load=load, identity="w-1"),
            supervisor_api(state) as api,
        ):
            assert state.heartbeats is not None
            beats = asyncio.create_task(heartbeat_loop(state.heartbeats, beat, stop, interval_s=0.2))
            idle = await _until(api, lambda b: b["status"] == "ok" and b["workers"])
            assert idle["slots_max"] == 3 and idle["slots_busy"] == 0 and idle["polling"] >= 1
            assert idle["workers"][0]["worker_id"] == "w-1" and idle["alerts"] == []

            sid = await env.create(state)
            await api.post(f"/v1/sessions/{sid}/messages", json={"command_id": "c1", "text": "list files"})
            busy = await _until(api, lambda b: b["slots_busy"] >= 1)
            assert busy["status"] == "ok"
            await env.wait_for(state, sid, run_ended())
            await _until(api, lambda b: b["slots_busy"] == 0)

            metrics = (await api.get("/v1/ops/workers/metrics")).text
            assert f'yal_worker_slots{{task_queue="{env.queue}"}} 3' in metrics
            assert f'yal_workers_serving{{task_queue="{env.queue}"}} 1' in metrics
            stop.set()
            await beats
            gone = await _until(api, lambda b: not b["workers"])
    # Its heartbeat left with it: down at once, though the server still lists its poller.
    assert gone["status"] == "down" and gone["polling"] >= 1


async def test_without_workers_the_fleet_is_down_and_waiting_work_raises_a_backlog_alert(env: Env) -> None:
    state = await env.state()
    nobody = SessionWorkflows(env.client, task_queue=f"nobody-{uuid.uuid4().hex[:8]}", backlog_alert_s=1.0)
    state.workflows = nobody
    async with supervisor_api(state) as api:
        down = (await api.get("/v1/ops/workers")).json()
        assert down["status"] == "down" and down["polling"] == 0
        assert [a["code"] for a in down["alerts"]] == ["no_workers"]

        sid = uuid.uuid4().hex
        await nobody.start(sid, CreateSession("t1", "githi-v1", principal_id="alice"))  # nobody takes it
        waiting = await _until(api, lambda b: any(a["code"] == "backlog" for a in b["alerts"]), timeout=30)
        metrics = (await api.get("/v1/ops/workers/metrics")).text
    await env.client.get_workflow_handle(workflow_id(sid)).terminate("test over")
    assert waiting["status"] == "down" and waiting["backlog"] >= 1 and waiting["backlog_age_s"] >= 1.0
    assert "yal_workers_serving" in metrics and 'yal_task_queue_backlog{task_queue="nobody-' in metrics


def _now() -> int:
    import time

    return int(time.time() * 1000)
