"""Starting a session without holding the request open (real Temporal, scripted model).

The API starts the session's workflow and answers at once; the Temporal server then says how
the start goes (``starting`` → ``ready`` or ``failed`` with the reason), including when no
worker is running at all — the case that used to leave "Creating session…" hanging.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest

from supervisor.temporal.contracts import CreateSession, Message, workflow_id
from supervisor.temporal.sessions import SessionWorkflows, Startup
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _until(sessions: SessionWorkflows, sid: str, *wanted: str, timeout: float = 30) -> Startup:
    async with asyncio.timeout(timeout):
        while True:
            found = await sessions.startup(sid)
            if found is not None and found[0].status in wanted:
                return found[0]
            await asyncio.sleep(0.2)


async def test_an_async_start_reports_starting_then_ready_and_takes_messages(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    state = await env.state()
    name, digest = await env.composition(state, "githi-v1")
    sid = uuid.uuid4().hex
    spec = CreateSession("t1", "githi-v1", composition_id=name, composition_digest=digest, principal_id="alice")
    async with session_worker(env.client, state, task_queue=env.queue):
        async with asyncio.timeout(15):  # the worker's first polls reach the server
            while not await env.sessions.worker_available():
                await asyncio.sleep(0.2)
        await env.sessions.start(sid, spec)  # returns at once
        ready = await _until(env.sessions, sid, "ready", "failed")
        assert ready.status == "ready" and ready.worker_available, ready
        found = await env.sessions.startup(sid)
        assert found is not None and found[1] == {"tenant_id": "t1", "principal_id": "alice"}
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(state, sid, run_ended())


async def test_a_start_that_cannot_succeed_reports_why(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model()
    state = await env.state()
    sid = uuid.uuid4().hex
    bad = CreateSession(
        "t1", "githi-v1", composition_id="no-such-composition", composition_digest="sha256:" + "0" * 64,
        principal_id="alice",
    )
    async with session_worker(env.client, state, task_queue=env.queue):
        await env.sessions.start(sid, bad)
        failed = await _until(env.sessions, sid, "failed", "ready")
    assert failed.status == "failed" and failed.reason, failed
    assert failed.kind  # the domain error's class, e.g. NotOffered
    again = await env.sessions.startup(sid)  # read from the closed workflow: no worker needed
    assert again is not None and again[0].status == "failed" and again[0].reason == failed.reason


async def test_without_a_worker_the_start_waits_and_says_so(env: Env) -> None:
    sessions = SessionWorkflows(env.client, task_queue=f"nobody-{uuid.uuid4().hex[:8]}")
    assert not await sessions.worker_available()
    sid = uuid.uuid4().hex
    await sessions.start(sid, CreateSession("t1", "githi-v1", principal_id="alice"))
    found = await sessions.startup(sid)
    assert found is not None
    startup, _ = found
    assert (startup.status, startup.worker_available) == ("starting", False)
    await env.client.get_workflow_handle(workflow_id(sid)).terminate("test over")


async def test_a_message_sent_while_starting_waits_and_then_runs(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    state = await env.state()
    name, digest = await env.composition(state, "githi-v1")
    sid = uuid.uuid4().hex
    spec = CreateSession("t1", "githi-v1", composition_id=name, composition_digest=digest, principal_id="alice")
    await env.sessions.start(sid, spec)  # no worker yet: the session cannot be set up
    sending = asyncio.create_task(env.sessions.submit(sid, Message("c1", "sent early")))
    await asyncio.sleep(0.5)
    assert not sending.done()
    async with session_worker(env.client, state, task_queue=env.queue):
        accepted = await asyncio.wait_for(sending, 30)
        assert accepted.command_id == "c1"
        await env.wait_for(state, sid, run_ended())
