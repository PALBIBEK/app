"""D8 §5 A / E / matrix T1–T8, T17 — approval lifecycle + multi-pending + choice."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from supervisor.agent.events import ApprovalRequired
from supervisor.agent.factory import create_runtime
from supervisor.catalog.store import CompositionCatalog
from supervisor.db.engine import open_db
from supervisor.harness import ApprovalSpec, BudgetSpec, HarnessCatalog
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.policy.approvals import (
    ApprovalBroker,
    InvalidApprovalChoice,
    is_allow_choice,
)
from supervisor.policy.sandbox import SandboxPolicy
from supervisor.session.manager import SessionAwaitingApproval, SessionManager
from supervisor.session.registry import SessionRegistry
from tests.support.session_env import make_manifest
from tests.unit.test_catalog_pins import _offer_composition


@pytest.mark.asyncio
async def test_t1_approval_required_parks_session(session_env) -> None:
    """T1: ApprovalRequired + ask → awaiting_approval; pending non-empty."""
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat, name="d8-approve-comp")
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
        "turn-t1",
        ApprovalRequired(
            approval_id="",
            prompt="allow bash?",
            options=["yes", "no"],
            timeout_s=120,
        ),
    )
    row = await m._get_session_row(sid)
    assert row.status == "awaiting_approval"
    pending = await m.pending_approvals(sid)
    assert len(pending) >= 1
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_t2_respond_allow_resumes(session_env) -> None:
    """T2: Respond allow → approval_resolved; session active."""
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat, name="d8-t2")
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
        "turn-t2",
        ApprovalRequired(
            approval_id="",
            prompt="ok?",
            options=["yes", "no"],
            timeout_s=60,
        ),
    )
    aid = (await m.pending_approvals(sid))[0].approval_id
    resolved = await m.respond_approval(aid, "yes")
    assert resolved.status == "responded"
    row = await m._get_session_row(sid)
    assert row.status == "active"
    payloads = await m.event_payloads(sid)
    assert "ApprovalResolved" in payloads
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_t3_http_409_while_awaiting(supervisor_stack) -> None:
    """T3: message/invoke return 409 while awaiting_approval."""
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
    await state.manager._handle_approval_required(
        sid,
        "turn",
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
        json={"text": "hi", "command_id": "c-d8-t3"},
    )
    assert msg.status_code == 409, msg.text


@pytest.mark.asyncio
async def test_t4_approval_default_never_no_park(tmp_path: Path) -> None:
    """T4: approval_default=never auto-resolves without parking."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 't4.db'}") as (_, factory):
        catalog = HarnessCatalog(
            {
                "kg-v1": make_manifest(
                    "kg-v1", requires_sandbox=False, image_digest=None
                )
            }
        )
        policy = SandboxPolicy()
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            reader=JournalReader(factory, blob_dir=tmp_path / "blobs"),
            registry=SessionRegistry(),
            catalog=catalog,
            approvals=ApprovalBroker(factory),
            sandbox_policy=policy,
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        from supervisor.catalog.types import SandboxProfile

        policy.configure(
            sid,
            SandboxProfile(
                name="never-ask",
                semver="1.0.0",
                digest="sha256:" + "n" * 64,
                protocol_version=1,
                status="offered",
                provider="local_process",
                file_effects="workspace_write",
                approval_default="never",
            ),
        )
        _payload, parked = await m._handle_approval_required(
            sid,
            "turn",
            ApprovalRequired(
                approval_id="",
                prompt="auto?",
                options=["yes", "no"],
                timeout_s=30,
            ),
        )
        assert parked is False
        row = await m._get_session_row(sid)
        assert row.status == "active"
        assert await m.pending_approvals(sid) == []
        payloads = await m.event_payloads(sid)
        assert "ApprovalResolved" in payloads
        await m.terminate(sid)


