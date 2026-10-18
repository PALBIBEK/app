"""CommandStore durable dedup."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.db.engine import open_db
from supervisor.journal.commands import CommandStore
from supervisor.journal.writer import JournalWriter


@pytest.mark.asyncio
async def test_claim_returns_new_then_duplicate(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}") as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        store = CommandStore(factory)

        first, r1 = await store.claim("s1", "cmd-a", turn_id="turn-1")
        assert first is True
        assert r1 == {"turn_id": "turn-1", "status": "accepted"}

        again, r2 = await store.claim("s1", "cmd-a", turn_id="turn-2")
        assert again is False
        assert r2 == r1


@pytest.mark.asyncio
async def test_claim_survives_new_store_instance(tmp_path: Path) -> None:
    db = f"sqlite+aiosqlite:///{tmp_path / 'c.db'}"
    async with open_db(db) as (_, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo", tenant_id="t", harness_version="kg-v1")
        store = CommandStore(factory)
        _, r1 = await store.claim("s1", "cmd-x", turn_id="t1")

    async with open_db(db) as (_, factory2):
        store2 = CommandStore(factory2)
        again, r2 = await store2.claim("s1", "cmd-x", turn_id="t2")
        assert again is False
        assert r2 == r1
