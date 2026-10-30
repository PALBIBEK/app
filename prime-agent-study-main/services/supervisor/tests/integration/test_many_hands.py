"""BH8: a session holds several named hands, each its own sandbox.

A composition pins a default sandbox profile and a named hand (``build``). The
model is offered the session's hands on every sandbox tool, and one turn writes
to both. Each hand is built only when a call needs it, keeps its own files and
checkpoints, and is named in the journal.
"""

from __future__ import annotations

import dataclasses
import hashlib
import threading
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from supervisor.agent.events import HandCheckpointed, HandProvisioned, JobEnd, ToolEnd, ToolStart
from supervisor.app import SupervisorState
from supervisor.catalog.core.types import DigestRef, SandboxProviderName
from supervisor.catalog.repository import persist_offer
from supervisor.hands.keys import hand_key
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.temporal import subagent_tools
from supervisor.temporal.contracts import CreateSession, Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import FAST, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

LOCAL: dict[str, Any] = {
    "provider": SandboxProviderName.LOCAL_PROCESS,
    "image_digest": f"sha256:{hashlib.sha256(b'test-image').hexdigest()}",
}


async def _offer(env: Env, catalog: Any, kind: str, base_id: str, **changes: Any) -> DigestRef:
    base = catalog.get(kind, base_id, catalog.list_versions(kind, base_id)[-1])
    offered = catalog.offer(
        dataclasses.replace(base, name=f"{base_id}-{uuid.uuid4().hex[:6]}", digest="", **changes)
    )
    await persist_offer(env.factory, catalog, kind, offered.name, offered.digest)
    return DigestRef(id=offered.name, digest=offered.digest)


async def create_with_hands(env: Env, state: SupervisorState, *hands: str) -> str:
    """githi-v1 with a default sandbox and the named ``hands`` (each its own profile)."""
    catalog = state.composition_catalog
    assert catalog is not None
    default = await _offer(env, catalog, "sandbox_profile", "githi-docker-v1", **LOCAL)
    named = tuple([(name, await _offer(env, catalog, "sandbox_profile", "githi-docker-v1", **LOCAL)) for name in hands])
    comp = await _offer(env, catalog, "composition", "githi-v1", sandbox_profile=default, hands=named)
    sid = uuid.uuid4().hex
    await env.sessions.create(
        sid,
        CreateSession("t1", "githi-v1", composition_id=comp.id, composition_digest=comp.digest, principal_id="alice"),
        timing=FAST,
    )
    return sid


