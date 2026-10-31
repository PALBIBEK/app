"""Everything a githi session runs on comes from what it pinned.

A session pins a composition: prompt pack, tool set, memory profile, plug
bindings (gateway, subagent transport, model route), and through its harness a
sandbox. These tests drive real stepped turns against a Temporal dev server and
check each pin reaches githi: what the model is sent, where tools run, how
the conversation is compacted and what is recalled, and how githi delegates.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import (
    BackgroundResults,
    CompactionEnd,
    HandProvisioned,
    JobEnd,
    MemoryRecall,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    TurnEnd,
)
from supervisor.agent.runtimes.githi.loop import AgentLoopError
from supervisor.agent.runtimes.githi.prompt import TOOL_ORDER
from supervisor.app import SupervisorState
from supervisor.catalog.core.types import (
    DigestRef,
    FileEffects,
    RecallBackend,
    SandboxProviderName,
)
from supervisor.catalog.repository import persist_offer
from supervisor.hands import HandWorkspace
from supervisor.seams.compaction.core import is_summary
from supervisor.session.workspaces import session_workspace
from supervisor.temporal.contracts import CreateSession, Message, job_ids
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.sessions import SessionCommandError
from supervisor.temporal.steps import run_id_for
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import FAST, WRITE, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway
from tests.support.workflow_histories import replayable_histories

pytestmark = pytest.mark.asyncio(loop_scope="session")

PINNED_TOOLS = ["echo", "edit", "find", "grep", "ls", "read", "remember", "write"]  # harness/tool_sets/githi-tools
PLATFORM_TOOLS = ["memory", "history", "subagent", "subagent_fork", "subagent_result"]


@dataclasses.dataclass
class Call:
    model: str | None
    tools: list[str]
    messages: list[Any]


def recording(gateway: ScriptedGateway) -> list[Call]:
    """Every agent model call (not memory's one-shots), as the gateway received it."""
    calls: list[Call] = []
    complete = gateway.complete

    def record(
        messages: list[Any], tools: list[dict[str, Any]], *, model: str | None = None
    ) -> Any:
        if gateway._one_shot(messages) is None:
            calls.append(Call(model, [t["function"]["name"] for t in tools], list(messages)))
        return complete(messages, tools, model=model)

    gateway.complete = record  # type: ignore[method-assign]
    return calls


async def _variant(env: Env, catalog: Any, kind: str, base_id: str, **changes: Any) -> DigestRef:
    """Publish (and persist) a copy of a shipped artifact with ``changes``."""
    base = catalog.get(kind, base_id, catalog.list_versions(kind, base_id)[-1])
    name = f"{base_id}-{uuid.uuid4().hex[:6]}"
    offered = catalog.offer(dataclasses.replace(base, name=name, digest="", **changes))
    await persist_offer(env.factory, catalog, kind, offered.name, offered.digest)
    return DigestRef(id=offered.name, digest=offered.digest)


async def create_pinned(
    env: Env,
    state: SupervisorState,
    *,
    memory: dict[str, Any] | None = None,
    sandbox: dict[str, Any] | None = None,
) -> str:
    """A githi-v1 session whose composition pins variant memory and/or sandbox profiles."""
    catalog = state.composition_catalog
    assert catalog is not None
    pins: dict[str, DigestRef] = {}
    if memory is not None:
        pins["memory_profile"] = await _variant(env, catalog, "memory_profile", "default", **memory)
    if sandbox is not None:
        pins["sandbox_profile"] = await _variant(
            env, catalog, "sandbox_profile", "githi-docker-v1", **sandbox
        )
    comp = await _variant(env, catalog, "composition", "githi-v1", **pins)
    sid = uuid.uuid4().hex
    request = CreateSession(
        "t1",
        "githi-v1",
        composition_id=comp.id,
        composition_digest=comp.digest,
        principal_id="alice",
    )
    await env.sessions.create(sid, request, timing=FAST)
    return sid


# --- model call -----------------------------------------------------------------


async def test_every_model_call_is_built_from_the_pins(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("ls", {"path": "."}))
    calls = recording(gateway)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "list the files"))
        await env.wait_for(state, sid, run_ended())
        pins = (await state.manager._get_session_row(sid)).composition_pins

    assert len(calls) == 2
    for call in calls:
        assert call.model == pins["model_route"] == "echo"  # the composition's route
        offered = PINNED_TOOLS + PLATFORM_TOOLS
        pi_first = [n for n in TOOL_ORDER if n in offered]
        assert call.tools == pi_first + [n for n in offered if n not in pi_first]  # pi's order
        system = call.messages[0]
        assert system.role == "system" and "You are an expert coding assistant." in system.content
        assert "<tools>\n- " in system.content and system.content.endswith("<cwd>\n.\n</cwd>")


