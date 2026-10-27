"""An in-process supervisor API on SQLite with sessions and journals written as rows, for the
trace tests: no worker runs, and a run's rollup is filled in the first time it is listed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.app import build_supervisor
from supervisor.catalog.loading.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.db.engine import open_db
from supervisor.db.models import EventRow, SessionRow
from supervisor.journal.serde import encode_event, event_type_name
from supervisor.main import create_app
from tests.support.auth import SECRET, ServiceAuth
from tests.support.platform import all_variants, shipped_harness_catalog
from tests.support.session_env import TrackingProvider

__all__ = ["ALICE", "BOB", "T1", "T2", "Api", "api", "list_traces"]

ALICE = ServiceAuth("t1", "alice")
BOB = ServiceAuth("t1", "bob")
T1 = ServiceAuth("t1")
T2 = ServiceAuth("t2")


@dataclass
class Api:
    client: AsyncClient
    factory: async_sessionmaker[AsyncSession]

    async def session(self, *, tenant: str = "t1", owner: str | None = "alice", status: str = "active") -> str:
        sid = uuid4().hex
        async with self.factory() as db, db.begin():
            db.add(SessionRow(id=sid, tenant_id=tenant, owner_principal_id=owner, harness_version="githi-v1",
                              runtime="githi", composition_pins={"composition_id": "githi-v1",
                                                                 "composition_digest": "sha256:" + "a" * 64},
                              status=status))
        return sid

    async def journal(self, sid: str, run: str | None, *events: tuple[int, Any]) -> None:
        async with self.factory() as db, db.begin():
            last = (await db.execute(select(func.max(EventRow.seq)).where(EventRow.session_id == sid))).scalar()
            seq = -1 if last is None else int(last)
            for ts, event in events:
                seq += 1
                db.add(EventRow(session_id=sid, seq=seq, event_id=uuid4().hex, turn_id=run,
                                type=event_type_name(event), payload=encode_event(event), ts_ms=ts, trace_id=None))

    async def run(self, sid: str, run: str, ask: str, *, start: int, tool_ok: bool = True,
                  denied: bool = False, ended: bool = True) -> None:
        events: list[tuple[int, Any]] = [
            (start, RunStart()),
            (start, MessageEnd(role="user", content=[{"type": "text", "text": ask}])),
            (start + 1, TurnStart()),
            (start + 100, MessageEnd(role="assistant", content=[
                {"type": "tool_call", "id": f"{run}-c", "name": "bash", "arguments": {"command": "make"}}],
                usage=Usage(50, 5, 0, 0, 0.01), model="m1")),
        ]
        if denied:
            events += [
                (start + 100, ApprovalRequired(f"{run}-a", "Allow bash?", ["allow", "deny"], 60,
                                               metadata={"tool": "bash", "tool_call_id": f"{run}-c"})),
                (start + 200, ApprovalResolved(f"{run}-a", "deny", "denied", responded_by="alice")),
            ]
        events += [
            (start + 200, ToolStart(f"{run}-c", "bash", {"command": "make"})),
            (start + 300, ToolEnd(f"{run}-c", "bash",
                                  {"ok": tool_ok and not denied, "payload": {"exit_code": 0, "stdout": "ok"},
                                   "error": "denied by approval" if denied else (None if tool_ok else "boom"),
                                   "code": "APPROVAL_DENIED" if denied else None},
                                  is_error=denied or not tool_ok)),
        ]
        if ended:
            events += [
                (start + 400, TurnStart()),
                (start + 500, MessageEnd(role="assistant", content=[{"type": "text", "text": f"answered {ask}"}],
                                         usage=Usage(60, 6, 0, 0, 0.02), model="m1")),
                (start + 500, TurnEnd("stop")),
                (start + 500, RunEnd(messages=[])),
            ]
        await self.journal(sid, run, *events)


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[Api]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'traces.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=all_variants(shipped_harness_catalog()),
            blob_dir=tmp_path / "blobs",
            composition_catalog=bootstrap_composition_catalog(default_catalog_dir()),
            sandbox_provider=TrackingProvider(),
            auth_required=True,
            service_jwt_secret=SECRET,
        )
        async with AsyncClient(transport=ASGITransport(app=create_app(state=state)), base_url="http://t") as client:
            yield Api(client, factory)


async def list_traces(api: Api, headers: dict[str, str], **params: Any) -> dict[str, Any]:
    r = await api.client.get("/v1/traces", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


