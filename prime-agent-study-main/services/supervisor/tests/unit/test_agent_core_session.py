"""Session create/resume wiring for agent-core-runtime (I1–I5, A2)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from supervisor.agent.events import RunEnd
from supervisor.db.models import SessionRow
from supervisor.harness import ApprovalSpec, IncompatibleHarness, RuntimeCapabilities
from supervisor.harness.services.compat_checker import check
from supervisor.session.manager import SessionManager
from tests.support.session_env import make_manifest


@pytest.mark.asyncio
async def test_i1_echo_kg_no_sandbox(session_env) -> None:
    m: SessionManager = session_env["manager"]
    provider = session_env["provider"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    assert provider.create_calls == []
    q = m.live_queue(sid)
    await m.send(sid, "hello", command_id="i1")
    saw_end = False
    for _ in range(50):
        env = await asyncio.wait_for(q.get(), timeout=1)
        if isinstance(env.payload, RunEnd):
            saw_end = True
            break
    assert saw_end
    await m.terminate(sid)






@pytest.mark.asyncio
async def test_i4_resume_recreates_via_factory(session_env) -> None:
    m: SessionManager = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    session_env["registry"].remove(sid)
    result = await m.resume(sid)
    assert result.session_id == sid
    assert session_env["registry"].get(sid) is not None
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_i5_runtime_override_without_editing_protocol(session_env) -> None:
    m: SessionManager = session_env["manager"]
    a = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    b = await m.create(tenant_id="t1", harness_version="kg-v1")  # manifest default echo
    async with session_env["factory"]() as db:
        ra = await db.get(SessionRow, a)
        rb = await db.get(SessionRow, b)
        assert ra is not None and rb is not None
        assert ra.runtime == "echo"
        assert rb.runtime == "echo"
    await m.terminate(a)
    await m.terminate(b)




def test_c3_ghost_runtime_refused() -> None:
    m = make_manifest("ghost-h", requires_sandbox=False, image_digest=None, runtime="ghost")
    with pytest.raises(IncompatibleHarness, match="unregistered runtime"):
        check(m)


def test_n1_unsupported_protocol_refused() -> None:
    m = replace(
        make_manifest("bad", requires_sandbox=False, image_digest=None),
        protocol_version=99,
    )
    with pytest.raises(IncompatibleHarness, match="protocol_version=99"):
        check(m)


def test_n5_approvals_required_but_runtime_caps_false() -> None:
    m = replace(
        make_manifest("kg-v1", requires_sandbox=False, image_digest=None),
        approvals=ApprovalSpec(required=True, timeout_s=30, on_timeout="deny"),
    )
    caps = RuntimeCapabilities(
        protocol_versions={1, 2},
        commands={"prompt", "abort", "get_state", "get_history"},
        supports_approvals=False,
    )
    with pytest.raises(IncompatibleHarness, match="approvals"):
        check(m, caps)
