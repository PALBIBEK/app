"""A session's gateway key: a refused one is replaced, and the run goes on.

The gateway signs keys, so a restart keeps them (tests/gateway/test_signed_virtual_keys.py).
A key it still refuses (its signing secret changed) must not end every run until the
key nears expiry: the model step replaces it and asks again.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from supervisor.agent.events import ModelRetry, TurnEnd
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, payloads, run_ended
from tests.support.keys import FakeKeys
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_a_refused_key_is_replaced_and_the_step_asked_again(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    keys = FakeKeys(lifetime_s=3600)  # far from expiry: only the refusal renews it
    state = await env.state(virtual_key_client=keys)
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        gateway.token, gateway.refused_tokens = "token-1", {"token-1"}  # the gateway lost it
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended())

    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
    (retry,) = payloads(events, ModelRetry)
    assert retry.attempt == 1 and "AUTH_INVALID" in retry.detail
    assert gateway.token == "token-2" and len(keys.minted) == 2


async def test_a_key_that_stays_refused_ends_the_run_saying_so(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """Every new key refused too (the gateway is misconfigured): the step does not loop,
    it fails once the run's retries are spent."""
    gateway = model()
    keys = FakeKeys(lifetime_s=3600)
    state = await env.state(virtual_key_client=keys)
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        gateway.token = "token-1"
        gateway.refused_tokens = {f"token-{n}" for n in range(1, 20)}
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended())

    assert payloads(events, TurnEnd)[-1].stop_reason == "error"
    assert len(payloads(events, ModelRetry)) == 3  # the run's model retries, then it ends
