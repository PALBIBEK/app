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
from typing import Any

import pytest

from supervisor.agent.events import (
    BackgroundResults,
    CompactionEnd,
    JobEnd,
    MemoryRecall,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
)
from supervisor.agent.runtimes.githi.loop import AgentLoopError
from supervisor.app import SupervisorState
from supervisor.catalog.core.types import (
    DigestRef,
    FileEffects,
    RecallBackend,
    SandboxProviderName,
)
from supervisor.catalog.repository import persist_offer
from supervisor.seams.compaction import SUMMARY_MARKER
from supervisor.session.workspaces import session_workspace
from supervisor.temporal.contracts import CreateSession, Message, job_ids
from supervisor.temporal.steps import run_id_for
from supervisor.temporal.worker import session_worker
from supervisor.tools import SandboxWorkspace
from tests.support.githi_env import FAST, WRITE, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")

PINNED_TOOLS = ["echo", "grep", "ls", "read", "remember", "write"]  # harness/tool_sets/githi-tools
PLATFORM_TOOLS = ["memory", "history", "subagent", "subagent_result"]


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
        assert call.tools == PINNED_TOOLS + PLATFORM_TOOLS
        system = call.messages[0]
        assert system.role == "system" and "You are a coding agent." in system.content


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
        sandbox = state.manager._sandboxes[sid]

    assert isinstance(runtime.workspace, SandboxWorkspace)
    assert payloads(events, ToolEnd)[0].result["ok"] is True
    assert await sandbox.read_file("/workspace/notes.txt") == b"hello"
    assert not (session_workspace(sid) / "notes.txt").exists()  # never the supervisor's disk


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
    assert isinstance(runtime.workspace, SandboxWorkspace)


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
    assert any(t.startswith(SUMMARY_MARKER) for t in texts)


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


async def test_githi_delegates_through_the_pinned_transport(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(("subagent", {"prompt": "summarise the repo", "description": "summary"}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "delegate it"))
        events = await env.wait_for(state, sid, run_ended())
        runtime = state.manager._registry.get(sid)
        default = runtime.seams.subagent_transport.default_provider

    start, end = payloads(events, SubagentStart)[0], payloads(events, SubagentEnd)[0]
    assert start.provider == default and start.label == "summary"
    tool = payloads(events, ToolEnd)[0]
    assert tool.result["ok"] and tool.result["payload"]["status"] == end.stop_reason


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
