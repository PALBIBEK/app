"""D6 §5 E / T9 — approvals thin gate: awaiting_approval blocks invoke/message until respond."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from supervisor.agent.events import ApprovalRequired
from supervisor.agent.factory import create_runtime
from supervisor.catalog.store import CompositionCatalog
from supervisor.session.manager import SessionAwaitingApproval
from tests.unit.test_catalog_pins import _offer_composition


# CompositionCatalog / _offer_composition used by manager-level tests below.


@pytest.mark.asyncio
async def test_t9_awaiting_approval_blocks_invoke_until_respond(session_env) -> None:
    """T9 / §5 E: pending approval → invoke raises; respond → active again."""
    cat = CompositionCatalog()
    # approval_default=ask (default) so ApprovalRequired parks the session.
    comp, _, _ = _offer_composition(cat, name="d6-approve-comp")
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="coding-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )

    await m._handle_approval_required(
        sid,
        "turn-d6",
        ApprovalRequired(
            approval_id="",
            prompt="allow dangerous tool?",
            options=["yes", "no"],
            timeout_s=120,
        ),
    )
    row = await m._get_session_row(sid)
    assert row.status == "awaiting_approval"

    with pytest.raises(SessionAwaitingApproval):
        await m.invoke_tool(
            sid, tenant_id="t1", name="echo", arguments={"text": "blocked"}
        )

    pending = await m.pending_approvals(sid)
    assert pending, "expected at least one pending approval"
    approval_id = pending[0].approval_id
    await m.respond_approval(approval_id, "yes")
    row2 = await m._get_session_row(sid)
    assert row2.status == "active"

    ok = await m.invoke_tool(
        sid, tenant_id="t1", name="echo", arguments={"text": "after"}, tool_call_id="tc-after"
    )
    assert ok.result.ok is True
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_t9_http_409_while_awaiting_approval(supervisor_stack, shipped_catalog) -> None:
    """T9 HTTP: message/invoke return 409 while awaiting_approval."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
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

    await state.manager._handle_approval_required(
        sid,
        "turn-http",
        ApprovalRequired(
            approval_id="",
            prompt="gate",
            options=["yes", "no"],
            timeout_s=60,
        ),
    )

    inv = await client.post(
        f"/v1/sessions/{sid}/tools/invoke",
        headers=headers,
        json={"name": "echo", "arguments": {"text": "x"}},
    )
    assert inv.status_code == 409, inv.text

    msg = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=headers,
        json={"text": "hi", "command_id": "c-d6"},
    )
    assert msg.status_code == 409, msg.text

    listed = await client.get(f"/v1/sessions/{sid}/approvals", headers=headers)
    assert listed.status_code == 200
    approvals = listed.json()
    assert approvals, "UI/BFF must be able to list pending approvals"
    approval_id = approvals[0]["approval_id"]

    resp = await client.post(
        f"/v1/approvals/{approval_id}",
        headers=headers,
        json={"choice": "yes"},
    )
    assert resp.status_code == 200, resp.text

    inv2 = await client.post(
        f"/v1/sessions/{sid}/tools/invoke",
        headers=headers,
        json={"name": "echo", "arguments": {"text": "ok"}, "tool_call_id": "tc-resume"},
    )
    assert inv2.status_code == 200, inv2.text
    assert inv2.json()["ok"] is True


def test_e_require_approval_gate_surface_exists() -> None:
    """§5 E thin: SessionManager must expose a per-tool require_approval gate.

    D6 wires harness ``approvals.require_approval`` (or sandbox) so named tools
    emit ApprovalRequired before invoke — not silent execute.
    """
    from supervisor.session.manager import SessionManager

    assert hasattr(SessionManager, "check_tool_approval_required") or hasattr(
        SessionManager, "require_approval_for_tool"
    ), (
        "D6 §E: add SessionManager.check_tool_approval_required "
        "(or require_approval_for_tool) and call it before provider.invoke"
    )