@pytest.mark.asyncio
async def test_t5_sweeper_timeout_deny(tmp_path: Path) -> None:
    """T5: expire_overdue with on_timeout=deny → timed_out; session cleared."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 't5.db'}") as (_, factory):
        manifest = replace(
            make_manifest("kg-v1", requires_sandbox=False, image_digest=None),
            approvals=ApprovalSpec(timeout_s=0.01, on_timeout="deny"),
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
        await m._handle_approval_required(
            sid,
            "turn",
            ApprovalRequired(
                approval_id="",
                prompt="expire me",
                options=["approve", "deny"],
                timeout_s=0.01,
            ),
        )
        assert (await m._get_session_row(sid)).status == "awaiting_approval"
        import asyncio

        await asyncio.sleep(0.05)
        expired = await m.expire_overdue_approvals()
        assert len(expired) >= 1
        assert expired[0].status == "timed_out"
        assert await m.pending_approvals(sid) == []
        row = await m._get_session_row(sid)
        assert row.status == "active"
        payloads = await m.event_payloads(sid)
        assert "ApprovalResolved" in payloads
        await m.terminate(sid)


@pytest.mark.asyncio
async def test_sweeper_timeout_allow(tmp_path: Path) -> None:
    """Sweeper with on_timeout=allow → timed_out with allow-shaped choice; session cleared."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 't5-allow.db'}") as (_, factory):
        manifest = replace(
            make_manifest("kg-v1", requires_sandbox=False, image_digest=None),
            approvals=ApprovalSpec(timeout_s=0.01, on_timeout="allow"),
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
        await m._handle_approval_required(
            sid,
            "turn",
            ApprovalRequired(
                approval_id="",
                prompt="expire with allow",
                options=["approve", "deny"],
                timeout_s=0.01,
            ),
        )
        assert (await m._get_session_row(sid)).status == "awaiting_approval"
        import asyncio

        await asyncio.sleep(0.05)
        expired = await m.expire_overdue_approvals()
        assert len(expired) >= 1
        assert expired[0].status == "timed_out"
        assert is_allow_choice(expired[0].choice)
        assert await m.pending_approvals(sid) == []
        row = await m._get_session_row(sid)
        assert row.status == "active"
        payloads = await m.event_payloads(sid)
        assert "ApprovalResolved" in payloads
        await m.terminate(sid)


@pytest.mark.asyncio
async def test_t6_respond_after_timeout_mapped(supervisor_stack) -> None:
    """T6: Respond after timeout → 409 (not 500)."""
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
    sid = create.json()["session_id"]
    await state.manager._handle_approval_required(
        sid,
        "turn",
        ApprovalRequired(
            approval_id="",
            prompt="soon",
            options=["yes", "no"],
            timeout_s=0.01,
        ),
    )
    pending = await state.manager.pending_approvals(sid)
    aid = pending[0].approval_id
    import asyncio

    await asyncio.sleep(0.05)
    await state.manager.expire_overdue_approvals()
    resp = await client.post(
        f"/v1/approvals/{aid}",
        headers=headers,
        json={"choice": "yes"},
    )
    assert resp.status_code in (404, 409), resp.text
    assert resp.status_code != 500


@pytest.mark.asyncio
async def test_t7_choice_not_in_options_422(supervisor_stack) -> None:
    """T7: Choice not in options → 422."""
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
    sid = create.json()["session_id"]
    await state.manager._handle_approval_required(
        sid,
        "turn",
        ApprovalRequired(
            approval_id="",
            prompt="pick",
            options=["yes", "no"],
            timeout_s=120,
        ),
    )
    aid = (await state.manager.pending_approvals(sid))[0].approval_id
    resp = await client.post(
        f"/v1/approvals/{aid}",
        headers=headers,
        json={"choice": "maybe"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_t8_multi_pending_stays_awaiting(session_env) -> None:
    """T8: Two pending; respond one → still awaiting until second resolved."""
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat, name="d8-t8")
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
        "t1",
        ApprovalRequired(
            approval_id="", prompt="a", options=["yes", "no"], timeout_s=60
        ),
    )
    await m._handle_approval_required(
        sid,
        "t2",
        ApprovalRequired(
            approval_id="", prompt="b", options=["yes", "no"], timeout_s=60
        ),
    )
    pending = await m.pending_approvals(sid)
    assert len(pending) == 2
    await m.respond_approval(pending[0].approval_id, "yes")
    row = await m._get_session_row(sid)
    assert row.status == "awaiting_approval"
    assert len(await m.pending_approvals(sid)) == 1
    await m.respond_approval(pending[1].approval_id, "no")
    row2 = await m._get_session_row(sid)
    assert row2.status == "active"
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_t17_restart_mid_pending_durable(tmp_path: Path) -> None:
    """T17: Restart mid-pending → row durable; resume respond works."""
    db = f"sqlite+aiosqlite:///{tmp_path / 't17.db'}"
    blobs = tmp_path / "blobs"
    catalog = HarnessCatalog(
        {"kg-v1": make_manifest("kg-v1", requires_sandbox=False, image_digest=None)}
    )
    async with open_db(db) as (_, factory):
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=blobs),
            reader=JournalReader(factory, blob_dir=blobs),
            registry=SessionRegistry(),
            catalog=catalog,
            approvals=ApprovalBroker(factory),
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        await m._handle_approval_required(
            sid,
            "turn",
            ApprovalRequired(
                approval_id="",
                prompt="survive",
                options=["yes", "no"],
                timeout_s=300,
            ),
        )
        aid = (await m.pending_approvals(sid))[0].approval_id

    async with open_db(db) as (_, factory2):
        m2 = SessionManager(
            session_factory=factory2,
            writer=JournalWriter(factory2, blob_dir=blobs),
            reader=JournalReader(factory2, blob_dir=blobs),
            registry=SessionRegistry(),
            catalog=catalog,
            approvals=ApprovalBroker(factory2),
        )
        pending = await m2.pending_approvals(sid)
        assert len(pending) == 1
        assert pending[0].approval_id == aid
        await m2.respond_approval(aid, "yes")
        assert (await m2._get_session_row(sid)).status == "active"


@pytest.mark.asyncio
async def test_sweeper_started_in_lifespan() -> None:
    """Production lifecycle must start approval sweeper (D8 Critical)."""
    from supervisor import main as main_mod

    src = Path(main_mod.__file__).read_text(encoding="utf-8")
    assert "expire_overdue_approvals" in src or "approval-sweeper" in src
    assert "approval_sweeper" in src or "approval-sweeper" in src
