"""D6 §5 B / T3–T4 — HTTP tools/invoke outbox idempotency + no-composition 409."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from supervisor.agent.events import ToolEnd, ToolStart
from supervisor.agent.factory import create_runtime
from supervisor.catalog.store import CompositionCatalog
from supervisor.session.manager import ToolInvokeNotConfigured
from tests.unit.test_catalog_pins import _offer_composition


@pytest.mark.asyncio
async def test_t3_http_invoke_same_tool_call_id_cached_once(session_env) -> None:
    """T3: duplicate tool_call_id → cached outbox; one ToolStart/ToolEnd pair."""
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat, name="d6-outbox-comp")
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="coding-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )

    seams = m._seams_for_session(sid, await m._require_active(sid))
    assert seams is not None
    real_invoke = seams.tool_provider.invoke
    calls: list[str] = []

    def counting_invoke(ctx, name, arguments):  # type: ignore[no-untyped-def]
        calls.append(name)
        return real_invoke(ctx, name, arguments)

    seams.tool_provider.invoke = counting_invoke  # type: ignore[method-assign]

    first = await m.invoke_tool(
        sid,
        tenant_id="t1",
        name="echo",
        arguments={"text": "idem"},
        tool_call_id="d6-tc-dup",
    )
    second = await m.invoke_tool(
        sid,
        tenant_id="t1",
        name="echo",
        arguments={"text": "idem"},
        tool_call_id="d6-tc-dup",
    )
    assert first.result.ok and second.result.ok
    assert first.result.payload == second.result.payload == {"text": "idem"}
    assert len(calls) == 1

    events = await session_env["reader"].read(sid, since=-1)
    assert sum(1 for e in events if isinstance(e.payload, ToolStart)) == 1
    assert sum(1 for e in events if isinstance(e.payload, ToolEnd)) == 1
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_t3_api_duplicate_invoke_replay(supervisor_stack, shipped_catalog) -> None:
    """T3 (HTTP): POST tools/invoke twice with same tool_call_id returns same payload."""
    client = supervisor_stack["client"]
    headers = {"X-Tenant-Id": "d6-tenant-a"}
    create = await client.post(
        "/v1/sessions",
        headers=headers,
        json={
            "harness_version": "coding-v1",
            "runtime": "echo",
            "composition_id": "coding-v1",
        },
    )
    assert create.status_code == 201, create.text
    sid = create.json()["session_id"]
    body = {
        "name": "echo",
        "arguments": {"text": "rpc-once"},
        "tool_call_id": "d6-http-dup",
    }
    r1 = await client.post(f"/v1/sessions/{sid}/tools/invoke", headers=headers, json=body)
    r2 = await client.post(f"/v1/sessions/{sid}/tools/invoke", headers=headers, json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["payload"] == r2.json()["payload"] == {"text": "rpc-once"}
    assert r1.json()["ok"] is True and r2.json()["ok"] is True


@pytest.mark.asyncio
async def test_t4_invoke_without_composition_409(supervisor_stack) -> None:
    """T4: invoke without composition → 409 ToolInvokeNotConfigured."""
    client = supervisor_stack["client"]
    headers = {"X-Tenant-Id": "d6-tenant-a"}
    create = await client.post(
        "/v1/sessions",
        headers=headers,
        json={"harness_version": "kg-v1", "runtime": "echo"},
    )
    assert create.status_code == 201, create.text
    sid = create.json()["session_id"]
    inv = await client.post(
        f"/v1/sessions/{sid}/tools/invoke",
        headers=headers,
        json={"name": "echo", "arguments": {"text": "x"}},
    )
    assert inv.status_code == 409, inv.text


@pytest.mark.asyncio
async def test_t4_manager_raises_tool_invoke_not_configured(session_env) -> None:
    m = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    with pytest.raises(ToolInvokeNotConfigured):
        await m.invoke_tool(sid, tenant_id="t1", name="echo", arguments={})
    await m.terminate(sid)