async def test_one_turn_uses_two_hands(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    gateway = model(
        ("write", {"path": "app.txt", "content": "in the default hand"}),
        ("write", {"path": "app.txt", "content": "in the build hand", "hand": "build"}),
    )
    state = await env.state(workspace_store=MemoryWorkspaceStore(objects=MemoryObjectStorage()))
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_with_hands(env, state, "build")
        assert state.manager.hand_names(sid) == ["default", "build"]
        await env.sessions.submit(sid, Message("c1", "write in both"))
        events = await env.wait_for(state, sid, run_ended())

        default = state.manager._hands.sandbox(sid)
        build = state.manager._hands.sandbox(hand_key(sid, "build"))
        assert default is not None and build is not None and default.id != build.id
        assert await default.read_file("app.txt") == b"in the default hand"
        assert await build.read_file("app.txt") == b"in the build hand"

    # The model was offered both hands on a sandbox tool, and only there.
    tools = {t["function"]["name"]: t["function"]["parameters"] for t in gateway.tools_seen[0]}
    assert tools["write"]["properties"]["hand"]["enum"] == ["default", "build"]
    assert "hand" not in tools["remember"]["properties"]
    assert not any(e.is_error for e in payloads(events, ToolEnd))
    assert sorted(h.hand for h in payloads(events, HandProvisioned)) == ["build", "default"]
    assert sorted(h.hand for h in payloads(events, HandCheckpointed)) == ["build", "default"]


async def test_a_hand_the_session_does_not_have_is_the_models_to_read(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(("write", {"path": "x.txt", "content": "x", "hand": "gpu"}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_with_hands(env, state, "build")
        await env.sessions.submit(sid, Message("c1", "write on the gpu"))
        events = await env.wait_for(state, sid, run_ended())

    end = payloads(events, ToolEnd)[0]
    assert end.is_error and end.result["code"] == "UNKNOWN_HAND"
    assert "default, build" in end.result["error"]
    assert not payloads(events, HandProvisioned)  # nothing was built for it


async def test_a_hand_is_lent_to_a_background_subagent_and_comes_back(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """The Phase 4 gate: two hands in one turn, one lent to a background job, and back."""
    gate = threading.Event()
    gateway = model(
        ("write", {"path": "notes.txt", "content": "the session's own"}),
        ("subagent", {"prompt": "build it", "background": True, "hand": "build"}),
        child_calls=[("write", {"path": "built.txt", "content": "made by the job"})],
        child_answer="built it",
        child_gate=gate,
    )
    state = await env.state(workspace_store=MemoryWorkspaceStore(objects=MemoryObjectStorage()))
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_with_hands(env, state, "build")
        await env.sessions.submit(sid, Message("c1", "write a note, and have a job build"))
        first = await env.wait_for(state, sid, run_ended(1))
        # The write and the subagent run at once (different lanes): find it by name.
        (spawn,) = [e for e in payloads(first, ToolEnd) if e.tool_name == "subagent"]
        started = spawn.result["payload"]
        assert started["status"] == "started" and started["hand"] == "build"

        # While the job holds it, the session's own call on that hand is refused.
        gateway.calls = [("write", {"path": "x.txt", "content": "x", "hand": "build"})]
        await env.sessions.submit(sid, Message("c2", "write in build"))
        second = await env.wait_for(state, sid, run_ended(2))
        refused = payloads(second, ToolEnd)[-1]
        assert refused.is_error and refused.result["code"] == "HAND_LENT"

        gate.set()  # the job works in the borrowed hand, answers, and gives it back
        ended = await env.wait_for(state, sid, lambda evs: any(isinstance(e.payload, JobEnd) for e in evs))
        job = payloads(ended, JobEnd)[0]
        assert (job.stop_reason, job.output) == ("ok", "built it")
        assert await state.manager._leases.current(hand_key(sid, "build")) is None

        gateway.calls = [("read", {"path": "built.txt", "hand": "build"})]
        await env.sessions.submit(sid, Message("c3", "what did it build?"))
        third = await env.wait_for(state, sid, run_ended(3))

    back = payloads(third, ToolEnd)[-1]
    assert not back.is_error and back.result["payload"] == "made by the job"
    # The job's call ran in the session, journalled like any call, fenced by the lease.
    job_calls = [e for e in payloads(third, ToolEnd) if e.tool_call_id.startswith(job.job_id)]
    assert [e.tool_name for e in job_calls] == ["write"] and not job_calls[0].is_error
    default = state.manager._hands.sandbox(sid)
    assert default is not None and not await default.exists("built.txt")  # it stayed in its hand


async def test_a_background_job_retried_on_another_worker_resumes_from_the_journal(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """A job whose worker is lost after its first call ran is retried, and goes on from
    what it did: the model is shown that call and its result, and no call runs twice.

    It used to start over: the model was asked from the beginning, and a call it asked
    for at a used place got the earlier call's result whatever it asked."""
    gateway = model(
        ("subagent", {"prompt": "build it", "background": True, "hand": "build"}),
        child_calls=[
            ("write", {"path": "one.txt", "content": "first"}),
            ("write", {"path": "two.txt", "content": "second"}),
        ],
        child_answer="built both",
        child_fail_after=1,  # lost after the first call's result came back
    )
    state = await env.state(workspace_store=MemoryWorkspaceStore(objects=MemoryObjectStorage()))
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_with_hands(env, state, "build")
        await env.sessions.submit(sid, Message("c1", "have a job build"))
        await env.wait_for(state, sid, run_ended(1))
        ended = await env.wait_for(state, sid, lambda evs: any(isinstance(e.payload, JobEnd) for e in evs))
        build = state.manager._hands.sandbox(hand_key(sid, "build"))
        assert build is not None
        assert await build.read_file("one.txt") == b"first"
        assert await build.read_file("two.txt") == b"second"

    job = payloads(ended, JobEnd)[0]
    assert (job.stop_reason, job.output) == ("ok", "built both")
    starts = [e for e in payloads(ended, ToolStart) if e.tool_call_id.startswith(job.job_id)]
    assert [(e.tool_name, e.args["path"]) for e in starts] == [("write", "one.txt"), ("write", "two.txt")]
    # Requests: step 0; step 1 (lost); the retry's first, already showing the first
    # call and its result; then the answer.
    tools_shown = [[m.tool_call_id for m in seen if m.role == "tool"] for seen in gateway.child_seen]
    assert tools_shown == [[], [starts[0].tool_call_id], [starts[0].tool_call_id], [e.tool_call_id for e in starts]]


async def test_a_session_runs_at_most_so_many_background_jobs_at_once(
    env: Env, model: Callable[..., ScriptedGateway], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each background job spends on the session's model until it ends: one past the
    limit is refused, and the model reads why."""
    monkeypatch.setattr(subagent_tools, "MAX_BACKGROUND_JOBS", 1)
    gate = threading.Event()  # the first job runs until the test lets it end
    model(
        ("subagent", {"prompt": "build it", "background": True, "hand": "build"}),
        ("subagent", {"prompt": "and this too", "background": True}),
        child_gate=gate,
    )
    state = await env.state(workspace_store=MemoryWorkspaceStore(objects=MemoryObjectStorage()))
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_with_hands(env, state, "build")
        await env.sessions.submit(sid, Message("c1", "start two jobs"))
        first = await env.wait_for(state, sid, run_ended(1))
        gate.set()
        await env.wait_for(state, sid, lambda evs: any(isinstance(e.payload, JobEnd) for e in evs))

    started, refused = [e for e in payloads(first, ToolEnd) if e.tool_name == "subagent"]
    assert started.result["payload"]["status"] == "started"
    assert refused.is_error and refused.result["code"] == "TOO_MANY_JOBS"
    assert "wait for one to end" in refused.result["error"]


async def test_a_background_jobs_model_calls_count_toward_the_sessions_cost(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """The job thinks with the session's model: two steps of the run that started it and
    two of the job's own (a call, then its answer), $0.01 each."""
    model(
        ("subagent", {"prompt": "build it", "background": True, "hand": "build"}),
        child_calls=[("write", {"path": "built.txt", "content": "made by the job"})],
        cost_per_call=0.01,
    )
    state = await env.state(workspace_store=MemoryWorkspaceStore(objects=MemoryObjectStorage()))
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_with_hands(env, state, "build")
        await env.sessions.submit(sid, Message("c1", "have a job build"))
        await env.wait_for(state, sid, run_ended(1))
        await env.wait_for(state, sid, lambda evs: any(isinstance(e.payload, JobEnd) for e in evs))
        spent = await state.manager._budget.check(sid)

    assert spent.total_cost_usd == Decimal("0.04")
