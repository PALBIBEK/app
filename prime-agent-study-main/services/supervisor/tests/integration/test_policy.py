"""Policy integration: budget abort, loop guard, approvals via manager."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from supervisor.agent.events import (
    ApprovalRequired,
    ErrorEvent,
    MessageEnd,
    ToolStart,
    Usage,
)
from supervisor.db.engine import open_db
from supervisor.harness import BudgetSpec, GuardSpec, HarnessCatalog
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.policy.approvals import ApprovalBroker
from supervisor.policy.budget import BudgetExceeded, BudgetTracker
from supervisor.policy.guards import LoopGuard
from supervisor.session.manager import SessionManager
from supervisor.session.registry import SessionRegistry
from tests.support.session_env import make_manifest


async def _drain(m: SessionManager, sid: str, timeout: float = 2.0) -> None:
    await asyncio.sleep(0.15)


@pytest.mark.asyncio
async def test_budget_aborts_mid_turn_with_terminal(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}") as (_, factory):
        manifest = replace(
            make_manifest("kg-v1", requires_sandbox=False, image_digest=None),
            budget=BudgetSpec(max_cost_per_session=1.0),
        )
        catalog = HarnessCatalog({"kg-v1": manifest})
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        reader = JournalReader(factory, blob_dir=tmp_path / "blobs")
        m = SessionManager(
            session_factory=factory,
            writer=writer,
            reader=reader,
            registry=SessionRegistry(),
            catalog=catalog,
            budget=BudgetTracker(factory),
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        rt = m._registry.get(sid)
        assert rt is not None
        rt.forced_usage = Usage(10, 10, 0, 0, 1.5)  # type: ignore[attr-defined]

        await m.send(sid, "hi", command_id="c1")
        await _drain(m, sid)
        payloads = await m.event_payloads(sid)
        assert "ErrorEvent" in payloads
        row = await m._get_session_row(sid)
        assert row.status == "budget_exceeded"
        # check refuses further send
        with pytest.raises(BudgetExceeded):
            await m.send(sid, "again", command_id="c2")


@pytest.mark.asyncio
async def test_budget_survives_restart(tmp_path: Path) -> None:
    db = f"sqlite+aiosqlite:///{tmp_path / 'p.db'}"
    blobs = tmp_path / "blobs"
    manifest = replace(
        make_manifest("kg-v1", requires_sandbox=False, image_digest=None),
        budget=BudgetSpec(max_cost_per_session=5.0),
    )

    async with open_db(db) as (_, factory):
        catalog = HarnessCatalog({"kg-v1": manifest})
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=blobs),
            reader=JournalReader(factory, blob_dir=blobs),
            registry=SessionRegistry(),
            catalog=catalog,
            budget=BudgetTracker(factory),
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        rt = m._registry.get(sid)
        assert rt is not None
        rt.forced_usage = Usage(1, 1, 0, 0, 4.0)  # type: ignore[attr-defined]
        await m.send(sid, "partial", command_id="c1")
        await _drain(m, sid)
        # Below cap — still active
        row = await m._get_session_row(sid)
        assert row.status == "active"

    async with open_db(db) as (_, factory2):
        catalog2 = HarnessCatalog({"kg-v1": manifest})
        m2 = SessionManager(
            session_factory=factory2,
            writer=JournalWriter(factory2, blob_dir=blobs),
            reader=JournalReader(factory2, blob_dir=blobs),
            registry=SessionRegistry(),
            catalog=catalog2,
            budget=BudgetTracker(factory2),
        )
        # Cold resume + push over cap
        rt = None
        await m2.ensure_ready(sid)
        rt = m2._registry.get(sid)
        assert rt is not None
        rt.forced_usage = Usage(1, 1, 0, 0, 2.0)  # type: ignore[attr-defined]
        await m2.send(sid, "over", command_id="c2")
        await _drain(m2, sid)
        row = await m2._get_session_row(sid)
        assert row.status == "budget_exceeded"


@pytest.mark.asyncio
async def test_loop_guard_trips_on_repetition(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}") as (_, factory):
        manifest = replace(
            make_manifest("kg-v1", requires_sandbox=False, image_digest=None),
            guards=GuardSpec(repetition="abort", oscillation="off", stall_iterations=100),
        )
        catalog = HarnessCatalog({"kg-v1": manifest})
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            reader=JournalReader(factory, blob_dir=tmp_path / "blobs"),
            registry=SessionRegistry(),
            catalog=catalog,
            guards=LoopGuard(),
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        rt = m._registry.get(sid)
        assert rt is not None
        rt.repeat_tool_times = 3  # type: ignore[attr-defined]
        await m.send(sid, "loop", command_id="c1")
        await _drain(m, sid)
        envs = await m._reader.read(sid, since=-1)
        errors = [e for e in envs if isinstance(e.payload, ErrorEvent)]
        assert errors
        assert "repetition" in errors[0].payload.detail
        assert "echo" in errors[0].payload.detail


@pytest.mark.asyncio
async def test_approval_round_trip_survives_restart(tmp_path: Path) -> None:
    db = f"sqlite+aiosqlite:///{tmp_path / 'p.db'}"
    blobs = tmp_path / "blobs"
    manifest = make_manifest("kg-v1", requires_sandbox=False, image_digest=None)

    async with open_db(db) as (_, factory):
        catalog = HarnessCatalog({"kg-v1": manifest})
        broker = ApprovalBroker(factory)
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=blobs),
            reader=JournalReader(factory, blob_dir=blobs),
            registry=SessionRegistry(),
            catalog=catalog,
            approvals=broker,
        )
        sid = await m.create(tenant_id="t", harness_version="kg-v1")
        # Inject approval mid-stream via fan-out path
        await m._handle_approval_required(
            sid,
            "turn",
            ApprovalRequired(
                approval_id="",
                prompt="Allow bash?",
                options=["yes", "no"],
                timeout_s=300,
                harness_request_id="ui-1",
            ),
        )
        pending = await m.pending_approvals(sid)
        assert len(pending) == 1
        aid = pending[0].approval_id
        row = await m._get_session_row(sid)
        assert row.status == "awaiting_approval"

    async with open_db(db) as (_, factory2):
        catalog2 = HarnessCatalog({"kg-v1": manifest})
        m2 = SessionManager(
            session_factory=factory2,
            writer=JournalWriter(factory2, blob_dir=blobs),
            reader=JournalReader(factory2, blob_dir=blobs),
            registry=SessionRegistry(),
            catalog=catalog2,
            approvals=ApprovalBroker(factory2),
        )
        pending = await m2.pending_approvals(sid)
        assert len(pending) == 1
        assert pending[0].approval_id == aid
        await m2.respond_approval(aid, "yes")
        row = await m2._get_session_row(sid)
        assert row.status == "active"
        payloads = await m2.event_payloads(sid)
        assert "ApprovalResolved" in payloads


@pytest.mark.asyncio
async def test_many_pending_approvals_do_not_block_create(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}") as (_, factory):
        catalog = HarnessCatalog(
            {
                "kg-v1": make_manifest(
                    "kg-v1", requires_sandbox=False, image_digest=None
                )
            }
        )
        broker = ApprovalBroker(factory)
        m = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            reader=JournalReader(factory, blob_dir=tmp_path / "blobs"),
            registry=SessionRegistry(),
            catalog=catalog,
            approvals=broker,
        )
        ids = []
        for i in range(15):
            sid = await m.create(tenant_id="t", harness_version="kg-v1")
            ids.append(sid)
            await broker.request(sid, None, f"p{i}", ["y"], 300)
        # Still can create more — workers not held
        extra = await m.create(tenant_id="t", harness_version="kg-v1")
        assert extra
        assert len(await broker.pending(ids[0])) == 1


def test_policy_leak_checks() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "supervisor" / "policy"
    leaks = []
    for path in root.rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "runtimes" in line:
                leaks.append(f"{path.name}:{i}:{line.strip()}")
    assert leaks == []
    # extension_ui_request only in translate.py (membrane)
    src = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    for path in src.rglob("*.py"):
        if path.name == "translate.py":
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#") or line.strip().startswith('"""') or line.strip().startswith("'''"):
                continue
            if "extension_ui_request" in line:
                raise AssertionError(f"{path}:{i}: {line.strip()}")
