"""Session create/resume wiring for agent-core-runtime (I1–I5, A2)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import patch

import pytest

from supervisor.agent.events import RunEnd
from supervisor.agent.factory import create_runtime
from supervisor.db.models import SessionRow
from supervisor.harness import ApprovalSpec, IncompatibleHarness, RuntimeCapabilities
from supervisor.harness.services.compat_checker import check
from supervisor.session.manager import SessionManager
from supervisor.session.registry import SessionRegistry
from tests.support.session_env import make_catalog, make_manifest


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
async def test_i2_coding_echo_pins_harness(session_env) -> None:
    m: SessionManager = session_env["manager"]
    sid = await m.create(
        tenant_id="t1", harness_version="coding-v1", runtime_name="echo"
    )
    async with session_env["factory"]() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        assert row.harness_version == "coding-v1"
        assert row.manifest is not None
        assert row.manifest.get("harness_version") == "coding-v1"
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_i3_create_forwards_manifest_model(session_env) -> None:
    catalog = session_env["catalog"]
    coded = catalog.resolve("coding-v1")
    with_model = replace(coded, model="manifest/model-hint")
    catalog.register(with_model)

    captured: dict = {}

    def spy(name: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["name"] = name
        captured["kwargs"] = kwargs
        return create_runtime(name, **kwargs)

    m: SessionManager = session_env["manager"]
    with patch("supervisor.session.manager.create_runtime", side_effect=spy):
        sid = await m.create(
            tenant_id="t1", harness_version="coding-v1", runtime_name="echo"
        )
    assert captured["name"] == "echo"
    assert captured["kwargs"].get("model") == "manifest/model-hint"
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


@pytest.mark.asyncio
async def test_a2_concurrent_echo_sessions_independent(session_env) -> None:
    m: SessionManager = session_env["manager"]

    async def one(i: int) -> str:
        return await m.create(
            tenant_id="t1", harness_version="kg-v1", runtime_name="echo"
        )

    sids = await asyncio.gather(*[one(i) for i in range(8)])
    assert len(set(sids)) == 8
    states = []
    for sid in sids:
        rt = session_env["registry"].get(sid)
        assert rt is not None
        states.append(await rt.get_state())
    session_ids = {s.session_id for s in states}
    assert len(session_ids) == 8
    for sid in sids:
        await m.terminate(sid)


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
