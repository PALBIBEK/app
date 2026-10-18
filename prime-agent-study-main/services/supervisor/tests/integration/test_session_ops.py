"""Session operations and forks through the session's workflow, against a real Temporal server.

The API process (its own SupervisorState) never holds a session: tool invoke,
memory, subagent and workspace operations run on the worker holding it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import MessageEnd, ToolEnd
from supervisor.app import SupervisorState
from supervisor.main import create_app
from supervisor.temporal.contracts import Activity, CreateSession, Message, Op, SessionOp
from supervisor.temporal.sessions import SessionCommandError
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import (
    FAST,
    LS,
    Env,
    event_types,
    has_event,
    payloads,
    run_ended,
    spawn_worker,
)
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")

HEADERS = {"X-Tenant-Id": "t1"}


@asynccontextmanager
async def api(env: Env) -> AsyncIterator[tuple[AsyncClient, SupervisorState]]:
    """The HTTP API as deployed: its own process state, sessions only via workflows."""
    state = await env.state()
    state.workflows = env.sessions
    transport = ASGITransport(app=create_app(state=state))
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, state


def _invoke(sid: str, op_id: str, name: str = "ls", **args: object) -> SessionOp:
    return SessionOp(sid, op_id, Op.INVOKE_TOOL, "t1", {"name": name, "arguments": args})


async def test_tool_invoke_runs_on_the_session_worker_not_the_api(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    worker = await env.state()
    async with session_worker(env.client, worker, task_queue=env.queue), api(env) as (http, front):
        sid = await env.create(worker)
        resp = await http.post(
            f"/v1/sessions/{sid}/tools/invoke",
            json={"name": "ls", "arguments": {"path": "."}, "tool_call_id": "call-1"},
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["tool_call_id"] == "call-1"
        assert resp.json()["ok"] is True
        # Re-sending the same call returns its recorded outcome; the tool ran once.
        again = await http.post(
            f"/v1/sessions/{sid}/tools/invoke",
            json={"name": "ls", "arguments": {"path": "."}, "tool_call_id": "call-1"},
            headers=HEADERS,
        )
        assert again.json() == resp.json()
        events = await worker.reader.read(sid)
        assert event_types(events).count("ToolStart") == 1
        assert worker.manager._registry.get(sid) is not None, "live on the worker"
        assert front.manager._registry.get(sid) is None, "never resumed in the API process"
    assert Activity.SESSION_OP in await env.activity_types(sid)


async def test_domain_errors_keep_their_http_status(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    worker = await env.state()
    async with session_worker(env.client, worker, task_queue=env.queue), api(env) as (http, _):
        bare = uuid.uuid4().hex  # a kg session: no composition, so no tool provider
        await env.sessions.create(bare, CreateSession("t1", "kg-v1"), timing=FAST)
        resp = await http.post(
            f"/v1/sessions/{bare}/tools/invoke",
            json={"name": "ls", "arguments": {}},
            headers=HEADERS,
        )
        assert resp.status_code == 409, resp.text
        sid = await env.create(worker)
        too_deep = await http.post(
            f"/v1/sessions/{sid}/subagents/spawn",
            json={"prompt": "x", "parent_depth": 99, "depth_limit": 1},
            headers=HEADERS,
        )
        assert too_deep.status_code in (400, 409), too_deep.text
        await env.sessions.terminate(sid)
        closed = await http.post(
            f"/v1/sessions/{sid}/tools/invoke",
            json={"name": "ls", "arguments": {}},
            headers=HEADERS,
        )
        assert closed.status_code in (404, 409), closed.text


async def test_an_operation_adopts_a_session_whose_worker_is_gone(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    first = await env.state()
    async with session_worker(env.client, first, task_queue=env.queue):
        sid = await env.create(first)
    second = await env.state()  # the session's worker has stopped; another takes over
    async with session_worker(env.client, second, task_queue=env.queue):
        body = await env.sessions.op(_invoke(sid, "after-loss", path="."))
    assert body is not None and body["ok"]
    assert second.manager._registry.get(sid) is not None


async def test_worker_killed_mid_invoke_reports_unknown_and_never_reruns(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    observer = await env.state()
    await env.composition(observer, "githi-v1")
    proc, _ = await spawn_worker(env, {"scripted_calls": [list(LS)], "slow_tool": ["ls", 120]})
    try:
        sid = await env.create(observer)
        task = asyncio.ensure_future(env.sessions.op(_invoke(sid, "slow-1", path=".")))
        await env.wait_for(observer, sid, has_event(lambda p: type(p).__name__ == "ToolStart"))
    finally:
        proc.kill()  # SIGKILL mid-tool
        await proc.wait()

    adopter = await env.state()
    async with session_worker(env.client, adopter, task_queue=env.queue):
        with pytest.raises(SessionCommandError) as err:
            await task
        assert err.value.kind == "ToolOutcomeUnknown"
        # Re-sending the call answers with the recorded outcome instead of running it.
        body = await env.sessions.op(_invoke(sid, "slow-1", path="."))
        assert body is not None and body["code"] == "TOOL_OUTCOME_UNKNOWN"
        # The session is still usable.
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(adopter, sid, run_ended())
    assert event_types(events).count("ToolStart") == 1
    (end,) = [e for e in payloads(events, ToolEnd) if e.tool_call_id == "slow-1"]
    assert end.result["code"] == "TOOL_OUTCOME_UNKNOWN"


async def test_a_fork_is_a_session_with_its_own_workflow(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    worker = await env.state()
    async with session_worker(env.client, worker, task_queue=env.queue), api(env) as (http, _):
        source = await env.create(worker)
        await env.sessions.submit(source, Message("c1", "remember the word kiwi"))
        await env.wait_for(worker, source, run_ended())
        resp = await http.post(f"/v1/sessions/{source}/fork", json={}, headers=HEADERS)
        assert resp.status_code == 201, resp.text
        fork = resp.json()["session_id"]
        await env.sessions.submit(fork, Message("c2", "what was the word?"))
        events = await env.wait_for(worker, fork, run_ended(2))
    # The fork carries the source's conversation, and its own turn went on top of it.
    texts = [m.content for m in gateway.seen[-1] if m.role == "user"]
    assert texts == ["remember the word kiwi", "what was the word?"]
    assert len(payloads(events, MessageEnd)) == 4
    assert (await env.sessions.status(fork)).turns_completed == 1


async def test_fork_copy_is_safe_to_retry(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model()
    worker = await env.state()
    async with session_worker(env.client, worker, task_queue=env.queue):
        source = await env.create(worker)
        await env.sessions.submit(source, Message("c1", "hi"))
        await env.wait_for(worker, source, run_ended())
    new_id = uuid.uuid4().hex
    for _ in range(2):  # an activity retry after a crash mid-copy
        await worker.manager.fork(source, new_id=new_id)
    copied = await worker.reader.read(new_id)
    assert event_types(copied) == event_types(await worker.reader.read(source))


async def test_session_op_histories_replay_deterministically(env: Env) -> None:
    replayer = Replayer(workflows=[AgentSessionWorkflow], data_converter=pydantic_data_converter)
    histories = [
        await env.client.get_workflow_handle(run.id, run_id=run.run_id).fetch_history()
        async for run in env.client.list_workflows("WorkflowType = 'AgentSession'")
    ]
    for history in histories:
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        assert result.replay_failure is None, result.replay_failure
