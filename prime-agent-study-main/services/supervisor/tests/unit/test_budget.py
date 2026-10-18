"""Budget tracker unit tests: the session total lives in the database."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.engine import open_db
from supervisor.harness import BudgetSpec
from supervisor.journal.writer import JournalWriter
from supervisor.policy.budget import BudgetExceeded, BudgetTracker

Factory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[Factory]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}") as (_, factory):
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await writer.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        yield factory


def _tracker(factory: Factory, spec: BudgetSpec | None) -> BudgetTracker:
    tracker = BudgetTracker(factory)
    tracker.configure("s1", spec)
    tracker.begin_turn("s1")
    return tracker


@pytest.mark.asyncio
async def test_cost_accumulates_across_turns(factory: Factory) -> None:
    tracker = _tracker(factory, BudgetSpec(max_cost_per_session=10.0))
    first = await tracker.record("s1", Decimal("1.5"), 20)
    assert (first.total_cost_usd, first.exceeded) == (Decimal("1.5"), False)
    tracker.begin_turn("s1")
    assert (await tracker.record("s1", Decimal("2.0"), 20)).total_cost_usd == Decimal("3.5")


@pytest.mark.asyncio
async def test_exceeding_cost_trips(factory: Factory) -> None:
    state = await _tracker(factory, BudgetSpec(max_cost_per_session=2.0)).record(
        "s1", Decimal("2.5"), 2
    )
    assert state.exceeded
    assert "max_cost_per_session" in (state.exceeded_reason or "")


@pytest.mark.asyncio
async def test_exceeding_tokens_per_turn(factory: Factory) -> None:
    state = await _tracker(factory, BudgetSpec(max_tokens_per_turn=100)).record(
        "s1", Decimal(0), 110
    )
    assert state.exceeded
    assert "max_tokens_per_turn" in (state.exceeded_reason or "")


@pytest.mark.asyncio
async def test_check_reports_a_spent_budget(factory: Factory) -> None:
    tracker = _tracker(factory, BudgetSpec(max_cost_per_session=1.0))
    await tracker.record("s1", Decimal("1.5"), 2)
    assert (await tracker.check("s1")).exceeded


@pytest.mark.asyncio
async def test_totals_survive_restart(factory: Factory) -> None:
    await _tracker(factory, BudgetSpec(max_cost_per_session=10.0)).record(
        "s1", Decimal("4.0"), 2
    )
    fresh = _tracker(factory, BudgetSpec(max_cost_per_session=10.0))
    assert (await fresh.check("s1")).total_cost_usd == Decimal("4.0")


@pytest.mark.asyncio
async def test_workers_sharing_a_session_never_lose_a_charge(factory: Factory) -> None:
    """A session adopted by another worker: each sees the other's spend at once."""
    spec = BudgetSpec(max_cost_per_session=0.05)
    a, b = _tracker(factory, spec), _tracker(factory, spec)
    await a.record("s1", Decimal("0.02"), 1)
    await b.record("s1", Decimal("0.02"), 1)
    await asyncio.gather(*(w.record("s1", Decimal("0.01"), 1) for w in (a, b)))
    assert (await a.check("s1")).total_cost_usd == Decimal("0.06")
    assert (await b.check("s1")).exceeded


@pytest.mark.asyncio
async def test_no_budget_never_trips(factory: Factory) -> None:
    assert not (await _tracker(factory, None).record("s1", Decimal(999), 999)).exceeded


@pytest.mark.asyncio
async def test_zero_usage_never_trips(factory: Factory) -> None:
    spec = BudgetSpec(max_cost_per_session=0.01, max_tokens_per_turn=1)
    state = await _tracker(factory, spec).record("s1", Decimal(0), 0)
    assert (state.exceeded, state.total_cost_usd) == (False, Decimal(0))


@pytest.mark.asyncio
async def test_the_error_carries_the_spent_state(factory: Factory) -> None:
    tracker = _tracker(factory, BudgetSpec(max_cost_per_session=1.0))
    state = await tracker.record("s1", Decimal("1.5"), 2)
    err = BudgetExceeded.from_state(state)
    assert (err.session_id, err.current_cost, err.limit_cost) == ("s1", Decimal("1.5"), 1.0)
    assert err.error_code == "BUDGET_EXCEEDED"
    assert "max_cost_per_session" in str(err)
