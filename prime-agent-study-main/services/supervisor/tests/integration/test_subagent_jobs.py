"""Background subagent jobs from before subagents were sessions (``supervisor.temporal.jobs``).

Nothing starts one now. One still running when this deployed ends as retired: its end
reaches its session's journal once (JobEnd + SubagentEnd), and its history and its
session's replay.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import JobEnd, SubagentEnd
from supervisor.temporal.contracts import JobSpec, job_workflow_id
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.jobs import RETIRED
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import FAST, Env, has_event, payloads
from tests.support.scripted import ScriptedGateway
from tests.support.workflow_histories import replayable_histories

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_a_job_from_before_subagents_were_sessions_ends_as_retired(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    worker = await env.state()
    async with session_worker(env.client, worker, task_queue=env.queue):
        sid = await env.create(worker)
        # As the session's workflow started one: a transport-based provider and its request.
        spec = JobSpec(sid, "job-1", "run-1", "nested_session", {"prompt": "old work"}, FAST)
        handle = await env.client.start_workflow(
            SubagentJobWorkflow.run, spec, id=job_workflow_id(sid, "job-1"), task_queue=env.queue
        )
        outcome = await handle.result()
        events = await env.wait_for(
            worker, sid, has_event(lambda p: isinstance(p, SubagentEnd) and p.run_id == "run-1")
        )
    assert (outcome.job_id, outcome.stop_reason, outcome.output) == ("job-1", "error", RETIRED)
    assert [(e.job_id, e.stop_reason, e.output) for e in payloads(events, JobEnd)] == [("job-1", "error", RETIRED)]
    assert [(e.run_id, e.stop_reason) for e in payloads(events, SubagentEnd)] == [("run-1", "error")]


async def test_job_and_session_histories_replay_deterministically(env: Env) -> None:
    replayer = Replayer(
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        data_converter=pydantic_data_converter,
    )
    replayed = 0
    for kind in ("AgentSession", "SubagentJob"):
        async for history in replayable_histories(env.client, f"WorkflowType = '{kind}'"):
            result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
            assert result.replay_failure is None, (kind, result.replay_failure)
            replayed += 1
    assert replayed >= 1
