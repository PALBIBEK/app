"""Budget tracker unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.events import Usage
from supervisor.db.engine import open_db
from supervisor.harness import BudgetSpec
from supervisor.journal.writer import JournalWriter
from supervisor.policy.budget import BudgetExceeded, BudgetTracker


@pytest.mark.asyncio
async def test_cost_accumulates_across_turns(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        tracker = BudgetTracker(
            factory, limits=BudgetSpec(max_cost_per_session=10.0)
        )
        tracker.configure("s1", BudgetSpec(max_cost_per_session=10.0))
        tracker.begin_turn("s1")
        s1 = await tracker.record("s1", Usage(10, 10, 0, 0, 1.5))
        assert s1.cost_total == pytest.approx(1.5)
        assert not s1.exceeded
        tracker.begin_turn("s1")
        s2 = await tracker.record("s1", Usage(10, 10, 0, 0, 2.0))
        assert s2.cost_total == pytest.approx(3.5)


@pytest.mark.asyncio
async def test_exceeding_cost_trips(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        tracker = BudgetTracker(factory)
        tracker.configure("s1", BudgetSpec(max_cost_per_session=2.0))
        tracker.begin_turn("s1")
        state = await tracker.record("s1", Usage(1, 1, 0, 0, 2.5))
        assert state.exceeded
        assert "max_cost_per_session" in (state.reason or "")


@pytest.mark.asyncio
async def test_exceeding_tokens_per_turn(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        tracker = BudgetTracker(factory)
        tracker.configure("s1", BudgetSpec(max_tokens_per_turn=100))
        tracker.begin_turn("s1")
        state = await tracker.record("s1", Usage(80, 30, 0, 0, 0.0))
        assert state.exceeded
        assert "max_tokens_per_turn" in (state.reason or "")


@pytest.mark.asyncio
async def test_check_refuses_when_over(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        tracker = BudgetTracker(factory)
        tracker.configure("s1", BudgetSpec(max_cost_per_session=1.0))
        await tracker.record("s1", Usage(1, 1, 0, 0, 1.5))
        state = await tracker.check("s1")
        assert state.exceeded


@pytest.mark.asyncio
async def test_totals_survive_restart(tmp_path: Path) -> None:
    db = f"sqlite+aiosqlite:///{tmp_path / 'b.db'}"
    async with open_db(db) as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        t1 = BudgetTracker(factory)
        t1.configure("s1", BudgetSpec(max_cost_per_session=10.0))
        await t1.record("s1", Usage(1, 1, 0, 0, 4.0))

    async with open_db(db) as (_, factory2):
        t2 = BudgetTracker(factory2)
        t2.configure("s1", BudgetSpec(max_cost_per_session=10.0))
        state = await t2.check("s1")
        assert state.cost_total == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_no_budget_never_trips(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        tracker = BudgetTracker(factory)
        tracker.configure("s1", None)
        state = await tracker.record("s1", Usage(999, 999, 0, 0, 999.0))
        assert not state.exceeded


@pytest.mark.asyncio
async def test_zero_usage_never_trips(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        tracker = BudgetTracker(factory)
        tracker.configure("s1", BudgetSpec(max_cost_per_session=0.01, max_tokens_per_turn=1))
        state = await tracker.record("s1", Usage(0, 0, 0, 0, 0.0))
        assert not state.exceeded
        assert state.cost_total == 0.0
