"""BH8: a session holds several named hands, each its own sandbox.

A composition pins a default sandbox profile and a named hand (``build``). The
model is offered the session's hands on every sandbox tool, and one turn writes
to both. Each hand is built only when a call needs it, keeps its own files and
checkpoints, and is named in the journal.
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from collections.abc import Callable
from typing import Any

from supervisor.agent.events import HandCheckpointed, HandProvisioned, ToolEnd
from supervisor.app import SupervisorState
from supervisor.catalog.core.types import DigestRef, SandboxProviderName
from supervisor.catalog.repository import persist_offer
from supervisor.hands.keys import hand_key
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
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
