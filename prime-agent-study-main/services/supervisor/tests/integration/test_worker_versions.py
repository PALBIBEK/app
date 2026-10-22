"""Safe deploys on real Temporal: versioned workers, ramp and promote, and the live replay check."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.common import WorkerDeploymentVersion

from supervisor.main import create_app
from supervisor.temporal import rollout
from supervisor.temporal.contracts import Message, workflow_id
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _build_of(env: Env, sid: str, *, timeout: float = 15) -> str | None:
    """The worker version the session's workflow runs on now. A run that just continued as new
    shows none until its first workflow task completes, so this waits for one to appear."""
    async with asyncio.timeout(timeout):
        while True:
            desc = await env.client.get_workflow_handle(workflow_id(sid)).describe()
            info = desc.raw_description.workflow_execution_info.versioning_info
            structured = info.deployment_version if info.HasField("deployment_version") else None
            if (build := rollout._build(info.version, structured)) is not None:
                return build
            await asyncio.sleep(0.2)


async def test_a_new_build_takes_over_sessions_while_the_old_one_drains(
    env: Env, model: Callable[..., ScriptedGateway], monkeypatch: pytest.MonkeyPatch
) -> None:
    model()
    state = await env.state()
    dep = f"yal-{uuid.uuid4().hex[:6]}"
    old, new = WorkerDeploymentVersion(dep, "b1"), WorkerDeploymentVersion(dep, "b2")
    async with session_worker(env.client, state, task_queue=env.queue, deployment=old, identity="w-b1"):
        # The first version of a deployment promotes itself, even with a manual rollout:
        # otherwise new sessions would have no version to run on.
        assert await rollout.bootstrap(env.client, dep, "b1", mode="manual", timeout_s=30) == "promoted"
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(state, sid, run_ended())
        assert await _build_of(env, sid) == "b1"

        async with session_worker(env.client, state, task_queue=env.queue, deployment=new, identity="w-b2"):
            assert await rollout.bootstrap(env.client, dep, "b2", mode="manual", timeout_s=30) == "left"
            found = await rollout.status(env.client, dep)
            assert found is not None and found.current_build == "b1" and found.version("b2") is not None

            await rollout.ramp(env.client, dep, "b2", 25)
            ramping = await rollout.status(env.client, dep)
            assert ramping is not None and (ramping.ramping_build, ramping.ramping_percentage) == ("b2", 25.0)

            await rollout.promote(env.client, dep, "b2")
            promoted = await rollout.status(env.client, dep)
            assert promoted is not None and promoted.current_build == "b2" and promoted.ramping_build is None
            assert promoted.version("b1").status in ("draining", "drained", "inactive")  # type: ignore[union-attr]

            # The running session moves to the new build at its next workflow task (AUTO_UPGRADE).
            await env.sessions.submit(sid, Message("c2", "after the deploy"))
            await env.wait_for(state, sid, run_ended(2), timeout=60)
            assert await _build_of(env, sid) == "b2"

            monkeypatch.setenv("WORKER_DEPLOYMENT_NAME", dep)
            state.workflows = env.sessions
            async with AsyncClient(
                transport=ASGITransport(app=create_app(state=state)), base_url="http://supervisor",
                headers={"X-Tenant-Id": "t1", "X-Principal-Id": "alice"},
            ) as api:
                shown = (await api.get("/v1/ops/rollout")).json()
    assert shown["versioned"] is True and shown["current_build"] == "b2"
    assert {v["build_id"] for v in shown["versions"]} == {"b1", "b2"}


async def test_the_live_replay_check_passes_on_this_code(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(state, sid, run_ended())
    report = await rollout.check(env.client, limit=15)
    assert report.checked >= 1 and report.ok, report.failures


async def test_turning_versioning_on_keeps_sessions_started_before_it(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):  # unversioned, as before
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "before versioning"))
        await env.wait_for(state, sid, run_ended())
    dep = f"yal-{uuid.uuid4().hex[:6]}"
    async with session_worker(
        env.client, state, task_queue=env.queue, deployment=WorkerDeploymentVersion(dep, "b1")
    ):
        assert await rollout.bootstrap(env.client, dep, "b1", timeout_s=30) == "promoted"
        await env.sessions.submit(sid, Message("c2", "after versioning"))
        await env.wait_for(state, sid, run_ended(2), timeout=60)
        assert await _build_of(env, sid) == "b1"  # the old session moved onto the versioned workers
