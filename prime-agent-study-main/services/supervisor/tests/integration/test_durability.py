"""Durable-execution scenarios, each against a real Temporal dev server.

The dev server suggests continue-as-new after a short history (see
tests/support/temporal_env.py), so these also run across continue-as-new.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from supervisor.agent.events import Envelope, ErrorEvent, TurnEnd
from supervisor.agent.runtimes.echo.runtime import EchoRuntime
from supervisor.app import SupervisorState
from supervisor.config import TemporalSettings
from supervisor.db.engine import open_db
from supervisor.temporal.client import connect
from supervisor.temporal.contracts import CreateSession, Message, Phase, Timing, workflow_id
from supervisor.temporal.sessions import SessionWorkflows
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.platform import boot_platform
from tests.support.waits import settled

pytestmark = pytest.mark.asyncio(loop_scope="session")

KG = CreateSession(tenant_id="t1", harness_version="kg-v1")
# Notice a dead worker in seconds rather than the production minute.
FAST = Timing(placement_timeout_s=2.0, heartbeat_timeout_s=2.0, turn_timeout_s=120.0)

Done = Callable[[list[Envelope]], bool]


def _types(events: list[Envelope]) -> list[str]:
    return [type(e.payload).__name__ for e in events]


def _count(name: str, n: int = 1) -> Done:
    return lambda events: _types(events).count(name) >= n


def _error(error_class: str) -> Done:
    return lambda events: any(
        isinstance(e.payload, ErrorEvent) and e.payload.error_class == error_class
        for e in events
    )


@dataclass(frozen=True, slots=True)
class Cluster:
    """Shared database and Temporal; workers come and go."""

    address: str
    namespace: str
    queue: str
    db_url: str
    blob_dir: Path
    client: Client
    sessions: SessionWorkflows
    factory: object

    async def state(self) -> SupervisorState:
        state, _ = await boot_platform(self.factory, self.blob_dir)  # type: ignore[arg-type]
        return state

    async def wait_for(self, state: SupervisorState, sid: str, done: Done, timeout: float = 45):
        async with asyncio.timeout(timeout):
            while not done(events := await state.reader.read(sid)):
                await asyncio.sleep(0.1)
        return events


@pytest_asyncio.fixture(loop_scope="session")
async def cluster(temporal_env: WorkflowEnvironment, tmp_path: Path) -> AsyncIterator[Cluster]:
    queue = f"durability-{uuid.uuid4().hex[:8]}"
    address = temporal_env.client.service_client.config.target_host
    namespace = temporal_env.client.namespace
    client = await connect(TemporalSettings(address=address, namespace=namespace, task_queue=queue))
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'cluster.db'}"
    async with open_db(db_url) as (_, factory):  # creates the schema before any worker starts
        yield Cluster(
            address, namespace, queue, db_url, tmp_path / "blobs", client,
            SessionWorkflows(client, task_queue=queue), factory,
        )


@pytest.fixture
def slow_tools() -> AsyncIterator[Callable[[float], None]]:
    def set_delay(seconds: float) -> None:
        EchoRuntime.tool_delay_s = seconds

    yield set_delay
    EchoRuntime.tool_delay_s = 0.0


async def _spawn_worker(
    cluster: Cluster, *, tool_delay_s: float = 0.0, spec: dict | None = None
) -> tuple[asyncio.subprocess.Process, str]:
    spec = spec or {}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.support.worker_process",
        cluster.db_url, str(cluster.blob_dir), cluster.address, cluster.queue,
        json.dumps(spec | {"echo_delay_s": tool_delay_s}),
        stdout=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.stdout is not None
    async with asyncio.timeout(60):
        while not (line := (await proc.stdout.readline()).decode()).startswith("READY"):
            if not line:
                raise RuntimeError("worker process exited before it was ready")
    return proc, line.split()[1]


async def test_worker_killed_mid_turn_session_is_adopted_and_continues(cluster: Cluster) -> None:
    worker_a, home_a = await _spawn_worker(cluster, tool_delay_s=120)
    observer = await cluster.state()
    sid = uuid.uuid4().hex
    try:
        await cluster.sessions.create(sid, KG, timing=FAST)
        await cluster.sessions.submit(sid, Message("c1", "slow work"))
        await cluster.wait_for(observer, sid, _count("ToolStart"))
    finally:
        worker_a.kill()  # SIGKILL mid-tool: no cleanup, no goodbye
        await worker_a.wait()

    worker_b = await cluster.state()
    async with session_worker(cluster.client, worker_b, task_queue=cluster.queue) as b:
        # Never silently re-run a half-finished turn: tools may already have run.
        await cluster.wait_for(observer, sid, _error("TurnInterrupted"))
        await cluster.sessions.submit(sid, Message("c2", "after the crash"))
        await cluster.wait_for(observer, sid, _count("RunEnd"))
        status = await cluster.sessions.status(sid)
        assert status.phase is Phase.IDLE
        assert status.turns_completed == 2
        assert b.home_queue != home_a
        await cluster.sessions.terminate(sid)


async def test_abort_stops_a_running_turn_and_the_session_stays_usable(
    cluster: Cluster, slow_tools: Callable[[float], None]
) -> None:
    state = await cluster.state()
    async with session_worker(cluster.client, state, task_queue=cluster.queue):
        sid = uuid.uuid4().hex
        await cluster.sessions.create(sid, KG, timing=FAST)
        slow_tools(120)
        await cluster.sessions.submit(sid, Message("c1", "long"))
        await cluster.wait_for(state, sid, _count("ToolStart"))
        await cluster.sessions.abort(sid)
        events = await cluster.wait_for(
            state,
            sid,
            lambda evs: any(
                isinstance(e.payload, TurnEnd) and e.payload.stop_reason == "aborted" for e in evs
            ),
            timeout=15,
        )
        assert "RunEnd" not in _types(events)
        slow_tools(0)
        await cluster.sessions.submit(sid, Message("c2", "quick"))
        await cluster.wait_for(state, sid, _count("RunEnd"))
        assert (await cluster.sessions.status(sid)).phase is Phase.IDLE
        await cluster.sessions.terminate(sid)


async def test_messages_sent_while_busy_queue_and_each_runs_once(
    cluster: Cluster, slow_tools: Callable[[float], None]
) -> None:
    state = await cluster.state()
    async with session_worker(cluster.client, state, task_queue=cluster.queue):
        sid = uuid.uuid4().hex
        await cluster.sessions.create(sid, KG, timing=FAST)
        slow_tools(1.0)
        first = await cluster.sessions.submit(sid, Message("c1", "one"))
        second = await cluster.sessions.submit(sid, Message("c2", "two"))
        duplicate = await cluster.sessions.submit(sid, Message("c2", "two"))
        assert (first.queued, second.queued, duplicate.queued) == (False, True, True)
        await cluster.wait_for(state, sid, _count("RunEnd", 2))
        await settled(cluster.sessions, sid, 2)
        assert _types(await state.reader.read(sid)).count("RunStart") == 2
        await cluster.sessions.terminate(sid)


async def test_long_sessions_continue_as_new_and_keep_their_state(cluster: Cluster) -> None:
    state = await cluster.state()
    async with session_worker(cluster.client, state, task_queue=cluster.queue):
        sid = uuid.uuid4().hex
        await cluster.sessions.create(sid, KG, timing=FAST)
        handle = cluster.client.get_workflow_handle(workflow_id(sid))
        first_run = (await handle.describe()).run_id
        for i in range(8):
            await cluster.sessions.submit(sid, Message(f"c{i}", f"message {i}"))
            await cluster.wait_for(state, sid, _count("RunEnd", i + 1))
        assert (await handle.describe()).run_id != first_run, "expected continue-as-new"
        # Re-sending an early command after continue-as-new must not run it again.
        await cluster.sessions.submit(sid, Message("c0", "message 0"))
        await settled(cluster.sessions, sid, 8)
        assert _types(await state.reader.read(sid)).count("RunStart") == 8
        assert (await cluster.sessions.status(sid)).turns_completed == 8
        await cluster.sessions.terminate(sid)


async def test_recorded_histories_replay_deterministically(cluster: Cluster) -> None:
    """Run this check against production histories in CI before deploying workflow changes."""
    state = await cluster.state()
    async with session_worker(cluster.client, state, task_queue=cluster.queue):
        sid = uuid.uuid4().hex
        await cluster.sessions.create(sid, KG, timing=FAST)
        for i in range(5):
            await cluster.sessions.submit(sid, Message(f"r{i}", "hi"))
            await cluster.wait_for(state, sid, _count("RunEnd", i + 1))
        await cluster.sessions.terminate(sid)

    replayer = Replayer(workflows=[AgentSessionWorkflow], data_converter=pydantic_data_converter)
    runs = cluster.client.list_workflows(f"WorkflowId = '{workflow_id(sid)}'")
    histories = [
        await cluster.client.get_workflow_handle(run.id, run_id=run.run_id).fetch_history()
        async for run in runs
    ]
    assert len(histories) >= 2, "expected the session to span continue-as-new runs"
    async with replayer.workflow_replay_iterator(_aiter(histories)) as results:
        async for result in results:
            assert result.replay_failure is None, result.replay_failure


async def test_a_loop_guard_trip_ends_the_turn_and_keeps_the_session(
    cluster: Cluster, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F32: one runaway turn used to fail the whole session; now only that turn ends."""
    state = await cluster.state()
    async with session_worker(cluster.client, state, task_queue=cluster.queue):
        sid = uuid.uuid4().hex
        await cluster.sessions.create(sid, KG, timing=FAST)
        monkeypatch.setattr(EchoRuntime, "repeat_tool_times", 3)  # identical calls in one run
        await cluster.sessions.submit(sid, Message("loop", "again and again"))
        await cluster.wait_for(state, sid, _error("LoopGuard"))
        await cluster.wait_for(state, sid, _count("RunEnd", 1))
        assert await state.manager.session_status(sid) == "active"
        monkeypatch.setattr(EchoRuntime, "repeat_tool_times", 0)
        await cluster.sessions.submit(sid, Message("after", "anything"))  # the session still answers
        await cluster.wait_for(state, sid, _count("RunEnd", 2))
        await cluster.sessions.terminate(sid)


