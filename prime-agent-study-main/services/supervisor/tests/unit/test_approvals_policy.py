"""Approval broker unit tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from supervisor.db.engine import open_db
from supervisor.journal.writer import JournalWriter
from supervisor.policy.approvals import ApprovalBroker, UnknownApproval


@pytest.mark.asyncio
async def test_request_and_respond(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        broker = ApprovalBroker(factory)
        aid = await broker.request("s1", "t1", "Allow?", ["yes", "no"], 300)
        pending = await broker.pending("s1")
        assert len(pending) == 1
        assert pending[0].approval_id == aid
        done = await broker.respond(aid, "yes")
        assert done.status == "responded"
        assert done.choice == "yes"
        assert await broker.pending("s1") == []


@pytest.mark.asyncio
async def test_pending_survives_restart(tmp_path: Path) -> None:
    db = f"sqlite+aiosqlite:///{tmp_path / 'a.db'}"
    async with open_db(db) as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        aid = await ApprovalBroker(factory).request(
            "s1", None, "ok?", ["y", "n"], 60
        )

    async with open_db(db) as (_, factory2):
        pending = await ApprovalBroker(factory2).pending("s1")
        assert len(pending) == 1
        assert pending[0].approval_id == aid


@pytest.mark.asyncio
async def test_timeout_journallable(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        broker = ApprovalBroker(factory)
        await broker.request("s1", None, "ok?", ["y"], 0.01)
        await asyncio.sleep(0.05)
        expired = await broker.expire_due(on_timeout="deny")
        assert len(expired) == 1
        assert expired[0].status == "timed_out"
        assert expired[0].choice == "deny"


@pytest.mark.asyncio
async def test_unknown_approval_rejected(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}") as (_, factory):
        broker = ApprovalBroker(factory)
        with pytest.raises(UnknownApproval):
            await broker.respond("deadbeef", "yes")


@pytest.mark.asyncio
async def test_respond_twice_idempotent(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        broker = ApprovalBroker(factory)
        aid = await broker.request("s1", None, "ok?", ["y"], 60)
        a = await broker.respond(aid, "yes")
        b = await broker.respond(aid, "no")
        assert a.status == b.status == "responded"
        assert b.choice == "yes"  # first choice kept


@pytest.mark.asyncio
async def test_request_does_not_block(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        broker = ApprovalBroker(factory)
        t0 = time.monotonic()
        await asyncio.gather(
            *[
                broker.request("s1", None, f"p{i}", ["y"], 300)
                for i in range(20)
            ]
        )
        assert time.monotonic() - t0 < 2.0
        assert len(await broker.pending("s1")) == 20