async def test_a_githi_model_turn_never_runs_outside_the_workflow(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        runtime = state.manager._registry.get(sid)
        with pytest.raises(AgentLoopError, match="driven step by step"):
            async for _ in runtime.loop().run_turn("hi", []):
                pass


# --- sandbox ----------------------------------------------------------------------


async def test_tools_act_in_the_pinned_sandbox(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "write a note"))
        events = await env.wait_for(state, sid, run_ended())
        runtime = state.manager._registry.get(sid)
        sandbox = state.manager._hands.sandbox(sid)

    assert isinstance(runtime.workspace, HandWorkspace)
    (up,) = payloads(events, HandProvisioned)
    assert (up.hand_id, up.cause) == (sandbox.id, "first_use")
    assert payloads(events, ToolEnd)[0].result["ok"] is True
    assert await sandbox.read_file("/workspace/notes.txt") == b"hello"
    assert not (session_workspace(sid) / "notes.txt").exists()  # never the supervisor's disk


async def test_a_sticky_harness_will_not_start_on_a_sandbox_that_may_not_be_kept(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """A composition stored before publish checked this still cannot start a session."""
    model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        with pytest.raises(SessionCommandError) as err:
            await create_pinned(env, state, sandbox={"sticky_eligible": False})
    assert err.value.kind == "CompositionIncompatible"
    assert "sticky_eligible" in str(err.value)


async def test_the_sandbox_profile_policy_governs_model_tool_calls(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(
            env,
            state,
            sandbox={
                "provider": SandboxProviderName.LOCAL_PROCESS,
                "image_digest": f"sha256:{hashlib.sha256(b'test-image').hexdigest()}",
                "file_effects": FileEffects.READ_ONLY,
            },
        )
        await env.sessions.submit(sid, Message("c1", "write a note"))
        events = await env.wait_for(state, sid, run_ended())

    end = payloads(events, ToolEnd)[0]
    assert end.is_error and end.result["code"] == "FILE_EFFECTS_DENIED"
    assert end.result["ok"] is False  # the shape of any tool result (F16)


async def test_a_resumed_session_gets_the_same_pins(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
    fresh = await env.state()  # a new process: nothing cached
    await fresh.manager.resume(sid)
    runtime = fresh.manager._registry.get(sid)
    assert runtime.model == "echo"
    assert isinstance(runtime.workspace, HandWorkspace)


# --- memory -----------------------------------------------------------------------


async def test_the_conversation_is_compacted_by_the_pinned_profile(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    calls = recording(gateway)
    state = await env.state()
    long = "lorem ipsum " * 900  # ~3k tokens
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(
            env,
            state,
            memory={"context_window": 2_000, "reserve_tokens": 100, "keep_recent_tokens": 50},
        )
        await env.sessions.submit(sid, Message("c1", long))
        await env.wait_for(state, sid, run_ended(1))
        await env.sessions.submit(sid, Message("c2", "and now?"))
        events = await env.wait_for(state, sid, run_ended(2))

    end = payloads(events, CompactionEnd)[-1]
    assert end.stop_reason == "ok" and end.messages is not None
    second = calls[-1].messages
    texts = [m.content for m in second]
    assert not any(long.strip() in t for t in texts)  # the long turn was summarised away
    assert any(is_summary(t) for t in texts)


async def test_after_a_compaction_the_model_reopens_a_tool_output_verbatim(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """BH7: compaction is never the only copy. The summary names the journal range it
    replaced, and ``history`` returns a tool result from inside it, verbatim."""
    marker = "".join(f"line {n}: the quick brown fox {n * 7919 % 1000}\n" for n in range(60))
    gateway = model(("write", {"path": "notes.txt", "content": marker}), ("read", {"path": "notes.txt"}))
    calls = recording(gateway)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(
            env,
            state,
            memory={"context_window": 2_000, "reserve_tokens": 100, "keep_recent_tokens": 50},
        )
        await env.sessions.submit(sid, Message("c1", "keep this file: " + "lorem ipsum " * 600))
        first = await env.wait_for(state, sid, run_ended(1))
        read = next(e for e in first if isinstance(e.payload, ToolEnd) and e.payload.tool_name == "read")
        assert read.payload.result["payload"] == marker

        gateway.calls = [("history", {"seq": read.seq})]
        await env.sessions.submit(sid, Message("c2", "what was in that file?"))
        events = await env.wait_for(state, sid, run_ended(2))

    # The compaction that summarised the read away: inside run 1 (its request plus the
    # file twice outgrew the window) or as run 2 began.
    end = next(
        e
        for e in payloads(events, CompactionEnd)
        if e.replaced_to_seq is not None and e.replaced_to_seq >= read.seq
    )
    assert end.stop_reason == "ok"
    assert end.replaced_from_seq is not None and end.replaced_to_seq is not None
    assert end.replaced_from_seq <= read.seq <= end.replaced_to_seq
    # The model was shown the summary, with the range it can reopen ...
    agent = [c for c in calls if c.tools]
    asked = next(c for c in agent if c.messages[-1].content == "what was in that file?")
    shown = [m.content for m in asked.messages]
    assert any(f"#{end.replaced_from_seq}–#{end.replaced_to_seq}" in t for t in shown)
    assert not any(marker in t for t in shown), "the tool output was still in context"
    # ... and the output it reopened reached it, even past a compaction inside the run.
    assert any(marker in str(m.content) for m in agent[-1].messages)
    # ... and reopened the tool output from it, verbatim.
    reopened = payloads(events, ToolEnd)[-1]
    assert reopened.tool_name == "history" and not reopened.is_error
    assert reopened.result["payload"].endswith(f"read [call-0-1]\n{marker}")
    # Replay stays deterministic: the history read is a journalled tool result.
    replayer = Replayer(workflows=[AgentSessionWorkflow, SubagentJobWorkflow], data_converter=pydantic_data_converter)
    query = f"WorkflowType = 'AgentSession' AND TaskQueue = '{env.queue}'"
    async for history in replayable_histories(env.client, query):
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        assert result.replay_failure is None, result.replay_failure


def _run_errors(events: list[Any]) -> list[str]:
    return [e.payload.error_class for e in events if type(e.payload).__name__ == "ErrorEvent"]


async def test_a_run_that_outgrows_the_window_compacts_and_still_answers(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """githi's prepare phase: a step whose request is past the pinned limit is not sent;
    the conversation so far, this run included, is summarised, and the step asks again."""
    big = "".join(f"row {n}: the quick brown fox jumps over the lazy dog {n * 7919 % 1000}\n" for n in range(120))
    gateway = model(("write", {"path": "big.txt", "content": big}), ("read", {"path": "big.txt"}))
    calls = recording(gateway)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(
            env, state, memory={"context_window": 2_000, "reserve_tokens": 100, "keep_recent_tokens": 50}
        )
        await env.sessions.submit(sid, Message("c1", "save this table and read it back"))
        events = await env.wait_for(state, sid, run_ended(1))

    ends = payloads(events, CompactionEnd)
    assert ends and ends[-1].in_run and ends[-1].stop_reason == "ok"
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"  # answered, not cut off
    after = calls[-1].messages  # the request sent after the compaction
    assert not any(big in str(m.content) for m in after), "the read output was still sent"
    assert any(is_summary(str(m.content)) or "summary" in str(m.content).lower() for m in after)


BIG = "".join(f"row {n}: the quick brown fox jumps over the lazy dog {n * 7919 % 1000}\n" for n in range(120))


async def test_a_summary_the_provider_rate_limits_is_asked_again(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """Pi Durable's compaction retry: a summary call that may pass (a 429) is asked again
    after a wait, and the compaction completes as if nothing had happened."""
    gateway = model(("write", {"path": "big.txt", "content": BIG}), ("read", {"path": "big.txt"}))
    gateway.summary_errors = [RuntimeError("model gateway error 429: rate limited")]
    calls = recording(gateway)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(
            env, state, memory={"context_window": 2_000, "reserve_tokens": 100, "keep_recent_tokens": 50}
        )
        await env.sessions.submit(sid, Message("c1", "save this table and read it back"))
        events = await env.wait_for(state, sid, run_ended(1))

    assert gateway.summary_errors == []  # the 429 was met ...
    (end,) = payloads(events, CompactionEnd)
    assert end.in_run and end.stop_reason == "ok" and end.error is None  # ... and asked again
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
    assert not any(BIG in str(m.content) for m in calls[-1].messages)


async def test_a_summary_that_fails_keeps_the_conversation_whole(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """A summary that will fail the same way (a refused key) fails the compaction, saying
    why; nothing the model did is dropped behind a notice, and the run goes on."""
    gateway = model(("write", {"path": "big.txt", "content": BIG}), ("read", {"path": "big.txt"}))
    gateway.summary_errors = [RuntimeError("model gateway error 401: invalid api key")]
    calls = recording(gateway)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(
            env, state, memory={"context_window": 2_000, "reserve_tokens": 100, "keep_recent_tokens": 50}
        )
        await env.sessions.submit(sid, Message("c1", "save this table and read it back"))
        events = await env.wait_for(state, sid, run_ended(1))

    (end,) = payloads(events, CompactionEnd)
    assert end.in_run and end.stop_reason == "error" and end.messages is None
    assert end.error is not None and "invalid api key" in end.error
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
    after = calls[-1].messages  # the request after the failed compaction: all of it
    assert any(BIG in str(m.content) for m in after)
    assert not any(is_summary(str(m.content)) for m in after)
    assert _run_errors(events) == []


async def test_a_summary_that_fails_between_runs_carries_the_whole_conversation(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(window=2_000)
    gateway.summary_errors = [RuntimeError("model gateway error 400: bad request")]
    calls = recording(gateway)
    long = "lorem ipsum " * 900
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(env, state, memory={"reserve_tokens": 100, "keep_recent_tokens": 50})
        await env.sessions.submit(sid, Message("c1", long))
        await env.wait_for(state, sid, run_ended(1))
        await env.sessions.submit(sid, Message("c2", "and now?"))
        events = await env.wait_for(state, sid, run_ended(2))

    (end,) = payloads(events, CompactionEnd)
    assert not end.in_run and end.stop_reason == "error" and end.messages is None
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
    assert any(long.strip() in str(m.content) for m in calls[-1].messages)


async def test_a_provider_overflow_is_compacted_once_and_the_step_asked_again(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("write", {"path": "a.txt", "content": "x" * 400}), ("read", {"path": "a.txt"}))
    gateway.overflow_calls = {1}  # the step after both tools ran
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(env, state, memory={"keep_recent_tokens": 20})
        await env.sessions.submit(sid, Message("c1", "write it and read it " + "please " * 40))
        events = await env.wait_for(state, sid, run_ended(1))

    assert [e.in_run for e in payloads(events, CompactionEnd)] == [True]
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
    assert _run_errors(events) == []


async def test_a_summarys_spend_inside_a_run_counts_like_a_model_steps(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """Each scripted call spends 15 tokens and $0.01. The summary of a compaction inside the
    run is journalled with what it spent, counted toward the session's tokens and charged
    to its cost, like the model steps around it."""
    gateway = model(("write", {"path": "a.txt", "content": "x" * 400}), ("read", {"path": "a.txt"}))
    gateway.overflow_calls = {1}  # the step after both tools ran: compacted in the run
    gateway.cost_per_call = 0.01
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(env, state, memory={"keep_recent_tokens": 20})
        await env.sessions.submit(sid, Message("c1", "write it and read it " + "please " * 40))
        events = await env.wait_for(state, sid, run_ended(1))
        status = await env.sessions.status(sid)
        spent = await state.manager._budget.check(sid)

    (end,) = payloads(events, CompactionEnd)
    assert end.in_run and end.usage is not None and end.usage.total_tokens == 15
    assert status.tokens_used == 3 * 15, "two model steps and the summary"
    assert spent.total_cost_usd == Decimal("0.03")


async def test_a_summarys_spend_between_runs_counts_toward_the_next_run(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(window=2_000)  # ~3k tokens carried into the second run: compacted as it opens
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(env, state, memory={"reserve_tokens": 100, "keep_recent_tokens": 50})
        await env.sessions.submit(sid, Message("c1", "lorem ipsum " * 900))
        await env.wait_for(state, sid, run_ended(1))
        await env.sessions.submit(sid, Message("c2", "and now?"))
        events = await env.wait_for(state, sid, run_ended(2))
        status = await env.sessions.status(sid)

    (end,) = payloads(events, CompactionEnd)
    assert not end.in_run and end.usage is not None and end.usage.total_tokens == 15
    assert status.tokens_used == 3 * 15, "a model step each run, and the summary"


async def test_a_step_that_still_overflows_after_compacting_ends_the_run_saying_why(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("write", {"path": "a.txt", "content": "x" * 400}), ("read", {"path": "a.txt"}))
    gateway.overflow_calls = {1, 2}
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(env, state, memory={"keep_recent_tokens": 20})
        await env.sessions.submit(sid, Message("c1", "write it and read it " + "please " * 40))
        events = await env.wait_for(state, sid, run_ended(1))
        # The session stays usable: the next message runs.
        gateway.overflow_calls = set()
        await env.sessions.submit(sid, Message("c2", "try again"))
        events = await env.wait_for(state, sid, run_ended(2))

    assert "ContextWindowFull" in _run_errors(events)
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"


@pytest.mark.parametrize(
    ("reported", "compacted"),
    [(2_000, True), (None, False)],
    ids=["gateway-reports-a-small-window", "gateway-cannot-say"],
)
async def test_without_a_pinned_window_the_gateway_reports_it(
    env: Env, model: Callable[..., ScriptedGateway], reported: int | None, compacted: bool
) -> None:
    model(window=reported)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(
            env, state, memory={"reserve_tokens": 100, "keep_recent_tokens": 50}
        )
        await env.sessions.submit(sid, Message("c1", "lorem ipsum " * 900))
        await env.wait_for(state, sid, run_ended(1))
        await env.sessions.submit(sid, Message("c2", "and now?"))
        events = await env.wait_for(state, sid, run_ended(2))

    # ~3k tokens: past a reported 2k window, far inside the 200k default.
    assert bool(payloads(events, CompactionEnd)) is compacted


async def test_recall_comes_from_the_pinned_backend(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model()
    calls = recording(gateway)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_pinned(
            env,
            state,
            memory={"recall_enabled": True, "recall_backend": RecallBackend.GITHI_MARKDOWN},
        )
        session_workspace(sid).joinpath("MEMORY.md").write_text(
            "- The deploy key rotates every Friday\n- Lunch is at noon\n"
        )
        await env.sessions.submit(sid, Message("c1", "the deploy key rotates on which day"))
        events = await env.wait_for(state, sid, run_ended())

    recall = payloads(events, MemoryRecall)[-1]
    assert recall.injected and "deploy key rotates every Friday" in (recall.text or "")
    shown = [m.content for m in calls[0].messages if m.role == "system"]
    assert any("deploy key rotates every Friday" in t for t in shown)


# --- subagents --------------------------------------------------------------------


async def test_githi_delegates_to_a_child_session(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """A foreground subagent is a session of its own (supervisor.session.children), and its
    answer is the call's result."""
    model(("subagent", {"prompt": "summarise the repo", "description": "summary"}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "delegate it"))
        events = await env.wait_for(state, sid, run_ended())

    start, end = payloads(events, SubagentStart)[0], payloads(events, SubagentEnd)[0]
    assert start.provider == "session" and start.label == "summary"
    tool = payloads(events, ToolEnd)[0]
    assert tool.result["ok"] and tool.result["payload"]["text"] == end.output


async def test_a_background_subagent_runs_as_a_durable_job(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("subagent", {"prompt": "long task", "background": True}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "start it"))
        events = await env.wait_for(state, sid, run_ended())
        started = payloads(events, ToolEnd)[0].result["payload"]
        job_id, _ = job_ids(f"{run_id_for(sid, 'c1')}:call-0-0")
        assert started == {"job_id": job_id, "status": "started"}
        events = await env.wait_for(
            state, sid, lambda evs: any(isinstance(e.payload, JobEnd) for e in evs)
        )
        gateway.calls = [("subagent_result", {"job_id": job_id})]
        await env.sessions.submit(sid, Message("c2", "is it done?"))
        events = await env.wait_for(state, sid, run_ended(2))

    result = payloads(events, ToolEnd)[-1].result
    job = payloads(events, JobEnd)[0]
    assert result["ok"] and result["payload"] == json.loads(
        json.dumps({"job_id": job_id, "status": job.stop_reason, "text": job.output})
    )


async def test_a_subagents_model_call_counts_toward_the_sessions_cost(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """The child's spend is its session's too: two model steps and the child's one call,
    $0.01 each (it used to cost the session nothing)."""
    model(("subagent", {"prompt": "summarise the repo"}), cost_per_call=0.01)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "delegate it"))
        events = await env.wait_for(state, sid, run_ended())
        spent = await state.manager._budget.check(sid)

    assert payloads(events, SubagentStart)[0].provider == "session"
    assert spent.total_cost_usd == Decimal("0.03")


async def test_a_background_subagents_model_call_counts_toward_the_sessions_cost(
    env: Env, model: Callable[..., ScriptedGateway], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOW_LAB_ECHO", "0")
    model(("subagent", {"prompt": "long task", "background": True}), cost_per_call=0.01)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "start it"))
        await env.wait_for(state, sid, run_ended())
        await env.wait_for(state, sid, lambda evs: any(isinstance(e.payload, JobEnd) for e in evs))
        spent = await state.manager._budget.check(sid)

    assert spent.total_cost_usd == Decimal("0.03"), "two model steps and the job's call"


async def test_a_finished_background_subagent_is_announced_once(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("subagent", {"prompt": "long task", "background": True}))
    calls = recording(gateway)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "start it"))
        await env.wait_for(state, sid, run_ended())
        await env.wait_for(state, sid, lambda evs: any(isinstance(e.payload, JobEnd) for e in evs))
        gateway.calls = []
        await env.sessions.submit(sid, Message("c2", "anything new?"))
        await env.wait_for(state, sid, run_ended(2))
        await env.sessions.submit(sid, Message("c3", "and now?"))
        events = await env.wait_for(state, sid, run_ended(3))

    job_id, _ = job_ids(f"{run_id_for(sid, 'c1')}:call-0-0")
    (announced,) = payloads(events, BackgroundResults)  # once, not again on the third turn
    assert [r["job_id"] for r in announced.results] == [job_id]
    told = [m for m in calls[-2].messages if m.role == "system" and job_id in m.content]
    assert told and told[0].content.startswith("Background subagents you started have finished")
