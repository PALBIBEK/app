"""A run's limits and retries come from the pinned harness (its ``loop:`` block)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from supervisor.agent.events import ErrorEvent, ModelRetry, TurnEnd
from supervisor.harness import LoopSpec
from supervisor.harness.storage.converters import loop_spec_from_dict
from supervisor.seams.core.types import ModelGatewayError
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import LS, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


def test_new_loop_settings_leave_published_digests_alone() -> None:
    # A manifest published before them (defaults) hashes exactly as it did.
    assert LoopSpec().body_for_digest() == {
        "max_steps": 30, "warn_steps_remaining": 3, "force_answer_on_final_step": True,
    }
    pinned = loop_spec_from_dict({"max_steps": 30, "max_run_seconds": 600, "model_retries": 5})
    assert pinned.body_for_digest()["max_run_seconds"] == 600.0
    assert pinned.body_for_digest()["model_retries"] == 5
    with pytest.raises(Exception, match="model_retries"):
        loop_spec_from_dict({"model_retries": 99})


async def test_a_run_past_its_pinned_time_limit_ends_saying_so(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, repeat=True)  # keeps asking: only the time limit stops it
    state = await env.state()
    with slow_tool("ls", 0.6):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state, "githi-loop")
            await env.sessions.submit(sid, Message("c1", "keep looking"))
            events = await env.wait_for(state, sid, run_ended())
            # The session stays usable.
            await env.sessions.submit(sid, Message("c2", "again"))
            await env.wait_for(state, sid, run_ended(2))

    (limit,) = [e for e in payloads(events, ErrorEvent) if e.error_class == "RunTimeLimit"]
    assert "time limit (1 s)" in limit.detail
    assert payloads(events, TurnEnd)[-1].stop_reason == "error"


async def test_model_retries_follow_the_pins(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model(errors_first=[ModelGatewayError(503, "overloaded"), ModelGatewayError(503, "overloaded")])
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-retry")
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended())

    retries = payloads(events, ModelRetry)
    assert [(r.attempt, r.max_attempts, r.delay_s) for r in retries] == [(1, 1, 0.1)]  # one, as pinned
    assert payloads(events, ErrorEvent)[-1].error_class == "ModelUnavailable"
