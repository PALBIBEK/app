"""A session whose subagents are working in its sandbox is not idle, however quiet it is.

Its children journal in their own sessions, so the session's own journal can stay quiet for
as long as a child works; the idle reaper must not pause it (and its sandbox) meanwhile.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.engine import open_db
from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.session.manager_upkeep import UpkeepOps

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'u.db'}") as (_, factory):
        yield factory


async def _session(factory: async_sessionmaker[AsyncSession], sid: str, root: str | None, quiet_h: float) -> None:
    async with factory() as db:
        db.add(SessionRow(id=sid, tenant_id="t1", root_session_id=root, parent_session_id=root))
        db.add(SessionIndexRow(session_id=sid, last_event_at=NOW - timedelta(hours=quiet_h)))
        await db.commit()


async def test_a_sessions_activity_is_its_own_or_its_subagents_latest(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _session(factory, "root", None, quiet_h=5)
    await _session(factory, "child", "root", quiet_h=0.1)  # working right now
    await _session(factory, "alone", None, quiet_h=5)
    upkeep = SimpleNamespace(_db=factory)

    last = UpkeepOps._session_last_activity
    root, alone = await last(upkeep, "root"), await last(upkeep, "alone")  # type: ignore[arg-type]

    assert root is not None and alone is not None
    assert root.replace(tzinfo=UTC) == NOW - timedelta(hours=0.1)
    assert alone.replace(tzinfo=UTC) == NOW - timedelta(hours=5)
