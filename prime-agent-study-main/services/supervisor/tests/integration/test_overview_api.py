"""Views across sessions over HTTP: the approvals inbox, sandboxes and usage (real Temporal, scripted model).

Each shows only the caller's sessions: another user of the tenant, and another
tenant, see nothing of them.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import ApprovalRequired
from supervisor.main import create_app
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import WRITE, Env, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _api(state, *, tenant: str = "t1", principal: str = "alice") -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app(state=state)),
        base_url="http://supervisor",
        headers={"X-Tenant-Id": tenant, "X-Principal-Id": principal},
    )


async def test_the_inbox_lists_waiting_approvals_for_their_owner_only(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    state.workflows = env.sessions
    async with (
        session_worker(env.client, state, task_queue=env.queue),
        _api(state) as alice,
        _api(state, principal="bob") as bob,
        _api(state, tenant="t2") as other,
    ):
        sid = await env.create(state, "githi-approve-allow")
        r = await alice.post(f"/v1/sessions/{sid}/messages", json={"command_id": "c1", "text": "write a note"})
        assert r.status_code == 202, r.text
        await env.wait_for(state, sid, lambda evs: any(isinstance(e.payload, ApprovalRequired) for e in evs))

        inbox = (await alice.get("/v1/approvals")).json()
        assert [i["session_id"] for i in inbox] == [sid]
        item = inbox[0]
        assert item["options"] and item["prompt"] and item["agent"]
        assert item["expires_at_ms"] > item["created_at_ms"]
        assert (await bob.get("/v1/approvals")).json() == []  # another user's session
        assert (await other.get("/v1/approvals")).json() == []  # another tenant

        answered = await alice.post(f"/v1/approvals/{item['approval_id']}", json={"choice": "allow"})
        assert answered.status_code == 200, answered.text
        await env.wait_for(state, sid, run_ended())
        assert (await alice.get("/v1/approvals")).json() == []  # answered: gone from the inbox


async def test_sandboxes_and_usage_come_from_the_journal(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model(WRITE)
    state = await env.state()
    state.workflows = env.sessions
    async with (
        session_worker(env.client, state, task_queue=env.queue),
        _api(state) as alice,
        _api(state, tenant="t2") as other,
    ):
        sid = await env.create(state)
        r = await alice.post(f"/v1/sessions/{sid}/messages", json={"command_id": "c1", "text": "write a note"})
        assert r.status_code == 202, r.text
        await env.wait_for(state, sid, run_ended())

        boxes = (await alice.get("/v1/sandboxes", params={"session_id": sid})).json()
        default = next(b for b in boxes if b["hand"] == "default")
        assert default["state"] == "running" and default["provider"] and default["sandbox_id"]
        assert default["cause"] and default["since_ms"]
        assert any(b["session_id"] == sid for b in (await alice.get("/v1/sandboxes")).json())  # all open sessions
        assert (await other.get("/v1/sandboxes", params={"session_id": sid})).status_code == 404
        assert (await other.get("/v1/sandboxes")).json() == []

        by_day = (await alice.get("/v1/usage", params={"by": "day"})).json()
        assert by_day["total"]["model_calls"] >= 2 and by_day["total"]["total_tokens"] > 0  # a tool call, then the answer
        assert by_day["total"]["sessions"] == 1 and len(by_day["rows"]) == 1
        by_agent = (await alice.get("/v1/usage", params={"by": "agent"})).json()
        assert by_agent["rows"][0]["key"] and by_agent["rows"][0]["cost"] == by_day["total"]["cost"]
        assert (await other.get("/v1/usage")).json()["total"]["model_calls"] == 0
        too_long = await alice.get("/v1/usage", params={"since_ms": 0, "until_ms": 200 * 86_400_000})
        assert too_long.status_code == 422
