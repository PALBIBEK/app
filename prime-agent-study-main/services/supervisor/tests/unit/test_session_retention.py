"""Ended sessions are erased once quiet for the retention window; open ones never are.

Nothing used to remove a session unless someone erased it by hand.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update

from supervisor.db.engine import open_db
from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.journal.writer import JournalWriter
from supervisor.session.erase import SessionEraser
from supervisor.session.retention import Retention, RetentionSweeper, run_retention_loop

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
RETENTION = Retention(days=30)


@pytest.fixture
async def platform(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}") as (_, factory):
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        eraser = SessionEraser(factory, blob_dir=tmp_path / "blobs", objects=None)
        erased: list[str] = []

        async def erase(session_id: str) -> None:
            erased.append(session_id)
            await eraser.erase(session_id)

        yield {
            "factory": factory,
            "writer": writer,
            "erased": erased,
            "sweeper": RetentionSweeper(factory, RETENTION, erase=erase, now=lambda: NOW),
        }


async def _session(p: dict[str, Any], sid: str, *, status: str, quiet_days: float) -> None:
    await p["writer"].ensure_session(sid, tenant_id="t1")
    at = NOW - timedelta(days=quiet_days)
    async with p["factory"]() as db:
        await db.execute(update(SessionRow).where(SessionRow.id == sid).values(status=status))
        index = await db.get(SessionIndexRow, sid)
        if index is None:
            db.add(SessionIndexRow(session_id=sid, last_event_at=at))
        else:
            index.last_event_at = at
        await db.commit()


async def test_only_ended_sessions_quiet_past_the_window_are_erased(platform: dict[str, Any]) -> None:
    p = platform
    await _session(p, "ended-long-ago", status="terminated", quiet_days=31)
    await _session(p, "failed-long-ago", status="failed", quiet_days=90)
    await _session(p, "ended-lately", status="terminated", quiet_days=29)
    await _session(p, "open-but-quiet", status="active", quiet_days=365)  # never swept
    await _session(p, "over-budget", status="budget_exceeded", quiet_days=365)  # may reopen

    assert await p["sweeper"].sweep() == 2

    assert sorted(p["erased"]) == ["ended-long-ago", "failed-long-ago"]
    async with p["factory"]() as db:
        assert await db.get(SessionRow, "ended-long-ago") is None
        for kept in ("ended-lately", "open-but-quiet", "over-budget"):
            assert await db.get(SessionRow, kept) is not None
    assert await p["sweeper"].sweep() == 0  # nothing left to do


async def test_a_session_that_cannot_be_erased_now_is_retried_and_does_not_stop_the_rest(
    platform: dict[str, Any],
) -> None:
    p = platform
    await _session(p, "stuck", status="terminated", quiet_days=40)
    await _session(p, "fine", status="terminated", quiet_days=35)
    tries: list[str] = []

    async def erase(session_id: str) -> None:
        tries.append(session_id)
        if session_id == "stuck":
            raise TimeoutError("still stopping")
        await SessionEraser(p["factory"], blob_dir=None, objects=None).erase(session_id)

    sweeper = RetentionSweeper(p["factory"], RETENTION, erase=erase, now=lambda: NOW)
    assert await sweeper.sweep() == 1
    assert await sweeper.expired() == ["stuck"]  # the next sweep tries it again
    assert tries == ["stuck", "fine"]  # oldest first


async def test_the_loop_sweeps_until_stopped(platform: dict[str, Any]) -> None:
    p = platform
    await _session(p, "old", status="terminated", quiet_days=100)
    stop = asyncio.Event()
    loop = asyncio.create_task(run_retention_loop(p["sweeper"], 0.05, stop))
    for _ in range(100):
        if p["erased"]:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(loop, timeout=2)
    assert p["erased"] == ["old"]


async def test_retention_is_off_unless_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_RETENTION_DAYS", raising=False)
    assert Retention.from_env() is None
    monkeypatch.setenv("SESSION_RETENTION_DAYS", "90")
    assert Retention.from_env() == Retention(days=90)
    monkeypatch.setenv("SESSION_RETENTION_DAYS", "0")
    with pytest.raises(ValueError, match="at least 1"):
        Retention.from_env()
