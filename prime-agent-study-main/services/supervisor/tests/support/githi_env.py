"""A githi platform against the shared Temporal dev server: sessions, events, workers.

Fixtures ``env`` (a fresh task queue and database) and ``model`` (the scripted
gateway every githi session in the test uses); helpers to read the journal.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from supervisor.agent.events import Envelope
from supervisor.app import SupervisorState
from supervisor.catalog.repository import persist_offer
from supervisor.config import TemporalSettings
from supervisor.db.engine import open_db
from supervisor.temporal.client import connect
from supervisor.temporal.contracts import CreateSession, Timing, workflow_id
from supervisor.temporal.sessions import SessionWorkflows
from tests.support.platform import boot_platform
from tests.support.scripted import ScriptedGateway, scripted_model

FAST = Timing(placement_timeout_s=2.0, heartbeat_timeout_s=2.0, turn_timeout_s=120.0)
SLOW_HEARTBEAT = Timing(placement_timeout_s=2.0, heartbeat_timeout_s=60.0, turn_timeout_s=120.0)
"""A dead worker's running call goes unnoticed for a minute: a test can act while it "runs"."""
WRITE = ("write", {"file_path": "notes.txt", "content": "hello"})
LS = ("ls", {"path": "."})

Done = Callable[[list[Envelope]], bool]


def event_types(events: list[Envelope]) -> list[str]:
    return [type(e.payload).__name__ for e in events]


def has_event(predicate: Callable[[object], bool]) -> Done:
    return lambda events: any(predicate(e.payload) for e in events)


def run_ended(n: int = 1) -> Done:
    return lambda events: event_types(events).count("RunEnd") >= n


def payloads[E](events: list[Envelope], kind: type[E]) -> list[E]:
    return [e.payload for e in events if isinstance(e.payload, kind)]


@dataclass(frozen=True, slots=True)
class Env:
    address: str
    namespace: str
    queue: str
    db_url: str
    blob_dir: Path
    client: Client
    sessions: SessionWorkflows
    factory: object

    async def state(self, **options: Any) -> SupervisorState:
        """A supervisor process; ``options`` go to ``boot_platform`` (a workspace store)."""
        state, _ = await boot_platform(self.factory, self.blob_dir, **options)  # type: ignore[arg-type]
        return state

    async def create(
        self,
        state: SupervisorState,
        harness: str = "githi-v1",
        *,
        principal: str = "alice",
        memory: dict[str, Any] | None = None,
        timing: Timing = FAST,
    ) -> str:
        """A githi session from the shipped composition, re-pinned to ``harness``."""
        name, digest = await self.composition(state, harness)
        sid = uuid.uuid4().hex
        await self.sessions.create(
            sid,
            CreateSession(
                "t1",
                harness,
                composition_id=name,
                composition_digest=digest,
                principal_id=principal,
                memory=memory,
            ),
            timing=timing,
        )
        return sid

    async def composition(self, state: SupervisorState, harness: str) -> tuple[str, str]:
        catalog = state.composition_catalog
        assert catalog is not None
        latest = catalog.list_versions("composition", "githi-v1")[-1]
        base = catalog.get("composition", "githi-v1", latest)
        if harness == base.harness_version:
            return base.name, base.digest
        variant = catalog.offer(
            dataclasses.replace(
                base, name=f"githi-on-{harness}", harness_version=harness, digest=""
            )
        )
        # Persisted, so a worker booted later (or in another process) hydrates it.
        await persist_offer(
            self.factory,  # type: ignore[arg-type]
            catalog,
            "composition",
            variant.name,
            variant.digest,
        )
        return variant.name, variant.digest

    async def wait_for(
        self, state: SupervisorState, sid: str, done: Done, timeout: float = 45
    ) -> list[Envelope]:
        async with asyncio.timeout(timeout):
            while not done(events := await state.reader.read(sid)):
                await asyncio.sleep(0.1)
        return events

    async def activity_types(self, sid: str) -> list[str]:
        """Every activity the session's workflow scheduled, across continue-as-new runs."""
        names: list[str] = []
        run_id: str | None = None
        while True:
            handle = self.client.get_workflow_handle(workflow_id(sid), run_id=run_id)
            history = await handle.fetch_history()
            names[:0] = [
                e.activity_task_scheduled_event_attributes.activity_type.name
                for e in history.events
                if e.HasField("activity_task_scheduled_event_attributes")
            ]
            started = history.events[0].workflow_execution_started_event_attributes
            if not (run_id := started.continued_execution_run_id or None):
                return names


@pytest_asyncio.fixture(loop_scope="session")
async def env(temporal_env: WorkflowEnvironment, tmp_path: Path) -> AsyncIterator[Env]:
    queue = f"githi-{uuid.uuid4().hex[:8]}"
    address = temporal_env.client.service_client.config.target_host
    namespace = temporal_env.client.namespace
    client = await connect(TemporalSettings(address=address, namespace=namespace, task_queue=queue))
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'steps.db'}"
    async with open_db(db_url) as (_, factory):
        yield Env(
            address,
            namespace,
            queue,
            db_url,
            tmp_path / "blobs",
            client,
            SessionWorkflows(client, task_queue=queue),
            factory,
        )


@pytest.fixture
def model() -> Iterator[Callable[..., ScriptedGateway]]:
    with scripted_model(ScriptedGateway(calls=[])) as gateway:

        def script(*calls: tuple[str, dict], **options: object) -> ScriptedGateway:
            gateway.calls = list(calls)
            for name, value in options.items():
                setattr(gateway, name, value)
            return gateway

        yield script


async def spawn_worker(env: Env, spec: dict) -> tuple[asyncio.subprocess.Process, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.worker_process",
        env.db_url,
        str(env.blob_dir),
        env.address,
        env.queue,
        json.dumps(spec),
        stdout=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.stdout is not None
    async with asyncio.timeout(60):
        while not (line := (await proc.stdout.readline()).decode()).startswith("READY"):
            if not line:
                raise RuntimeError("worker process exited before it was ready")
    return proc, line.split()[1]
