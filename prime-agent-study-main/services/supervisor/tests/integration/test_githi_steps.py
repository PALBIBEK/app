"""githi turns driven step by step by the session workflow, against a real Temporal server.

One activity per model call and per tool call; tools retry or run at most once by
their manifest class; the loop guard and approval timers live in the workflow.
The model is a scripted gateway (a pure function of the conversation).
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

import pytest
import pytest_asyncio
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    Envelope,
    ErrorEvent,
    MessageEnd,
    ToolEnd,
    TurnEnd,
)
from supervisor.app import SupervisorState
from supervisor.catalog.repository import persist_offer
from supervisor.config import TemporalSettings
from supervisor.db.engine import open_db
from supervisor.temporal.client import connect
from supervisor.temporal.contracts import (
    Activity,
    CreateSession,
    Message,
    Phase,
    Timing,
    workflow_id,
)
from supervisor.temporal.sessions import SessionCommandError, SessionWorkflows
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.platform import boot_platform
from tests.support.scripted import ScriptedGateway, scripted_model, slow_tool

pytestmark = pytest.mark.asyncio(loop_scope="session")

FAST = Timing(placement_timeout_s=2.0, heartbeat_timeout_s=2.0, turn_timeout_s=120.0)
WRITE = ("write", {"file_path": "notes.txt", "content": "hello"})
LS = ("ls", {"path": "."})

Done = Callable[[list[Envelope]], bool]


def _types(events: list[Envelope]) -> list[str]:
    return [type(e.payload).__name__ for e in events]


def _has(predicate: Callable[[object], bool]) -> Done:
    return lambda events: any(predicate(e.payload) for e in events)


def _run_ended(n: int = 1) -> Done:
    return lambda events: _types(events).count("RunEnd") >= n


def _payloads[E](events: list[Envelope], kind: type[E]) -> list[E]:
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

    async def state(self) -> SupervisorState:
        state, _ = await boot_platform(self.factory, self.blob_dir)  # type: ignore[arg-type]
        return state

    async def create(self, state: SupervisorState, harness: str = "githi-v1") -> str:
        """A githi session from the shipped composition, re-pinned to ``harness``."""
        name, digest = await self.composition(state, harness)
        sid = uuid.uuid4().hex
        await self.sessions.create(
            sid,
            CreateSession("t1", harness, composition_id=name, composition_digest=digest),
            timing=FAST,
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
        history = await self.client.get_workflow_handle(workflow_id(sid)).fetch_history()
        return [
            e.activity_task_scheduled_event_attributes.activity_type.name
            for e in history.events
            if e.HasField("activity_task_scheduled_event_attributes")
        ]


@pytest_asyncio.fixture(loop_scope="session")
async def env(temporal_env: WorkflowEnvironment, tmp_path: Path) -> AsyncIterator[Env]:
    queue = f"steps-{uuid.uuid4().hex[:8]}"
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


async def test_githi_turn_runs_one_activity_per_model_and_tool_call(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(LS)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "list the files"))
        events = await env.wait_for(state, sid, _run_ended())

    assert _types(events) == [
        "RunStart",
        "MessageEnd",
        "TurnStart",
        "MessageEnd",
        "ToolStart",
        "ToolEnd",
        "TurnStart",
        "MessageEnd",
        "TurnEnd",
        "RunEnd",
    ]
    assert _payloads(events, MessageEnd)[-1].content[0]["text"] == "done after 1 tool results"
    scheduled = await env.activity_types(sid)
    assert scheduled.count(Activity.MODEL_STEP) == 2
    assert scheduled.count(Activity.TOOL_CALL) == 1
    # The model saw the pinned system prompt, its own tool call, and the result tied to it.
    second = gateway.seen[1]
    assert second[0].role == "system"
    call = next(m for m in second if m.tool_calls)
    result = next(m for m in second if m.role == "tool")
    assert result.tool_call_id == call.tool_calls[0]["id"]


async def test_each_turn_sees_the_conversation_so_far(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "first question"))
        await env.wait_for(state, sid, _run_ended())
        await env.sessions.submit(sid, Message("c2", "second question"))
        await env.wait_for(state, sid, _run_ended(2))
    texts = [m.content for m in gateway.seen[-1] if m.role in ("user", "assistant")]
    assert texts[:3] == ["first question", "done after 0 tool results", "second question"]


# --- approvals: the workflow holds the timer ------------------------------------


async def _await_approval(env: Env, state: SupervisorState, sid: str) -> str:
    events = await env.wait_for(state, sid, _has(lambda p: isinstance(p, ApprovalRequired)))
    return _payloads(events, ApprovalRequired)[0].approval_id


@pytest.mark.parametrize(("choice", "tool_ran"), [("allow", True), ("deny", False)])
async def test_a_human_decides_a_gated_tool(
    env: Env, model: Callable[..., ScriptedGateway], choice: str, tool_ran: bool
) -> None:
    model(WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-approve-deny")
        await env.sessions.submit(sid, Message("c1", "write a note"))
        approval_id = await _await_approval(env, state, sid)
        await env.sessions.respond_approval(sid, approval_id, choice)
        events = await env.wait_for(state, sid, _run_ended())
    (end,) = _payloads(events, ToolEnd)
    assert end.is_error is not tool_ran
    if not tool_ran:
        assert end.result["code"] == "APPROVAL_DENIED"
    (resolved,) = _payloads(events, ApprovalResolved)
    assert (resolved.choice, resolved.status) == (choice, "responded")


@pytest.mark.parametrize(
    ("harness", "tool_ran"), [("githi-approve-deny", False), ("githi-approve-allow", True)]
)
async def test_unanswered_approval_applies_on_timeout(
    env: Env, model: Callable[..., ScriptedGateway], harness: str, tool_ran: bool
) -> None:
    model(WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, harness)
        await env.sessions.submit(sid, Message("c1", "write a note"))
        events = await env.wait_for(state, sid, _run_ended())  # manifest timeout is 2s
    (resolved,) = _payloads(events, ApprovalResolved)
    assert resolved.status == "timed_out"
    assert _payloads(events, ToolEnd)[0].is_error is not tool_ran


async def test_an_invalid_choice_is_rejected_and_the_approval_stays_open(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-approve-allow")
        await env.sessions.submit(sid, Message("c1", "write a note"))
        approval_id = await _await_approval(env, state, sid)
        with pytest.raises(SessionCommandError) as err:
            await env.sessions.respond_approval(sid, approval_id, "maybe")
        assert err.value.kind == "InvalidApprovalChoice"
        await env.sessions.respond_approval(sid, approval_id, "allow")
        await env.wait_for(state, sid, _run_ended())


async def test_approval_wait_survives_a_worker_restart(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    first = await env.state()
    async with session_worker(env.client, first, task_queue=env.queue):
        sid = await env.create(first, "githi-approve-deny")
        await env.sessions.submit(sid, Message("c1", "write a note"))
        approval_id = await _await_approval(env, first, sid)
    # No worker is running now; the wait lives in Temporal, not in any process.
    second = await env.state()
    async with session_worker(env.client, second, task_queue=env.queue):
        await env.sessions.respond_approval(sid, approval_id, "allow")
        events = await env.wait_for(second, sid, _run_ended())
    assert not _payloads(events, ToolEnd)[0].is_error


# --- policy gates ---------------------------------------------------------------


async def test_a_looping_model_trips_the_guard_and_closes_the_session(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, repeat=True)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "list forever"))
        events = await env.wait_for(state, sid, _run_ended())
        handle = env.client.get_workflow_handle_for(AgentSessionWorkflow.run, workflow_id(sid))
        async with asyncio.timeout(15):
            assert (await handle.result()).phase is Phase.FAILED
    assert [e.error_class for e in _payloads(events, ErrorEvent)] == ["LoopGuard"]
    assert len(_payloads(events, ToolEnd)) == 2  # the third identical call never ran


async def test_a_model_that_never_answers_ends_the_run_not_the_session(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, vary_args=True)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "keep going"))
        events = await env.wait_for(state, sid, _run_ended())
        assert [e.error_class for e in _payloads(events, ErrorEvent)] == ["AgentLoopError"]
        assert (await env.sessions.status(sid)).phase is Phase.IDLE


async def test_spending_the_budget_stops_the_run_and_refuses_new_turns(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, cost_per_call=0.02)  # the githi-budget variant allows $0.01
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-budget")
        await env.sessions.submit(sid, Message("c1", "expensive"))
        events = await env.wait_for(state, sid, _run_ended())
        assert [e.error_class for e in _payloads(events, ErrorEvent)] == ["BudgetExceeded"]
        assert not _payloads(events, ToolEnd), "no tool runs once the budget is spent"
        with pytest.raises(SessionCommandError) as err:
            await env.sessions.submit(sid, Message("c2", "more"))
        assert err.value.kind == "BudgetExceeded"


async def test_a_flaky_model_gateway_is_retried(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(fail_first=2)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, _run_ended())
    assert not _payloads(events, ErrorEvent)
    assert _types(events).count("MessageEnd") == 2  # one user, one answer: no duplicates


# --- abort and worker loss ------------------------------------------------------


async def test_abort_during_a_tool_closes_the_call_and_the_run(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    with slow_tool("write", 60):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "write slowly"))
            await env.wait_for(state, sid, _has(lambda p: type(p).__name__ == "ToolStart"))
            await env.sessions.abort(sid)
            events = await env.wait_for(state, sid, _run_ended(), timeout=20)
    assert _payloads(events, TurnEnd)[-1].stop_reason == "aborted"
    assert _payloads(events, ToolEnd)[0].result["code"] == "TOOL_OUTCOME_UNKNOWN"


async def test_abort_during_an_approval_wait(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-approve-allow")
        await env.sessions.submit(sid, Message("c1", "write a note"))
        await _await_approval(env, state, sid)
        await env.sessions.abort(sid)
        events = await env.wait_for(state, sid, _run_ended())
    assert _payloads(events, TurnEnd)[-1].stop_reason == "aborted"
    assert _payloads(events, ApprovalResolved)[0].choice == "deny"
    assert not _payloads(events, ToolEnd)


async def _spawn_worker(env: Env, spec: dict) -> tuple[asyncio.subprocess.Process, str]:
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


@pytest.mark.parametrize(
    ("harness", "interrupted"),
    [("githi-v1", False), ("githi-once", True)],
    ids=["idempotent-tool-is-retried", "non-atomic-tool-is-not"],
)
async def test_worker_killed_during_a_tool(
    env: Env, model: Callable[..., ScriptedGateway], harness: str, interrupted: bool
) -> None:
    model(WRITE)
    observer = await env.state()
    sid = await env.create(observer, harness)
    worker, _ = await _spawn_worker(
        env, {"scripted_calls": [list(WRITE)], "slow_tool": ["write", 120]}
    )
    try:
        await env.sessions.submit(sid, Message("c1", "write a note"))
        await env.wait_for(observer, sid, _has(lambda p: type(p).__name__ == "ToolStart"))
    finally:
        worker.kill()  # SIGKILL mid-tool
        await worker.wait()

    adopter = await env.state()
    async with session_worker(env.client, adopter, task_queue=env.queue):
        events = await env.wait_for(observer, sid, _run_ended())
    assert _types(events).count("ToolStart") == 1, "the call is journalled once"
    (end,) = _payloads(events, ToolEnd)
    if interrupted:
        assert end.result["code"] == "TOOL_OUTCOME_UNKNOWN"
        assert [e.error_class for e in _payloads(events, ErrorEvent)] == ["TurnInterrupted"]
    else:
        assert not end.is_error, "an idempotent tool re-runs on the adopting worker"
        assert not _payloads(events, ErrorEvent)
