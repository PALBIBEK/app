"""githi turns driven step by step by the session workflow, against a real Temporal server.

One activity per model call and per tool call; tools retry or run at most once by
their manifest class; the loop guard and approval timers live in the workflow.
The model is a scripted gateway (a pure function of the conversation).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from temporalio.client import Client, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    ErrorEvent,
    MessageEnd,
    ToolEnd,
    TurnEnd,
)
from supervisor.app import SupervisorState
from supervisor.temporal.contracts import (
    Activity,
    Message,
    Phase,
    workflow_id,
)
from supervisor.temporal.sessions import SessionCommandError
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import (
    LS,
    WRITE,
    Env,
    event_types,
    has_event,
    payloads,
    run_ended,
    spawn_worker,
)
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_githi_turn_runs_one_activity_per_model_and_tool_call(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(LS)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "list the files"))
        events = await env.wait_for(state, sid, run_ended())

    assert event_types(events) == [
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
    assert payloads(events, MessageEnd)[-1].content[0]["text"] == "done after 1 tool results"
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
        await env.wait_for(state, sid, run_ended())
        await env.sessions.submit(sid, Message("c2", "second question"))
        await env.wait_for(state, sid, run_ended(2))
    texts = [m.content for m in gateway.seen[-1] if m.role in ("user", "assistant")]
    assert texts[:3] == ["first question", "done after 0 tool results", "second question"]


# --- approvals: the workflow holds the timer ------------------------------------


async def _await_approval(env: Env, state: SupervisorState, sid: str) -> str:
    events = await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ApprovalRequired)))
    return payloads(events, ApprovalRequired)[0].approval_id


@pytest.mark.parametrize(
    ("choice", "status", "tool_ran"), [("allow", "approved", True), ("deny", "denied", False)]
)
async def test_a_human_decides_a_gated_tool(
    env: Env, model: Callable[..., ScriptedGateway], choice: str, status: str, tool_ran: bool
) -> None:
    model(WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-approve-deny")
        await env.sessions.submit(sid, Message("c1", "write a note"))
        approval_id = await _await_approval(env, state, sid)
        await env.sessions.respond_approval(sid, approval_id, choice)
        events = await env.wait_for(state, sid, run_ended())
    (end,) = payloads(events, ToolEnd)
    assert end.is_error is not tool_ran
    if not tool_ran:
        assert end.result["code"] == "APPROVAL_DENIED"
    (resolved,) = payloads(events, ApprovalResolved)
    assert (resolved.choice, resolved.status) == (choice, status)


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
        events = await env.wait_for(state, sid, run_ended())  # manifest timeout is 2s
    (resolved,) = payloads(events, ApprovalResolved)
    assert resolved.status == "timed_out"
    assert payloads(events, ToolEnd)[0].is_error is not tool_ran


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
        await env.wait_for(state, sid, run_ended())


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
        events = await env.wait_for(second, sid, run_ended())
    assert not payloads(events, ToolEnd)[0].is_error


# --- policy gates ---------------------------------------------------------------


async def test_a_looping_model_trips_the_guard_and_closes_the_session(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, repeat=True)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "list forever"))
        events = await env.wait_for(state, sid, run_ended())
        handle = env.client.get_workflow_handle_for(AgentSessionWorkflow.run, workflow_id(sid))
        async with asyncio.timeout(15):
            assert (await handle.result()).phase is Phase.FAILED
    assert [e.error_class for e in payloads(events, ErrorEvent)] == ["LoopGuard"]
    assert len(payloads(events, ToolEnd)) == 2  # the third identical call never ran


async def test_a_model_that_never_answers_ends_the_run_not_the_session(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, vary_args=True)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "keep going"))
        events = await env.wait_for(state, sid, run_ended())
        assert [e.error_class for e in payloads(events, ErrorEvent)] == ["AgentLoopError"]
        assert (await env.sessions.status(sid)).phase is Phase.IDLE


async def test_spending_the_budget_stops_the_run_and_refuses_new_turns(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, cost_per_call=0.02)  # the githi-budget variant allows $0.01
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-budget")
        await env.sessions.submit(sid, Message("c1", "expensive"))
        # Queued behind c1, so it is only refused once c1 has spent the budget.
        assert (await env.sessions.submit(sid, Message("c2", "queued"))).queued
        events = await env.wait_for(state, sid, lambda ev: len(payloads(ev, ErrorEvent)) >= 2)
        assert [e.error_class for e in payloads(events, ErrorEvent)] == ["BudgetExceeded"] * 2
        assert not payloads(events, ToolEnd), "no tool runs once the budget is spent"
        assert event_types(events).count("RunStart") == 1, "the queued turn never starts"
        async with asyncio.timeout(10):
            while (await env.sessions.status(sid)).phase is not Phase.IDLE:
                await asyncio.sleep(0.1)
        # Idle: the refusal reaches the caller directly.
        with pytest.raises(SessionCommandError) as err:
            await env.sessions.submit(sid, Message("c3", "more"))
        assert err.value.kind == "BudgetExceeded"


async def test_a_flaky_model_gateway_is_retried(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(fail_first=2)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended())
    assert not payloads(events, ErrorEvent)
    assert event_types(events).count("MessageEnd") == 2  # one user, one answer: no duplicates


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
            await env.wait_for(state, sid, has_event(lambda p: type(p).__name__ == "ToolStart"))
            await env.sessions.abort(sid)
            events = await env.wait_for(state, sid, run_ended(), timeout=20)
    assert payloads(events, TurnEnd)[-1].stop_reason == "aborted"
    assert payloads(events, ToolEnd)[0].result["code"] == "TOOL_OUTCOME_UNKNOWN"


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
        events = await env.wait_for(state, sid, run_ended())
    assert payloads(events, TurnEnd)[-1].stop_reason == "aborted"
    assert payloads(events, ApprovalResolved)[0].choice == "deny"
    assert not payloads(events, ToolEnd)


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
    # Published before the worker process boots, so it hydrates the composition.
    await env.composition(observer, harness)
    worker, _ = await spawn_worker(
        env, {"scripted_calls": [list(WRITE)], "slow_tool": ["write", 120]}
    )
    try:
        sid = await env.create(observer, harness)
        await env.sessions.submit(sid, Message("c1", "write a note"))
        await env.wait_for(observer, sid, has_event(lambda p: type(p).__name__ == "ToolStart"))
    finally:
        worker.kill()  # SIGKILL mid-tool
        await worker.wait()

    adopter = await env.state()
    async with session_worker(env.client, adopter, task_queue=env.queue):
        events = await env.wait_for(observer, sid, run_ended())
    assert event_types(events).count("ToolStart") == 1, "the call is journalled once"
    (end,) = payloads(events, ToolEnd)
    if interrupted:
        assert end.result["code"] == "TOOL_OUTCOME_UNKNOWN"
        assert [e.error_class for e in payloads(events, ErrorEvent)] == ["TurnInterrupted"]
    else:
        assert not end.is_error, "an idempotent tool re-runs on the adopting worker"
        assert not payloads(events, ErrorEvent)


# --- determinism ----------------------------------------------------------------


async def _histories(client: Client) -> AsyncIterator[WorkflowHistory]:
    async for run in client.list_workflows("WorkflowType = 'AgentSession'"):
        yield await client.get_workflow_handle(run.id, run_id=run.run_id).fetch_history()


async def test_stepped_histories_replay_deterministically(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """Replays every session history this server has recorded (all scenarios above)."""
    model(WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-approve-deny")
        await env.sessions.submit(sid, Message("c1", "write a note"))
        await env.sessions.respond_approval(sid, await _await_approval(env, state, sid), "allow")
        await env.wait_for(state, sid, run_ended())
        await env.sessions.terminate(sid)

    replayer = Replayer(workflows=[AgentSessionWorkflow], data_converter=pydantic_data_converter)
    replayed = 0
    async with replayer.workflow_replay_iterator(_histories(env.client)) as results:
        async for result in results:
            assert result.replay_failure is None, result.replay_failure
            replayed += 1
    assert replayed >= 1
