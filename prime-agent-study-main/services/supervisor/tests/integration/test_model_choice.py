"""A session runs the model its owner chose: at creation, or with a message from that run on.

A run keeps the model it began with (``RunStart.model``), every step names it, and the
session's gateway key is replaced by one that covers it; a model that is not offered now is
refused before anything changes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest

from supervisor.agent.events import ErrorEvent, RunStart
from supervisor.app import SupervisorState
from supervisor.db.models import SessionRow
from supervisor.models import reset_model_router, use_model_router
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, event_types, payloads, run_ended
from tests.support.keys import FakeKeys
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")

OFFERED = ("gateway/deepseek-flash", "gateway/local/qwen3:8b")


@pytest.fixture(autouse=True)
def offered() -> Iterator[None]:
    """A gateway that offers two models; each is served by the group its id names."""
    use_model_router(
        SimpleNamespace(  # type: ignore[arg-type]
            selectable_models=lambda: [SimpleNamespace(id=m) for m in OFFERED],
            target_for=lambda m: m.removeprefix("gateway/"),
        )
    )
    yield
    reset_model_router()


def _agent_models(gateway: ScriptedGateway) -> list[str | None]:
    return list(dict.fromkeys(gateway.models))


async def _chosen(state: SupervisorState, sid: str) -> str | None:
    async with state.manager._db() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        return row.model


async def test_a_session_created_with_a_model_runs_it_on_a_key_for_it(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    keys = FakeKeys(lifetime_s=3600)
    state = await env.state(virtual_key_client=keys)
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, model="gateway/deepseek-flash")
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended())

    assert _agent_models(gateway) == ["gateway/deepseek-flash"]
    assert payloads(events, RunStart)[0].model == "gateway/deepseek-flash"
    assert keys.minted[-1]["allowed_models"] == ["deepseek-flash"]


async def test_a_message_switches_the_model_from_its_run_on(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    keys = FakeKeys(lifetime_s=3600)
    state = await env.state(virtual_key_client=keys)
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "first"))
        await env.wait_for(state, sid, run_ended(1))
        pinned = gateway.models[-1]
        await env.sessions.submit(sid, Message("c2", "second", model="gateway/local/qwen3:8b"))
        events = await env.wait_for(state, sid, run_ended(2))

    assert pinned != "gateway/local/qwen3:8b"
    assert [r.model for r in payloads(events, RunStart)] == [pinned, "gateway/local/qwen3:8b"]
    assert gateway.models[-1] == "gateway/local/qwen3:8b"
    assert keys.minted[-1]["allowed_models"] == ["local/qwen3:8b"]  # a new key that covers it
    assert gateway.token == f"token-{len(keys.minted)}"  # and the gateway client carries it
    assert await _chosen(state, sid) == "gateway/local/qwen3:8b"


async def test_a_model_not_offered_is_refused_and_nothing_changes(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """The API refuses it first (422); a model withdrawn between that check and the run is
    refused by the run, which then calls nothing."""
    gateway = model()
    state = await env.state(virtual_key_client=FakeKeys(lifetime_s=3600))
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello", model="gateway/not-offered"))
        events = await env.wait_for(state, sid, lambda events: "ErrorEvent" in event_types(events))

    (refused,) = payloads(events, ErrorEvent)
    assert refused.error_class == "ModelNotOffered" and "gateway/not-offered" in refused.detail
    assert "RunStart" not in event_types(events) and gateway.models == []
    assert await _chosen(state, sid) is None
