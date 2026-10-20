"""Memory inside running githi turns, against a real Temporal server and a scripted model.

Recall goes into the prompt (only what is new after the first run), the agent
curates its notebook through the memory tool, finished turns are captured in a
durable burst, and a change of audience cuts earlier history off.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import MemoryContext, MemoryInjected, ToolEnd
from supervisor.app import SupervisorState
from supervisor.db.models import ScopeMembershipRow, ScopeRow, SessionRow
from supervisor.memory.binding import MemoryBinding
from supervisor.memory.service import CaptureContext
from supervisor.memory.types import Fact, Sensitivity, personal_source
from supervisor.seams.core.types import ChatMessage
from supervisor.temporal.contracts import Message
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import LS, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway
from tests.support.workflow_histories import replayable_histories

pytestmark = pytest.mark.asyncio(loop_scope="session")

TRACKED = CaptureContext(sensitivity=Sensitivity.ORDINARY, inherited=())
QUICK = {"capture_quiet_s": 1}


async def _binding(state: SupervisorState, sid: str) -> MemoryBinding:
    resolved = await state.memory.session_audience(sid)  # type: ignore[union-attr]
    assert resolved is not None
    return resolved[1]


async def _facts(state: SupervisorState, sid: str) -> list[Fact]:
    binding = await _binding(state, sid)
    assert binding.write is not None
    return await state.memory.notebooks.facts(binding.write)  # type: ignore[union-attr]


async def _wait_facts(state: SupervisorState, sid: str, n: int, timeout: float = 30) -> list[Fact]:
    async with asyncio.timeout(timeout):
        while len(facts := await _facts(state, sid)) < n:
            await asyncio.sleep(0.2)
    return facts


def _memory_blocks(messages: list[ChatMessage]) -> list[str]:
    return [m.content for m in messages if m.role == "system" and "What you remember" in m.content]


async def test_recall_is_injected_and_later_runs_add_only_whats_new(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        notebook = (await _binding(state, sid)).write
        assert notebook is not None
        await state.memory.notebooks.capture(notebook, ["Prefers tea"], context=TRACKED)
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(state, sid, run_ended())
        (first,) = _memory_blocks(gateway.seen[0])
        assert "### default" in first and "Prefers tea" in first
        await state.memory.notebooks.capture(notebook, ["Owns billing"], context=TRACKED)
        await env.sessions.submit(sid, Message("c2", "again"))
        events = await env.wait_for(state, sid, run_ended(2))
    blocks = _memory_blocks(gateway.seen[-1])
    assert len(blocks) == 2, "the first run's memory stays in the history"
    assert blocks[1].count("Owns billing") == 1 and "Prefers tea" not in blocks[1]
    assert "New or updated memory facts" in blocks[1]
    injected = payloads(events, MemoryInjected)
    assert [len(i.shown) for i in injected] == [1, 2]


async def test_the_agent_curates_its_notebook_with_the_memory_tool(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("memory", {"action": "add", "facts": [{"text": "Prefers terse replies"}]}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, memory={"capture": "writable", "strategy": "agent-only"})
        await env.sessions.submit(sid, Message("c1", "remember that I like short answers"))
        events = await env.wait_for(state, sid, run_ended())
    (end,) = payloads(events, ToolEnd)
    assert end.result["payload"] == "Remembered 1 fact." and not end.is_error
    (fact,) = await _facts(state, sid)
    assert fact.text == "Prefers terse replies"
    assert fact.provenance.sources == (personal_source("alice"),)
    classified = [k for k, _ in gateway.one_shots if k.startswith("Classify")]
    assert classified, "explicit adds are classified"
    assert "sole curator" in _memory_blocks(gateway.seen[0])[0]


async def test_finished_turns_are_captured_after_a_quiet_window(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(extract="SENSITIVITY: ordinary\n- Prefers tea")
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, memory=QUICK)
        await env.sessions.submit(sid, Message("c1", "I always drink tea"))
        await env.wait_for(state, sid, run_ended())
        (fact,) = await _wait_facts(state, sid, 1)
    assert fact.text == "Prefers tea"
    assert fact.provenance.sensitivity is Sensitivity.ORDINARY
    assert fact.provenance.sources == (personal_source("alice"),)
    assert not fact.provenance.source_unknown, "no tools ran: everything is tracked"
    (extraction,) = [c for k, c in gateway.one_shots if k.startswith("You extract")]
    assert "User said:\nI always drink tea" in extraction


async def test_a_turn_that_used_tools_captures_unknown_provenance(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, extract="SENSITIVITY: ordinary\n- Keeps notes in the repo root")
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, memory=QUICK)
        await env.sessions.submit(sid, Message("c1", "what files do I have?"))
        await env.wait_for(state, sid, run_ended())
        (fact,) = await _wait_facts(state, sid, 1)
    assert fact.provenance.source_unknown, "ls output is untracked context"


async def test_bursts_flush_at_max_turns_and_when_the_session_ends(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(extract="SENSITIVITY: ordinary\n- Likes jazz")
    state = await env.state()

    def extractions() -> list[str]:
        return [c for k, c in gateway.one_shots if k.startswith("You extract")]

    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, memory={"capture_quiet_s": 3600, "capture_max_turns": 2})
        for i in range(2):
            await env.sessions.submit(sid, Message(f"c{i}", f"turn {i}"))
            await env.wait_for(state, sid, run_ended(i + 1))
        await _wait_facts(state, sid, 1)
        (both,) = extractions()
        assert "turn 0" in both and "turn 1" in both, "one burst, both turns"
        await env.sessions.submit(sid, Message("c2", "turn 2"))
        await env.wait_for(state, sid, run_ended(3))
        await env.sessions.terminate(sid)  # the hour-long quiet window never elapses
        async with asyncio.timeout(15):
            while len(extractions()) < 2:
                await asyncio.sleep(0.2)
    assert "turn 2" in extractions()[1]


async def test_a_pending_capture_survives_its_worker(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(extract="SENSITIVITY: ordinary\n- Works from Lisbon")
    first = await env.state()
    async with session_worker(env.client, first, task_queue=env.queue):
        sid = await env.create(first, memory={"capture_quiet_s": 3})
        await env.sessions.submit(sid, Message("c1", "I'm based in Lisbon"))
        await env.wait_for(first, sid, run_ended())
    assert await _facts(first, sid) == [], "the window had not elapsed when the worker left"
    second = await env.state()
    async with session_worker(env.client, second, task_queue=env.queue):
        (fact,) = await _wait_facts(second, sid, 1)
    assert fact.text == "Works from Lisbon"


async def test_agent_only_sessions_are_never_captured(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(extract="SENSITIVITY: ordinary\n- Should not be saved")
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, memory={"strategy": "agent-only", "capture_quiet_s": 0.5})
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(state, sid, run_ended())
        await asyncio.sleep(2)
    assert await _facts(state, sid) == []
    assert not [k for k, _ in gateway.one_shots if k.startswith("You extract")]


async def test_a_new_audience_cuts_earlier_history_off(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        notebook = (await _binding(state, sid)).write
        assert notebook is not None
        await state.memory.notebooks.capture(notebook, ["Private salary details"], context=TRACKED)
        await env.sessions.submit(sid, Message("c1", "my private question"))
        await env.wait_for(state, sid, run_ended())
        # The session is now shared with bob (it moves into a scope with two members).
        scope = uuid.uuid4().hex
        async with state.manager._db() as db:
            db.add(ScopeRow(id=scope, type="team", owner_principal_id="alice", name="team"))
            await db.flush()
            for person in ("alice", "bob"):
                db.add(ScopeMembershipRow(id=uuid.uuid4().hex, scope_id=scope, principal_id=person))
            (await db.get(SessionRow, sid)).scope_id = scope  # type: ignore[union-attr]
            await db.commit()
        await env.sessions.submit(sid, Message("c2", "hello team"))
        events = await env.wait_for(state, sid, run_ended(2))
    contexts = payloads(events, MemoryContext)
    assert contexts[0].through_seq == -1 and contexts[-1].through_seq > 0
    shared_call = gateway.seen[-1]
    texts = " ".join(m.content for m in shared_call)
    assert "my private question" not in texts, "earlier history never reaches the new audience"
    assert "Private salary details" not in texts, "personal facts are not disclosed to bob"
    assert "hello team" in texts


async def test_the_history_tool_searches_this_conversation(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "the codename is kiwi"))
        await env.wait_for(state, sid, run_ended())
        gateway.calls = [("history", {"query": "codename"})]
        await env.sessions.submit(sid, Message("c2", "what was the codename?"))
        events = await env.wait_for(state, sid, run_ended(2))
    (end,) = payloads(events, ToolEnd)
    assert "user#" in end.result["payload"] and "kiwi" in end.result["payload"]


async def test_memory_histories_replay_deterministically(env: Env) -> None:
    replayer = Replayer(
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        data_converter=pydantic_data_converter,
    )
    async for history in replayable_histories(env.client, "WorkflowType = 'AgentSession'"):
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        assert result.replay_failure is None, result.replay_failure
