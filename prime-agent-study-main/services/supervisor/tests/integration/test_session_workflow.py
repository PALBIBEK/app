"""Every session runs as a Temporal workflow: lifecycle against a real dev server.

The platform is the shipped one (catalog, manifests, journal, policy); only the
sandbox provider is mocked. Each test gets its own task queue and database.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from supervisor.agent.events import Envelope
from supervisor.api.events import iter_journal_sse
from supervisor.app import SupervisorState
from supervisor.config import TemporalSettings
from supervisor.db.engine import open_db
from supervisor.main import create_app
from supervisor.temporal.client import connect
from supervisor.temporal.contracts import CreateSession, Message, Phase, workflow_id
from supervisor.temporal.sessions import SessionCommandError, SessionWorkflows
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.platform import boot_platform

pytestmark = pytest.mark.asyncio(loop_scope="session")

KG = CreateSession(tenant_id="t1", harness_version="kg-v1")


@dataclass(frozen=True, slots=True)
class Platform:
    state: SupervisorState
    sessions: SessionWorkflows
    client: Client

    async def events(self, session_id: str) -> list[Envelope]:
        return await self.state.reader.read(session_id)

    async def wait_for(
        self, session_id: str, done: Callable[[list[Envelope]], bool], timeout: float = 20
    ) -> list[Envelope]:
        async with asyncio.timeout(timeout):
            while not done(events := await self.events(session_id)):
                await asyncio.sleep(0.1)
        return events


def _types(events: list[Envelope]) -> list[str]:
    return [type(e.payload).__name__ for e in events]


def _count(name: str, n: int) -> Callable[[list[Envelope]], bool]:
    return lambda events: _types(events).count(name) >= n


@pytest_asyncio.fixture(loop_scope="session")
async def platform(temporal_env: WorkflowEnvironment, tmp_path: Path) -> AsyncIterator[Platform]:
    queue = f"test-{uuid.uuid4().hex[:8]}"
    client = await connect(
        TemporalSettings(
            address=temporal_env.client.service_client.config.target_host,
            namespace=temporal_env.client.namespace,
            task_queue=queue,
        )
    )
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}") as (_, factory):
        state, _ = await boot_platform(factory, tmp_path / "blobs")
        state.workflows = SessionWorkflows(client, task_queue=queue)
        async with session_worker(client, state, task_queue=queue):
            yield Platform(state, state.workflows, client)


async def test_session_lifecycle_runs_through_its_workflow(platform: Platform) -> None:
    sid = uuid.uuid4().hex
    created = await platform.sessions.create(sid, KG)
    assert created.phase is Phase.IDLE
    assert await platform.state.manager.session_status(sid) == "active"

    accepted = await platform.sessions.submit(sid, Message("c1", "hello"))
    assert not accepted.queued
    await platform.wait_for(sid, _count("RunEnd", 1))
    # Stateless harness: the runtime stops after each turn, so turn 2 is a cold resume.
    await platform.sessions.submit(sid, Message("c2", "again"))
    await platform.wait_for(sid, _count("RunEnd", 2))

    await platform.sessions.terminate(sid)
    assert (await platform.sessions.status(sid)).phase is Phase.TERMINATED
    assert await platform.state.manager.session_status(sid) == "terminated"
    handle = platform.client.get_workflow_handle_for(AgentSessionWorkflow.run, workflow_id(sid))
    result = await handle.result()
    assert result.turns_completed == 2


async def test_create_is_idempotent_and_reports_domain_errors(platform: Platform) -> None:
    sid = uuid.uuid4().hex
    await platform.sessions.create(sid, KG)
    await platform.sessions.create(sid, KG)  # same id again: attaches, does not duplicate
    assert len(await platform.state.manager.list_sessions(tenant_id="t1")) == 1

    with pytest.raises(SessionCommandError) as err:
        await platform.sessions.create(
            uuid.uuid4().hex, CreateSession(tenant_id="t1", harness_version="no-such-harness")
        )
    assert err.value.kind == "UnknownHarness"


async def test_duplicate_messages_run_once(platform: Platform) -> None:
    sid = uuid.uuid4().hex
    await platform.sessions.create(sid, KG)
    first = await platform.sessions.submit(sid, Message("same", "hello"))
    again = await platform.sessions.submit(sid, Message("same", "hello"))
    assert first.command_id == again.command_id
    await platform.wait_for(sid, _count("RunEnd", 1))
    await asyncio.sleep(1)
    assert _types(await platform.events(sid)).count("RunStart") == 1


async def test_messages_after_terminate_are_rejected(platform: Platform) -> None:
    sid = uuid.uuid4().hex
    await platform.sessions.create(sid, KG)
    await platform.sessions.terminate(sid)
    with pytest.raises(SessionCommandError) as err:
        await platform.sessions.submit(sid, Message("late", "hello"))
    assert err.value.kind in {"SessionTerminated", "SessionClosed"}
    await platform.sessions.terminate(sid)  # terminating twice is fine


# --- the HTTP API drives sessions through their workflows ---------------------


def _api(platform: Platform) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app(state=platform.state)),
        base_url="http://supervisor",
        headers={"X-Tenant-Id": "t1"},
    )


async def test_api_session_runs_through_workflow_and_streams_from_journal(
    platform: Platform,
) -> None:
    async with _api(platform) as api:
        assert (await api.get("/readyz")).json() == {"status": "ready"}
        created = await api.post("/v1/sessions", json={"harness_version": "kg-v1"})
        assert created.status_code == 201, created.text
        sid = created.json()["session_id"]
        assert created.json()["runtime"] == "echo"

        sent = await _send(api, sid, "c1")
        assert sent.status_code == 202, sent.text
        again = await _send(api, sid, "c1")
        assert again.json()["turn_id"] == sent.json()["turn_id"]

        await platform.wait_for(sid, _count("RunEnd", 1))
        body = iter_journal_sse(session_id=sid, cursor=-1, reader=platform.state.reader)
        streamed = [chunk async for chunk in _until(body, "run_end")]
        assert any('"type":"run_start"' in c for c in streamed)

        assert (await api.post(f"/v1/sessions/{sid}/abort")).status_code == 200
        assert (await api.delete(f"/v1/sessions/{sid}")).status_code == 204
        late = await _send(api, sid, "c2")
        assert late.status_code == 409


async def test_api_reports_domain_errors_with_their_old_statuses(platform: Platform) -> None:
    async with _api(platform) as api:
        unknown = await api.post("/v1/sessions", json={"harness_version": "nope"})
        assert unknown.status_code == 422, unknown.text
        mismatch = await api.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "composition_id": "githi-v1"},
        )
        assert mismatch.status_code == 422, mismatch.text


async def test_temporal_down_means_not_ready_and_503(platform: Platform) -> None:
    down = await connect(TemporalSettings(address="127.0.0.1:1"), lazy=True)
    platform.state.workflows = SessionWorkflows(down, task_queue="unreachable")
    async with _api(platform) as api:
        ready = await api.get("/readyz")
        assert (ready.status_code, ready.json()["reason"]) == (503, "temporal")
        created = await api.post("/v1/sessions", json={"harness_version": "kg-v1"})
        assert created.status_code == 503
        assert created.json()["detail"]["reason"] == "temporal_unavailable"


async def _send(api: AsyncClient, session_id: str, command_id: str) -> Response:
    payload = {"command_id": command_id, "text": "hi"}
    return await api.post(f"/v1/sessions/{session_id}/messages", json=payload)


async def _until(body: AsyncIterator[str], wire_type: str) -> AsyncIterator[str]:
    async with asyncio.timeout(10):
        async for chunk in body:
            yield chunk
            if f'"type":"{wire_type}"' in chunk:
                return
