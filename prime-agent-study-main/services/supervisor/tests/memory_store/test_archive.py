"""An archived session is loadable from every replica, and what is written to it after it
was archived is loaded with it (it used to be a file on one replica, and later writes
were never read)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.types import Scope

SC = Scope("t1", "user:alice", "agent")


async def _svc(tmp_path: Path, name: str) -> MemoryService:
    svc = MemoryService(database_url=f"sqlite+aiosqlite:///{tmp_path / 'm.db'}", archive_dir=str(tmp_path / name))
    await svc.startup()
    return svc


def _texts(events: object) -> list[str]:
    return [e.data["text"] for e in events if "text" in e.data]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_another_replica_loads_an_archived_session(tmp_path: Path) -> None:
    a = await _svc(tmp_path, "replica-a")
    h = await a.create_session(SC)
    a.append(h.id, "user/message", {"text": "before"})
    await a.flush(h.id)
    assert (await a.archive_session(h.id))["archived_events"] == 1
    b = await _svc(tmp_path, "replica-b")  # its own disk: nothing of A's there
    loaded = await b.load(h.id)
    assert loaded.status == "archived" and _texts(loaded.events) == ["before"]
    assert not (tmp_path / "replica-a").exists() or not any((tmp_path / "replica-a").iterdir())
    await a.aclose()
    await b.aclose()


@pytest.mark.asyncio
async def test_writes_after_archiving_are_loaded_and_folded_in_again(tmp_path: Path) -> None:
    svc = await _svc(tmp_path, "a")
    h = await svc.create_session(SC)
    svc.append(h.id, "user/message", {"text": "before"})
    await svc.flush(h.id)
    await svc.archive_session(h.id)
    svc.append(h.id, "user/message", {"text": "after"})
    await svc.flush(h.id)
    assert _texts((await svc.load(h.id)).events) == ["before", "after"]
    assert (await svc.archive_session(h.id))["archived_events"] == 2
    assert _texts((await svc.load(h.id)).events) == ["before", "after"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_an_archive_file_from_before_the_table_still_loads(tmp_path: Path) -> None:
    svc = await _svc(tmp_path, "legacy")
    h = await svc.create_session(SC)
    svc.append(h.id, "user/message", {"text": "old"})
    await svc.flush(h.id)
    await svc.archive_session(h.id)
    from sqlalchemy import delete

    from supervisor.memory_service.models import SessionArchiveRow
    async with svc._factory() as db, db.begin():  # as an older build left it: a file, no row
        await db.execute(delete(SessionArchiveRow))
    (tmp_path / "legacy").mkdir(exist_ok=True)
    (tmp_path / "legacy" / f"{h.id}.jsonl").write_text(
        json.dumps({"type": "user/message", "seq": 0, "time": 1, "data": {"text": "old"}}) + "\n"
    )
    assert _texts((await svc.load(h.id)).events) == ["old"]
    await svc.aclose()
