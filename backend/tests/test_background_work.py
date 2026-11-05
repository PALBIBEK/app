"""Background work that a server stop can cut off: it beats while it runs, and what stopped
beating is settled (procrastinate's worker heartbeat and stalled-job rule)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prime_agent.db import Base
from prime_agent.db.models.evaluation import Dataset, Experiment
from prime_agent.db.models.user import User
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.heartbeat import STALLED_AFTER, beating, stalled


def test_work_has_stalled_only_after_no_sign_of_life_for_the_limit() -> None:
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    assert not stalled(now - STALLED_AFTER, None, now=now)
    assert stalled(now - STALLED_AFTER - timedelta(seconds=1), None, now=now)
    assert stalled(None, now - timedelta(hours=1), now=now), "never beaten: judged on when it began"
    assert not stalled(now - timedelta(seconds=5), now - timedelta(hours=1), now=now), "a beat is newer than its start"
    assert not stalled(None, None, now=now)
    assert stalled(datetime(2026, 11, 5, 11, 0), None, now=now), "a naive time is read as UTC"


@pytest.mark.asyncio
async def test_a_row_is_stamped_while_the_work_runs_and_no_longer_after(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'beat.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(email="beat@example.com", name="Beat", hashed_password="x")
        db.add(user)
        await db.flush()
        agent = await HarnessService.acreate({"name": "a", "model": "gateway/echo", "mode": "sticky",
                                              "user_id": user.id, "created_by": user.id}, db)
        ds = Dataset(user_id=user.id, harness_id=agent.id, name="d")
        db.add(ds)
        await db.flush()
        exp = Experiment(user_id=user.id, harness_id=agent.id, dataset_id=ds.id, label="v1", status="running",
                         harness_version="h-r1", runtime="githi", composition_id="c", composition_digest="sha256:x")
        db.add(exp)
        await db.commit()

    async def beat_at() -> datetime | None:
        async with factory() as db:
            row = await db.get(Experiment, exp.id)
            return row.heartbeat_at if row else None

    async with beating(factory, Experiment, exp.id, every=0.02):
        await asyncio.sleep(0.1)
        first = await beat_at()
        await asyncio.sleep(0.1)
        second = await beat_at()
    last = await beat_at()  # a beat may land between the read above and the block's end
    await asyncio.sleep(0.1)
    assert first is not None and second is not None and second > first, "stamped again and again"
    assert await beat_at() == last, "the beat stops with the work"
    await engine.dispose()
