"""Runs over HTTP: fetch by id (202 until ended), list, cancel (real Temporal, scripted model)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from supervisor.agent.events import ApprovalRequired, RunStart
from supervisor.temporal.worker import session_worker
from tests.support.api import supervisor_api
from tests.support.githi_env import WRITE, Env, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")



async def _send(api: AsyncClient, sid: str, command_id: str, text: str) -> dict:
    r = await api.post(f"/v1/sessions/{sid}/messages", json={"command_id": command_id, "text": text})
    assert r.status_code == 202, r.text
    return r.json()


async def _run(api: AsyncClient, sid: str, run_id: str, *, until: set[str], timeout: float = 20) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            r = await api.get(f"/v1/sessions/{sid}/runs/{run_id}")
            body = r.json()
            if body["status"] in until:
                assert r.status_code == (200 if body["status"] in {"completed", "failed", "cancelled", "incomplete"} else 202)
                return body
            await asyncio.sleep(0.2)


async def test_a_run_is_fetched_by_the_id_its_message_got(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model()  # the model answers without tools
    state = await env.state()
    state.workflows = env.sessions  # the API drives sessions through their workflows
    async with session_worker(env.client, state, task_queue=env.queue), supervisor_api(state) as api:
        sid = await env.create(state)
        sent = await _send(api, sid, "c1", "hello")
        done = await _run(api, sid, sent["turn_id"], until={"completed", "failed"})
        events = await env.wait_for(state, sid, run_ended())
    run_start = next(e for e in events if isinstance(e.payload, RunStart))
    assert run_start.turn_id == sent["turn_id"]  # one id for the message, its run and its events
    assert done["status"] == "completed" and done["stop_reason"] == "stop"
    assert done["output_text"] and done["usage"]["total_tokens"] > 0
    assert done["started_at_ms"] <= done["ended_at_ms"] and done["steps"] == 1


async def test_queued_and_running_runs_can_be_cancelled_and_cancel_is_idempotent(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    state.workflows = env.sessions  # the API drives sessions through their workflows
    async with session_worker(env.client, state, task_queue=env.queue), supervisor_api(state) as api:
        sid = await env.create(state, "githi-approve-allow")
        first = await _send(api, sid, "c1", "write a note")
        await env.wait_for(state, sid, lambda evs: any(isinstance(e.payload, ApprovalRequired) for e in evs))
        waiting = await _run(api, sid, first["turn_id"], until={"requires_action"})
        assert waiting["pending_approval_id"]

        second = await _send(api, sid, "c2", "and then this")
        assert second["queued"] is True
        assert (await _run(api, sid, second["turn_id"], until={"queued"}))["status"] == "queued"
        dropped = await api.post(f"/v1/sessions/{sid}/runs/{second['turn_id']}/cancel")
        assert dropped.status_code == 200 and dropped.json()["status"] == "cancelled"

        stopped = await api.post(f"/v1/sessions/{sid}/runs/{first['turn_id']}/cancel")
        body = stopped.json() if stopped.status_code == 200 else await _run(api, sid, first["turn_id"], until={"cancelled"})
        assert body["status"] == "cancelled" and body["stop_reason"] == "aborted"
        again = await api.post(f"/v1/sessions/{sid}/runs/{first['turn_id']}/cancel")
        assert again.status_code == 200 and again.json() == body  # idempotent: the final run

        listed = (await api.get(f"/v1/sessions/{sid}/runs")).json()
        missing = await api.get(f"/v1/sessions/{sid}/runs/no-such-run")
    assert [(r["command_id"], r["status"]) for r in listed] == [("c2", "cancelled"), ("c1", "cancelled")]
    assert missing.status_code == 404
