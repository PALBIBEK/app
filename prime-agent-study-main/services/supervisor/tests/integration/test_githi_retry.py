"""pi's auto-retry, made durable: a failed model call that may pass is asked again
after a wait on a workflow timer; one that will fail the same way ends the run at once.

Against a real Temporal server, with a scripted gateway raising the provider's errors.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from supervisor.agent.events import ErrorEvent, ModelRetry, TurnEnd
from supervisor.agent.runtimes.githi import classify
from supervisor.agent.runtimes.githi.classify import RetrySpec, is_retryable, retry_delay
from supervisor.seams.core.types import ModelGatewayError
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classify, "DEFAULT_RETRY", RetrySpec(max_retries=3, base_delay_s=0.2, max_delay_s=5))


def test_which_model_errors_are_worth_retrying() -> None:
    assert is_retryable(ModelGatewayError(429, "rate limited"))
    assert is_retryable(ModelGatewayError(503, "overloaded"))
    assert is_retryable(ConnectionError("reset by peer"))
    assert is_retryable(RuntimeError("model gateway error 502: bad gateway"))
    assert not is_retryable(ModelGatewayError(401, "invalid api key"))
    assert not is_retryable(ModelGatewayError(400, "This model's maximum context length is 8192 tokens"))
    assert not is_retryable(ValueError("unknown route"))
    from supervisor.models.transports import ModelCallError

    assert is_retryable(ModelCallError("503 overloaded", status=503))
    assert not is_retryable(ModelCallError("429 budget spent", status=429, error_type="VIRTUAL_KEY_BUDGET_EXCEEDED"))
    spec = RetrySpec(max_retries=3, base_delay_s=2, max_delay_s=60)
    assert [retry_delay(spec, n, ConnectionError()) for n in (1, 2, 3, 4)] == [2, 4, 8, None]
    assert retry_delay(spec, 1, ModelGatewayError(429, "", retry_after=7)) == 7
    assert retry_delay(spec, 1, ModelGatewayError(429, "", retry_after=600)) is None  # too long to wait


async def test_a_rate_limited_call_waits_and_is_asked_again(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(errors_first=[ModelGatewayError(429, "slow down", retry_after=1.0), ConnectionError("reset")])
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        started = time.monotonic()
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended())
        took = time.monotonic() - started

    retries = payloads(events, ModelRetry)
    assert [(r.attempt, r.delay_s) for r in retries] == [(1, 1.0), (2, 0.4)]  # Retry-After honoured
    assert retries[0].error_class == "ModelGatewayError" and "slow down" in retries[0].detail
    assert took >= 1.4  # the waits happened
    assert not payloads(events, ErrorEvent)
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
    assert not gateway.errors_first


async def test_a_refused_call_is_not_retried(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    gateway = model(errors_first=[ModelGatewayError(401, "invalid api key")])
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(state, sid, run_ended())
        # The session stays usable.
        await env.sessions.submit(sid, Message("c2", "again"))
        events = await env.wait_for(state, sid, run_ended(2))

    assert not payloads(events, ModelRetry)
    errors = payloads(events, ErrorEvent)
    assert len(errors) == 1 and "invalid api key" in errors[0].detail
    assert len(gateway.seen) == 2  # asked once per run
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"


async def test_a_provider_that_keeps_failing_ends_the_run_saying_so(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(errors_first=[ModelGatewayError(503, "overloaded") for _ in range(4)])
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended(), timeout=30)

    assert [r.attempt for r in payloads(events, ModelRetry)] == [1, 2, 3]
    errors = payloads(events, ErrorEvent)
    assert errors and errors[-1].error_class == "ModelUnavailable"
    assert "3 retries" in errors[-1].detail and "overloaded" in errors[-1].detail


async def test_an_abort_ends_the_wait(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model(errors_first=[ModelGatewayError(429, "slow down", retry_after=4.0)])
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ModelRetry)))
        started = time.monotonic()
        await env.sessions.abort(sid)
        events = await env.wait_for(state, sid, run_ended(), timeout=10)

    assert time.monotonic() - started < 3.5
    assert payloads(events, TurnEnd)[-1].stop_reason == "aborted"
