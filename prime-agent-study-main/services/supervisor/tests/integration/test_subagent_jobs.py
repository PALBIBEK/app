"""Background subagent jobs as durable child workflows, against a real Temporal server.

A job's result always reaches its session's journal (JobEnd + SubagentEnd), and
nothing a worker or the session does can lose it or record it twice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.client import WorkflowExecutionStatus
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import JobEnd, JobStart, SubagentEnd
from supervisor.app import SupervisorState
from supervisor.main import create_app
from supervisor.temporal.contracts import Message, job_workflow_id, workflow_id
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import Env, has_event, payloads, run_ended, spawn_worker
from tests.support.scripted import ScriptedGateway, slow_subagent
from tests.support.workflow_histories import replayable_histories

pytestmark = pytest.mark.asyncio(loop_scope="session")

HEADERS = {"X-Tenant-Id": "t1"}


@asynccontextmanager
async def api(env: Env) -> AsyncIterator[AsyncClient]:
    state = await env.state()
    state.workflows = env.sessions
    async with AsyncClient(
        transport=ASGITransport(app=create_app(state=state)), base_url="http://test"
    ) as http:
        yield http


async def _provider(state: SupervisorState, sid: str) -> str:
    """A provider the session's composition actually registers."""
    row = await state.manager._get_session_row(sid)
    return state.manager._subagent_ops(sid, row)._transport._registry.names()[0]


async def _start(http: AsyncClient, sid: str, provider: str, prompt: str = "bg work") -> str:
    resp = await http.post(
        f"/v1/sessions/{sid}/subagents/background",
        json={"prompt": prompt, "provider": provider},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["job_id"]


def _ended(job_id: str) -> Callable[[list], bool]:
    return has_event(lambda p: isinstance(p, JobEnd) and p.job_id == job_id)


def _end(events: list, job_id: str) -> JobEnd:
    (end,) = [e for e in payloads(events, JobEnd) if e.job_id == job_id]
    return end


async def test_a_job_result_reaches_its_session(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    worker = await env.state()
    async with session_worker(env.client, worker, task_queue=env.queue), api(env) as http:
        sid = await env.create(worker)
        job_id = await _start(http, sid, await _provider(worker, sid))
        events = await env.wait_for(worker, sid, _ended(job_id))
        job = env.client.get_workflow_handle(job_workflow_id(sid, job_id))
        assert (await job.describe()).status is WorkflowExecutionStatus.COMPLETED
    assert [j.job_id for j in payloads(events, JobStart)] == [job_id]
    assert _end(events, job_id).stop_reason == "ok"
    assert len(payloads(events, SubagentEnd)) == 1


async def test_validation_errors_are_immediate(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    worker = await env.state()
    async with session_worker(env.client, worker, task_queue=env.queue), api(env) as http:
        sid = await env.create(worker)
        missing = await http.post(
            f"/v1/sessions/{sid}/subagents/background",
            json={"prompt": "x", "provider": "no-such-provider"},
            headers=HEADERS,
        )
        assert missing.status_code == 404, missing.text
        empty = await http.post(
            f"/v1/sessions/{sid}/subagents/background",
            json={"prompt": "   ", "provider": await _provider(worker, sid)},
            headers=HEADERS,
        )
        assert empty.status_code == 400, empty.text
    assert not payloads(await worker.reader.read(sid), JobStart), "nothing started"


async def test_a_job_survives_its_worker_being_killed(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    observer = await env.state()
    await env.composition(observer, "githi-v1")
    proc, _ = await spawn_worker(env, {"scripted_calls": [], "slow_subagent": 120})
    try:
        sid = await env.create(observer)
        async with api(env) as http:
            job_id = await _start(http, sid, await _provider(observer, sid))
        await env.wait_for(observer, sid, has_event(lambda p: isinstance(p, JobStart)))
    finally:
        proc.kill()  # SIGKILL while the job runs
        await proc.wait()
    rescuer = await env.state()
    async with session_worker(env.client, rescuer, task_queue=env.queue):
        events = await env.wait_for(rescuer, sid, _ended(job_id), timeout=60)
    assert _end(events, job_id).stop_reason == "ok", "retried on another worker"
    assert len([e for e in payloads(events, JobEnd) if e.job_id == job_id]) == 1


async def test_interrupt_cancels_a_running_job(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    worker = await env.state()
    with slow_subagent(6):
        async with session_worker(env.client, worker, task_queue=env.queue), api(env) as http:
            sid = await env.create(worker)
            job_id = await _start(http, sid, await _provider(worker, sid))
            resp = await http.post(
                f"/v1/sessions/{sid}/children/{job_id}/interrupt", headers=HEADERS
            )
            assert resp.status_code == 204, resp.text
            events = await env.wait_for(worker, sid, _ended(job_id))
            # The cancelled run's late result never adds a second end.
            await env.client.get_workflow_handle(job_workflow_id(sid, job_id)).result()
    assert _end(events, job_id).stop_reason == "interrupted"


async def test_ending_the_session_cancels_its_jobs(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    worker = await env.state()
    with slow_subagent(6):
        async with session_worker(env.client, worker, task_queue=env.queue), api(env) as http:
            sid = await env.create(worker)
            job_id = await _start(http, sid, await _provider(worker, sid))
            await env.sessions.terminate(sid)
            events = await env.wait_for(worker, sid, _ended(job_id))
    assert _end(events, job_id).stop_reason == "interrupted"


async def test_a_job_outlives_its_sessions_continue_as_new(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    worker = await env.state()
    with slow_subagent(8):
        async with session_worker(env.client, worker, task_queue=env.queue), api(env) as http:
            sid = await env.create(worker)
            job_id = await _start(http, sid, await _provider(worker, sid))
            handle = env.client.get_workflow_handle(workflow_id(sid))
            first_run = (await handle.describe()).run_id
            for i in range(6):  # enough history for the server to suggest continue-as-new
                await env.sessions.submit(sid, Message(f"c{i}", "hello"))
                await env.wait_for(worker, sid, run_ended(i + 1))
            assert (await handle.describe()).run_id != first_run, "the session continued as new"
            events = await env.wait_for(worker, sid, _ended(job_id), timeout=60)
            assert not (await env.sessions.status(sid)).running_command
    assert _end(events, job_id).stop_reason == "ok"


async def test_job_and_session_histories_replay_deterministically(env: Env) -> None:
    replayer = Replayer(
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        data_converter=pydantic_data_converter,
    )
    replayed = 0
    for kind in ("AgentSession", "SubagentJob"):
        async for history in replayable_histories(env.client, f"WorkflowType = '{kind}'"):
            result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
            assert result.replay_failure is None, (kind, result.replay_failure)
            replayed += 1
    assert replayed >= 1
