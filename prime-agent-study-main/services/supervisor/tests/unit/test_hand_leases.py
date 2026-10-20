"""Hand leases: one holder, renewals, give-back, expiry and the fencing token (SQLite and Postgres)."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from supervisor.db.engine import open_db
from supervisor.hands.leases import HandLeases

type Factory = async_sessionmaker[AsyncSession]
PG_URL = os.environ.get("SUPERVISOR_TEST_PG_URL", "")


@pytest_asyncio.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(not PG_URL, reason="no Postgres"))]
)
async def leases(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[HandLeases]:
    if request.param == "sqlite":
        async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'l.db'}") as (_, factory):
            yield HandLeases(factory)
        return
    name = f"leases_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        async with open_db(f"{PG_URL.rpartition('/')[0]}/{name}") as (_, factory):
            yield HandLeases(factory)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def test_one_holder_at_a_time_and_lending_again_renews(leases: HandLeases) -> None:
    first = await leases.lend("s1~build", "job-a")
    assert first is not None and first.token == 1
    assert await leases.lend("s1~build", "job-b") is None  # held
    again = await leases.lend("s1~build", "job-a")
    assert again is not None and again.token == 1 and again.expires_ms >= first.expires_ms
    assert await leases.holds("s1~build", "job-a", 1)


async def test_a_lease_given_back_or_expired_fences_out_its_old_holder(leases: HandLeases) -> None:
    first = await leases.lend("s1~build", "job-a")
    assert first is not None and await leases.give_back("s1~build", "job-a", 1)
    assert not await leases.holds("s1~build", "job-a", 1)
    assert await leases.current("s1~build") is None
    second = await leases.lend("s1~build", "job-b", ttl_s=0.05)
    assert second is not None and second.token == 2  # the token only grows
    await asyncio.sleep(0.1)
    assert await leases.current("s1~build") is None  # expired: the hand is free
    third = await leases.lend("s1~build", "job-a")
    assert third is not None and third.token == 3
    assert not await leases.holds("s1~build", "job-b", 2)  # the expired holder is fenced out
    assert await leases.renew(second) is None and not await leases.give_back("s1~build", "job-b", 2)


async def test_lenders_racing_for_a_new_hand_get_one_lease(leases: HandLeases) -> None:
    got = await asyncio.gather(*(leases.lend("s2~build", f"job-{n}") for n in range(5)))
    won = [g for g in got if g is not None]
    assert len(won) == 1 and won[0].token == 1


async def test_taking_a_hand_back_ends_any_lease(leases: HandLeases) -> None:
    await leases.lend("s1", "job-a")
    await leases.take_back("s1")
    assert await leases.current("s1") is None and not await leases.holds("s1", "job-a", 1)
