"""A message sent while no worker is up is kept and runs once one comes (pi-temporal's
Signal fallback): the submit update waits for a worker to take it; past a bound the
message goes as a signal, which the server keeps. Re-sent, it still runs once.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest

from supervisor.temporal.contracts import Message, workflow_id
from supervisor.temporal.sessions import SessionWorkflows
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import LS, Env, event_types, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


async def test_a_message_sent_with_no_worker_up_is_kept_and_runs_once_one_comes(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "first"))
        await env.wait_for(state, sid, run_ended())

    # Every worker is down now.
    sessions = SessionWorkflows(env.client, task_queue=env.queue, submit_accept_s=2.0)
    started = time.monotonic()
    accepted = await sessions.submit(sid, Message("c2", "sent while nobody is up"))
    assert accepted.held and accepted.queued
    assert time.monotonic() - started < 10, "the caller was left waiting for a worker"
    again = await sessions.submit(sid, Message("c2", "sent while nobody is up"))  # a client retry
    assert again.held
    handle = env.client.get_workflow_handle(workflow_id(sid))
    await handle.signal(AgentSessionWorkflow.submit_message_signal, Message("c3", ""))  # refused: dropped

    async with session_worker(env.client, state, task_queue=env.queue):
        await env.wait_for(state, sid, run_ended(2), timeout=60)
        await asyncio.sleep(2)  # anything that would run twice has had time to
        events = await state.reader.read(sid)
        assert (await env.sessions.status(sid)).queued == 0
    assert event_types(events).count("RunStart") == 2, "the held message ran once"