async def _aiter(items: list) -> AsyncIterator:
    for item in items:
        yield item


async def test_a_deploy_stopping_the_worker_mid_turn_is_a_crash_not_a_stop(
    cluster: Cluster, slow_tools: Callable[[float], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker shut down mid-turn (a deploy) has its activities cancelled once the grace
    passes. That is not the user's stop: the turn must not be recorded as aborted, but
    recovered on another worker as after a crash (pi-temporal's shutdown-check)."""
    from supervisor.temporal import worker as worker_module

    monkeypatch.setattr(worker_module, "GRACEFUL_SHUTDOWN", timedelta(seconds=1))
    observer = await cluster.state()
    sid = uuid.uuid4().hex
    slow_tools(120)
    async with session_worker(cluster.client, await cluster.state(), task_queue=cluster.queue):
        await cluster.sessions.create(sid, KG, timing=FAST)
        await cluster.sessions.submit(sid, Message("c1", "slow work"))
        await cluster.wait_for(observer, sid, _count("ToolStart"))
    # The worker has shut down: its turn was cancelled as a shutdown.
    slow_tools(0)
    async with session_worker(cluster.client, await cluster.state(), task_queue=cluster.queue):
        events = await cluster.wait_for(observer, sid, _error("TurnInterrupted"), timeout=60)
        assert not [
            e for e in events if isinstance(e.payload, TurnEnd) and e.payload.stop_reason == "aborted"
        ], "the deploy was recorded as the user stopping the turn"
        await cluster.sessions.submit(sid, Message("c2", "after the deploy"))
        await cluster.wait_for(observer, sid, _count("RunEnd"))
        await cluster.sessions.terminate(sid)
