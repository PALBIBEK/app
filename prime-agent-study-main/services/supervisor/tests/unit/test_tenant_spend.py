"""A tenant's monthly spend limit holds across its sessions and survives erasing them.

The limit (``TENANT_MAX_COST_PER_TENANT``) used to be the sum of the sessions still on
record, checked only when a session was created: an open session spent past it, and
erasing sessions gave their spend back.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.engine import open_db
from supervisor.harness import BudgetSpec
from supervisor.journal.writer import JournalWriter
from supervisor.policy.budget import BudgetTracker
from supervisor.policy.budget.tracker import tenant_spend
from supervisor.policy.errors import CostQuotaExceeded
from supervisor.policy.quotas import TenantQuotaEnforcer
from supervisor.policy.types import QuotaLimits
from supervisor.session.erase import SessionEraser

pytestmark = pytest.mark.asyncio

Factory = async_sessionmaker[AsyncSession]
LIMIT = Decimal(5)


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[Factory]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 't.db'}") as (_, factory):
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        for sid, tenant in (("a1", "acme"), ("a2", "acme"), ("g1", "globex")):
            await writer.ensure_session(sid, runtime="echo", tenant_id=tenant, harness_version="kg-v1")
        yield factory


class Clock:
    month = "2026-10"

    def __call__(self) -> str:
        return self.month


def _tracker(factory: Factory, clock: Clock | None = None) -> BudgetTracker:
    tracker = BudgetTracker(factory, tenant_limit=LIMIT, month=clock or Clock())
    for sid in ("a1", "a2", "g1"):
        tracker.configure(sid, BudgetSpec(max_cost_per_session=100.0))
        tracker.begin_turn(sid)
    return tracker


async def test_the_limit_holds_across_the_tenants_sessions(factory: Factory) -> None:
    tracker = _tracker(factory)
    assert not (await tracker.record("a1", Decimal(3), 0)).exceeded
    over = await tracker.record("a2", Decimal(2), 0)  # 5 across the two
    assert over.exceeded and "this month" in (over.exceeded_reason or "")
    assert (await tracker.check("a1")).exceeded  # the other session stops too, before its next call
    assert not (await tracker.check("g1")).exceeded  # another tenant is not held up


async def test_erasing_sessions_does_not_give_their_spend_back(factory: Factory, tmp_path: Path) -> None:
    tracker = _tracker(factory)
    await tracker.record("a1", Decimal(5), 0)
    await SessionEraser(factory, blob_dir=tmp_path / "blobs", objects=None).erase("a1")
    assert (await tracker.check("a2")).exceeded
    enforcer = TenantQuotaEnforcer(factory, limits=QuotaLimits(max_cost_per_tenant=LIMIT))
    with pytest.raises(CostQuotaExceeded):
        await enforcer.check_create("acme")  # and no new session either


async def test_a_new_month_starts_at_zero(factory: Factory) -> None:
    clock = Clock()
    tracker = _tracker(factory, clock)
    await tracker.record("a1", Decimal(5), 0)
    assert (await tracker.check("a1")).exceeded
    clock.month = "2026-11"
    assert not (await tracker.check("a1")).exceeded
    async with factory() as db:
        assert await tenant_spend(db, "acme", "2026-10") == Decimal(5)


async def test_charges_recorded_at_once_all_count(factory: Factory) -> None:
    tracker = BudgetTracker(factory, month=Clock())  # no limit: spend is still counted
    await asyncio.gather(*(tracker.record(sid, Decimal("0.25"), 0) for sid in ("a1", "a2") * 4))
    async with factory() as db:
        assert await tenant_spend(db, "acme", Clock.month) == Decimal("2.00")
