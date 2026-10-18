"""D8 §5 B / T9–T10 — pre-invoke require_approval gate."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from supervisor.db.engine import open_db
from supervisor.harness import ApprovalSpec, HarnessCatalog
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.policy.approvals import ApprovalBroker
from supervisor.seams.types import ToolResult
from supervisor.session.manager import SessionAwaitingApproval, SessionManager
from supervisor.session.registry import SessionRegistry
from tests.support.session_env import make_manifest


class _CountingProvider:
    """Minimal tool provider that counts invoke calls."""

    def __init__(self) -> None:
        self.calls = 0
        self._tool_set = None

    def invoke(self, ctx, name, arguments):  # noqa: ANN001
        self.calls += 1
        return ToolResult(ok=True, payload={"echo": arguments})


@pytest.mark.asyncio
async def test_t9_pre_invoke_parks_without_running_body(tmp_path: Path) -> None:
    """T9: require_approval tool parks; provider body not run."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 't9.db'}") as (_, factory):
        manifest = replace(
            make_manifest("kg-v1", requires_sandbox=False, image_digest=None),
            approvals=ApprovalSpec(
                timeout_s=300,
                on_timeout="deny",
                require_approval=("echo",),
            ),
        )
        catalog = HarnessCatalog({"kg-v1": manifest})
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            reader=JournalReader(factory, blob_dir=tmp_path / "blobs"),
            registry=SessionRegistry(),
            catalog=catalog,
            approvals=ApprovalBroker(factory),
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        provider = _CountingProvider()
        seams = MagicMock()
        seams.tool_provider = provider
        m._seams[sid] = seams

        with pytest.raises(SessionAwaitingApproval):
            await m.invoke_tool(
                sid,
                tenant_id="t",
                name="echo",
                arguments={"text": "secret"},
                tool_call_id="tc-t9",
            )
        assert provider.calls == 0
        row = await m._get_session_row(sid)
        assert row.status == "awaiting_approval"
        pending = await m.pending_approvals(sid)
        assert pending
        await m.terminate(sid)


@pytest.mark.asyncio
async def test_t10_pre_invoke_after_allow_runs_once(tmp_path: Path) -> None:
    """T10: After allow, re-invoke with same tool_call_id runs body once."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 't10.db'}") as (_, factory):
        manifest = replace(
            make_manifest("kg-v1", requires_sandbox=False, image_digest=None),
            approvals=ApprovalSpec(
                timeout_s=300,
                on_timeout="deny",
                require_approval=("echo",),
            ),
        )
        catalog = HarnessCatalog({"kg-v1": manifest})
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            reader=JournalReader(factory, blob_dir=tmp_path / "blobs"),
            registry=SessionRegistry(),
            catalog=catalog,
            approvals=ApprovalBroker(factory),
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        provider = _CountingProvider()
        seams = MagicMock()
        seams.tool_provider = provider
        m._seams[sid] = seams

        with pytest.raises(SessionAwaitingApproval):
            await m.invoke_tool(
                sid,
                tenant_id="t",
                name="echo",
                arguments={"text": "x"},
                tool_call_id="tc-t10",
            )
        assert provider.calls == 0
        aid = (await m.pending_approvals(sid))[0].approval_id
        await m.respond_approval(aid, "approve")
        assert (await m._get_session_row(sid)).status == "active"

        out = await m.invoke_tool(
            sid,
            tenant_id="t",
            name="echo",
            arguments={"text": "x"},
            tool_call_id="tc-t10",
        )
        assert out.result.ok is True
        assert provider.calls == 1
        # Idempotent outbox: same call_id should not double-invoke provider.
        out2 = await m.invoke_tool(
            sid,
            tenant_id="t",
            name="echo",
            arguments={"text": "x"},
            tool_call_id="tc-t10",
        )
        assert out2.result.ok is True
        assert provider.calls == 1
        await m.terminate(sid)


@pytest.mark.asyncio
async def test_t9_http_pre_invoke_parks_without_running_body(supervisor_stack) -> None:
    """T9 (HTTP): POST tools/invoke on require_approval tool → 409; provider body not run."""
    from supervisor.agent.events import ToolEnd

    catalog = supervisor_stack["catalog"]
    base = make_manifest(
        "coding-v1",
        requires_sandbox=True,
        image_digest="sha256:" + "a" * 64,
    )
    catalog.register(
        replace(
            base,
            approvals=ApprovalSpec(
                timeout_s=300,
                on_timeout="deny",
                require_approval=("echo",),
            ),
        )
    )

    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    headers = {"X-Tenant-Id": "d8-tenant-a"}
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

    inv = await client.post(
        f"/v1/sessions/{sid}/tools/invoke",
        headers=headers,
        json={
            "name": "echo",
            "arguments": {"text": "secret"},
            "tool_call_id": "d8-http-pre",
        },
    )
    assert inv.status_code == 409, inv.text

    row = await state.manager._get_session_row(sid)
    assert row.status == "awaiting_approval"
    pending = await state.manager.pending_approvals(sid)
    assert len(pending) == 1
    assert pending[0].harness_request_id == "d8-http-pre"

    events = await state.reader.read(sid, since=-1)
    assert not any(isinstance(e.payload, ToolEnd) for e in events)

    listed = await client.get(f"/v1/sessions/{sid}/approvals", headers=headers)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) >= 1


def test_require_approval_parsed_from_manifest() -> None:
    """ApprovalSpec must parse require_approval list (D8 Critical)."""
    from supervisor.harness import ApprovalSpec
    from supervisor.harness.storage.converters import approval_spec_from_dict

    raw = {
        "timeout_s": 10,
        "on_timeout": "deny",
        "require_approval": ["bash", "shell"],
    }
    spec = approval_spec_from_dict(raw)
    assert "bash" in spec.require_approval
    assert "shell" in spec.require_approval
    assert isinstance(ApprovalSpec().require_approval, tuple)
