"""The brain runs anywhere: model steps on any worker, tools and checkpoints beside the sandbox.

Worker A creates the session and holds its sandbox; then A stops taking shared
work (it keeps only its home queue), and worker B, which has never run the
session, picks up the workflow and every model step. B builds the session's
brain from its pins without a sandbox and without resuming it; A runs the tool
and the checkpoints. The turn is the same turn a single worker would run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from google.protobuf.json_format import MessageToDict
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, Worker

from supervisor.agent.events import HandCheckpointed, MessageEnd
from supervisor.agent.runtimes.githi.runtime import GithiRuntime
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.temporal.activities import SessionActivities
from supervisor.temporal.contracts import Activity, Message, workflow_id
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.worker import home_queue, session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import WRITE, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway
from tests.support.workflow_histories import replayable_histories

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "histories"


async def _workers_of(env: Env, sid: str) -> dict[str, set[str]]:
    """Which worker identities ran each activity type, across continue-as-new runs."""
    ran: dict[str, set[str]] = {}
    run_id: str | None = None
    while True:
        history = await env.client.get_workflow_handle(workflow_id(sid), run_id=run_id).fetch_history()
        names: dict[str, str] = {}
        for event in history.events:
            data = MessageToDict(event)
            if scheduled := data.get("activityTaskScheduledEventAttributes"):
                names[str(event.event_id)] = scheduled["activityType"]["name"]
            elif started := data.get("activityTaskStartedEventAttributes"):
                name = names.get(str(started["scheduledEventId"]))
                if name is not None:
                    ran.setdefault(name, set()).add(started["identity"])
        first = history.events[0].workflow_execution_started_event_attributes
        if not (run_id := first.continued_execution_run_id or None):
            return ran


async def test_model_steps_run_on_a_worker_without_the_sandbox(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    store = MemoryWorkspaceStore(objects=MemoryObjectStorage())
    a, b = await env.state(workspace_store=store), await env.state(workspace_store=store)
    assert a.writer is not None and b.writer is not None
    acts_a = SessionActivities(a.manager, reader=a.reader, writer=a.writer, home_queue=home_queue(env.queue))
    acts_b = SessionActivities(b.manager, reader=b.reader, writer=b.writer, home_queue=home_queue(env.queue))
    shared = {"task_queue": env.queue, "workflows": [AgentSessionWorkflow, SubagentJobWorkflow]}
    a_home = Worker(env.client, task_queue=acts_a.home_queue, activities=acts_a.all(), identity="a")
    async with a_home:
        async with Worker(env.client, activities=acts_a.all(), identity="a", **shared):  # type: ignore[arg-type]
            sid = await env.create(a)
        # A keeps its home queue (the sandbox's worker); B takes everything shared.
        async with Worker(env.client, activities=acts_b.all(), identity="b", **shared):  # type: ignore[arg-type]
            await env.sessions.submit(sid, Message("c1", "write a note"))
            events = await env.wait_for(b, sid, run_ended())

    assert payloads(events, MessageEnd)[-1].content[0]["text"] == "done after 1 tool results"
    ran = await _workers_of(env, sid)
    assert ran[Activity.MODEL_STEP] == {"b"}
    assert ran[Activity.TOOL_CALL] == {"a"}
    assert ran[Activity.CHECKPOINT] == {"a"}
    # The note the tool wrote was checkpointed by A while B's brain thought.
    assert [e.files for e in payloads(events, HandCheckpointed)] == [1]

    # B built a brain (pinned seams, the model, no sandbox) and never resumed the session.
    brain = b.manager._brains[sid]
    assert isinstance(brain, GithiRuntime) and brain.workspace is None
    assert brain.seams is not None and brain.seams.model_gateway is not None
    assert b.manager._registry.get(sid) is None and b.manager._hands.sandbox(sid) is None
    assert a.manager._hands.sandbox(sid) is not None


async def test_a_history_from_before_the_brain_ran_anywhere_still_replays() -> None:
    """Recorded on the previous workflow (model steps on the session's worker, no CHECKPOINT).

    ``workflow.patched("brain-anywhere")`` is false for it, so the replay takes the
    old path; a session mid-turn during the deploy keeps its history valid.
    """
    history = WorkflowHistory.from_json(
        "stepped-turn", (FIXTURES / "stepped-turn-before-brain-anywhere.json").read_text()
    )
    replayer = Replayer(
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        data_converter=pydantic_data_converter,
    )
    result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
    assert result.replay_failure is None, result.replay_failure


async def test_new_histories_replay(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model(WRITE)
    state = await env.state(workspace_store=MemoryWorkspaceStore(objects=MemoryObjectStorage()))
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "write a note"))
        await env.wait_for(state, sid, run_ended())
    replayer = Replayer(
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        data_converter=pydantic_data_converter,
    )
    query = f"WorkflowType = 'AgentSession' AND TaskQueue = '{env.queue}'"
    async for history in replayable_histories(env.client, query):
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        assert result.replay_failure is None, result.replay_failure
