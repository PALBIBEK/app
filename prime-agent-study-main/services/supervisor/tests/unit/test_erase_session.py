"""Erasing a session removes what the supervisor keeps of it, and nothing of anyone else's.

There was no way to: "deleting" a session ended it, and its journal, payloads, files and
checkpoints were kept for ever.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import HandLost, HandProvisioned, MessageEnd, ToolEnd, ToolStart
from supervisor.db.engine import open_db
from supervisor.db.models import (
    ArtifactRow,
    EventRow,
    HandLeaseRow,
    ScopeRow,
    SessionIndexRow,
    SessionRow,
    VaultSecretRow,
    WorkspaceFileRow,
    WorkspaceFileVersionRow,
)
from supervisor.hands.checkpoint import checkpoint_scope
from supervisor.hands.keys import hand_key
from supervisor.journal.writer import JournalWriter
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.postgres_workspace_store import PostgresWorkspaceStore
from supervisor.session.erase import Erased, SessionEraser

pytestmark = pytest.mark.asyncio

GONE, KEPT = "s-erase", "s-other"
LARGE = "x" * 4096  # past the writer's threshold below: kept beside the journal
SHARED = "the same long answer in both sessions " * 200
REPORT = b"report"
REPORT_SHA = hashlib.sha256(REPORT).hexdigest()


@pytest.fixture
async def platform(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'erase.db'}") as (_, factory):
        objects = MemoryObjectStorage()
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs", blob_threshold=1024)
        yield {
            "factory": factory,
            "objects": objects,
            "writer": writer,
            "blobs": tmp_path / "blobs",
            "store": PostgresWorkspaceStore(factory, objects),
            "eraser": SessionEraser(factory, blob_dir=tmp_path / "blobs", objects=objects),
        }


async def _session(p: dict[str, Any], sid: str, *, unique: str) -> None:
    writer: JournalWriter = p["writer"]
    await writer.ensure_session(sid, tenant_id="t1")
    await writer.append(sid, MessageEnd(role="user", content=[{"type": "text", "text": unique}]), turn_id="r1", trace_id=None)
    await writer.append(sid, MessageEnd(role="assistant", content=[{"type": "text", "text": SHARED}]), turn_id="r1", trace_id=None)
    await writer.append(sid, ToolStart(tool_call_id="c1", tool_name="ls", args={}), turn_id="r1", trace_id=None)
    await writer.append(sid, ToolEnd(tool_call_id="c1", tool_name="ls", result={"ok": True}), turn_id="r1", trace_id=None)
    await writer.append(sid, HandProvisioned(hand_id="h1", provider="docker", cause="first_use", hand="build"), turn_id=None, trace_id=None)
    logs = writer.put_blob(f"logs of {sid}".encode())
    await writer.append(sid, HandLost(hand_id="h1", provider="docker", reason="gone", hand="build", logs_ref=logs), turn_id=None, trace_id=None)
    factory: async_sessionmaker[AsyncSession] = p["factory"]
    await p["objects"].put(f"sessions/{sid}/artifacts/a1", REPORT, content_sha256=REPORT_SHA)
    async with factory() as db:
        db.add(ArtifactRow(id=f"{sid}-a1", session_id=sid, turn_id="r1", name="report.txt", content_sha256=REPORT_SHA,
                           object_key=f"sessions/{sid}/artifacts/a1", size_bytes=6, created_at=datetime.now(UTC)))
        db.add(VaultSecretRow(id=f"{sid}-key", owner_kind="session", owner_id=sid, name="gateway_key", key_id="k1",
                              nonce=b"n", ciphertext=b"c", expires_at=datetime.now(UTC) + timedelta(hours=1)))
        for key in (hand_key(sid), hand_key(sid, "build")):
            db.add(HandLeaseRow(hand_key=key, holder=None, token=1))
        for key in (hand_key(sid), hand_key(sid, "build")):
            db.add(ScopeRow(id=checkpoint_scope(key), type="session", owner_principal_id="alice", name=f"checkpoints of {key}"))
        await db.commit()
    for key in (hand_key(sid), hand_key(sid, "build")):
        await p["store"].commit_blob(checkpoint_scope(key), "app.py", f"print('{key}')".encode())


async def _count(factory: async_sessionmaker[AsyncSession], row: Any, *where: Any) -> int:
    async with factory() as db:
        return int(await db.scalar(select(func.count()).select_from(row).where(*where)) or 0)


async def test_erasing_removes_the_sessions_record_and_keeps_everyone_elses(platform: dict[str, Any]) -> None:
    p = platform
    await _session(p, GONE, unique=LARGE)
    await _session(p, KEPT, unique="short")
    blobs_before = set(Path(p["blobs"]).iterdir())

    erased = await p["eraser"].erase(GONE)

    factory = p["factory"]
    scopes = [checkpoint_scope(hand_key(GONE)), checkpoint_scope(hand_key(GONE, "build"))]
    assert erased.events == 6 and erased.artifacts == 1 and erased.checkpoint_files == 2
    for row, where in [
        (SessionRow, SessionRow.id == GONE),
        (EventRow, EventRow.session_id == GONE),
        (SessionIndexRow, SessionIndexRow.session_id == GONE),
        (ArtifactRow, ArtifactRow.session_id == GONE),
        (VaultSecretRow, VaultSecretRow.owner_id == GONE),
        (HandLeaseRow, HandLeaseRow.hand_key.startswith(GONE)),
        (ScopeRow, ScopeRow.id.in_(scopes)),
        (WorkspaceFileRow, WorkspaceFileRow.scope_id.in_(scopes)),
        (WorkspaceFileVersionRow, WorkspaceFileVersionRow.scope_id.in_(scopes)),
    ]:
        assert await _count(factory, row, where) == 0, row.__tablename__
    assert not await p["objects"].exists(f"sessions/{GONE}/artifacts/a1")

    # Its own payloads (the large message, the lost hand's logs) are gone; the payload both
    # sessions hold is not.
    removed = blobs_before - set(Path(p["blobs"]).iterdir())
    assert erased.payloads == len(removed) == 2

    # The other session is whole.
    assert await _count(factory, EventRow, EventRow.session_id == KEPT) == 6
    assert await _count(factory, ArtifactRow, ArtifactRow.session_id == KEPT) == 1
    assert await _count(factory, HandLeaseRow, HandLeaseRow.hand_key.startswith(KEPT)) == 2
    assert await p["objects"].exists(f"sessions/{KEPT}/artifacts/a1")
    reader_rows = await _count(factory, WorkspaceFileRow, WorkspaceFileRow.scope_id == checkpoint_scope(hand_key(KEPT, "build")))
    assert reader_rows == 1


async def test_erasing_again_finds_nothing_left(platform: dict[str, Any]) -> None:
    await _session(platform, GONE, unique=LARGE)
    await platform["eraser"].erase(GONE)
    assert await platform["eraser"].erase(GONE) == Erased()
