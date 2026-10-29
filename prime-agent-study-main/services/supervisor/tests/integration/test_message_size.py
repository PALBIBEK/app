"""A message too big for the session's workflow history is refused, and the session lives.

Before this, a 2.5 MB message was accepted as an update the server could not store, and the
server terminated the session's workflow: every later message was refused for good.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from temporalio.client import WorkflowUpdateFailedError

from supervisor.temporal.contracts import (
    MAX_COMMAND_BYTES,
    MAX_MESSAGE_CHARS,
    Message,
    Op,
    SessionOp,
    workflow_id,
)
from supervisor.temporal.sessions import SessionCommandError
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.api import supervisor_api
from tests.support.githi_env import Env, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_a_message_too_big_is_refused_and_the_session_goes_on(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    state = await env.state()
    state.workflows = env.sessions
    async with session_worker(env.client, state, task_queue=env.queue), supervisor_api(state) as api:
        sid = await env.create(state)

        with pytest.raises(SessionCommandError) as too_long:
            await env.sessions.submit(sid, Message("big", "x" * 2_500_000))
        assert too_long.value.kind == "MessageTooLong"
        assert too_long.value.details == {"chars": 2_500_000, "limit": MAX_MESSAGE_CHARS}

        # Over the limit by one character, through the HTTP API: 413 with the reason.
        r = await api.post(
            f"/v1/sessions/{sid}/messages", json={"command_id": "c-over", "text": "x" * (MAX_MESSAGE_CHARS + 1)}
        )
        assert r.status_code == 413, r.text
        assert "Attach long content as a file" in r.text

        # A client that skips the supervisor's check meets the workflow's, before the
        # update is accepted, so nothing too big ever reaches the session's history.
        handle = env.client.get_workflow_handle(workflow_id(sid))
        with pytest.raises(WorkflowUpdateFailedError) as refused:
            await handle.execute_update(
                AgentSessionWorkflow.submit_message, Message("raw", "x" * (MAX_MESSAGE_CHARS + 1)), id="raw"
            )
        assert getattr(refused.value.cause, "type", None) == "MessageTooLong"

        # An operation that big is refused too.
        with pytest.raises(SessionCommandError) as too_large:
            await env.sessions.op(
                SessionOp(sid, "op-big", Op.INVOKE_TOOL, "t1", {"name": "write", "arguments": {"content": "y" * 400_000}})
            )
        assert too_large.value.kind == "CommandTooLarge" and too_large.value.details["limit"] == MAX_COMMAND_BYTES

        # The session is alive and answers; a message at the limit is accepted.
        ok = await env.sessions.submit(sid, Message("ok", "x" * MAX_MESSAGE_CHARS))
        assert ok.command_id == "ok"
        await env.wait_for(state, sid, run_ended())
    assert (await handle.describe()).status.name == "RUNNING"
