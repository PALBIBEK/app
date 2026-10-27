"""Traces over HTTP: runs across sessions with filters, one session's runs, one run's tree,
one span's content, and who may see them.

Runs the API in-process on SQLite. Sessions and their journals are inserted as rows, so
no worker runs; a run's rollup row is filled in the first time it is listed.
"""

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
from supervisor.db.models import EventRow, RunTraceRow, SessionRow
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import encode_event, event_type_name
from supervisor.main import create_app
from supervisor.traces.store import record_run
from tests.support.auth import SECRET, ServiceAuth
from tests.support.platform import all_variants, shipped_harness_catalog
from tests.support.session_env import TrackingProvider

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


async def _traces(api: Api, headers: dict[str, str], **params: Any) -> dict[str, Any]:
    r = await api.client.get("/v1/traces", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_runs_are_listed_newest_first_and_only_to_whoever_may_see_their_session(api: Api) -> None:
    mine = await api.session(owner="alice")
    bobs = await api.session(owner="bob")
    other_tenant = await api.session(tenant="t2", owner=None)
    await api.run(mine, "r1", "build it", start=1_000)
    await api.run(mine, "r2", "build it again", start=5_000)
    await api.run(bobs, "r3", "bob's build", start=3_000)
    await api.run(other_tenant, "r4", "elsewhere", start=4_000)

    alice = await _traces(api, ALICE)
    assert [i["run_id"] for i in alice["items"]] == ["r2", "r1"]
    first = alice["items"][0]
    assert first["status"] == "ok" and first["input"] == "build it again" and first["output"] == "answered build it again"
    assert first["cost"] == 0.03 and first["tool_calls"] == 1 and first["tools"] == {"bash": 1}
    assert first["composition_id"] == "githi-v1" and len(first["trace_id"]) == 32

    assert [i["run_id"] for i in (await _traces(api, T1))["items"]] == ["r2", "r3", "r1"]
    assert [i["run_id"] for i in (await _traces(api, BOB))["items"]] == ["r3"]
    assert [i["run_id"] for i in (await _traces(api, T2))["items"]] == ["r4"]


async def test_filters_and_pages(api: Api) -> None:
    sid = await api.session()
    await api.run(sid, "ok", "fine", start=1_000)
    await api.run(sid, "broke", "tool fails", start=2_000, tool_ok=False)
    await api.run(sid, "said-no", "was denied", start=3_000, denied=True)
    await api.run(sid, "going", "still going", start=4_000, ended=False)

    def ids(page: dict[str, Any]) -> list[str]:
        return [i["run_id"] for i in page["items"]]

    assert ids(await _traces(api, ALICE, errors="true")) == ["broke"]
    assert ids(await _traces(api, ALICE, denied="true")) == ["said-no"]
    assert ids(await _traces(api, ALICE, status="running")) == ["going"]
    assert ids(await _traces(api, ALICE, status=["ok", "running"])) == ["going", "said-no", "broke", "ok"]
    assert ids(await _traces(api, ALICE, q="DENIED")) == ["said-no"]
    assert ids(await _traces(api, ALICE, session_id=uuid4().hex)) == []

    page1 = await _traces(api, ALICE, limit=2)
    assert ids(page1) == ["going", "said-no"] and page1["next_before_ms"] == 3_000
    page2 = await _traces(api, ALICE, limit=2, before_ms=page1["next_before_ms"])
    assert ids(page2) == ["broke", "ok"] and page2["next_before_ms"] is None

    r = await api.client.get("/v1/traces", params={"status": "sideways"}, headers=ALICE)
    assert r.status_code == 422


async def test_a_session_replays_its_runs_and_an_ended_session_has_no_running_runs(api: Api) -> None:
    sid = await api.session(status="terminated")
    await api.run(sid, "first", "one", start=1_000)
    await api.run(sid, "cut-off", "two", start=2_000, ended=False)

    r = await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [(i["run_id"], i["status"]) for i in items] == [("first", "ok"), ("cut-off", "interrupted")]
    assert (await api.client.get(f"/v1/sessions/{sid}/traces", headers=BOB)).status_code == 404


async def test_a_run_is_a_tree_and_a_span_opens_with_its_content(api: Api) -> None:
    sid = await api.session()
    await api.run(sid, "r1", "make it", start=1_000)

    r = await api.client.get(f"/v1/sessions/{sid}/runs/r1/trace", headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "githi-v1" and body["rollup"]["status"] == "ok"
    assert [s["kind"] for s in body["spans"]] == ["agent", "step", "llm", "tool", "step", "llm"]
    assert all("detail" not in s for s in body["spans"])
    tool = next(s for s in body["spans"] if s["kind"] == "tool")
    assert tool["preview"] == "$ make" and tool["attrs"]["exit_code"] == 0

    detail = await api.client.get(f"/v1/sessions/{sid}/runs/r1/trace/spans/{tool['id']}", headers=ALICE)
    assert detail.status_code == 200
    assert detail.json()["detail"]["args"] == {"command": "make"}
    assert detail.json()["detail"]["result"]["payload"]["stdout"] == "ok"

    raw = await api.client.get(f"/v1/sessions/{sid}/runs/r1/trace/events", headers=ALICE)
    assert [e["event_type"] for e in raw.json()["events"]][:3] == ["RunStart", "MessageEnd", "TurnStart"]

    base = f"/v1/sessions/{sid}/runs"
    assert (await api.client.get(f"{base}/r1/trace/events", headers=BOB)).status_code == 404
    assert (await api.client.get(f"{base}/nope/trace/events", headers=ALICE)).status_code == 404
    assert (await api.client.get(f"{base}/r1/trace", headers=BOB)).status_code == 404
    assert (await api.client.get(f"{base}/r1/trace", headers=T2)).status_code == 404
    assert (await api.client.get(f"{base}/nope/trace", headers=ALICE)).status_code == 404
    assert (await api.client.get(f"{base}/r1/trace/spans/0000", headers=ALICE)).status_code == 404


async def test_a_run_is_recorded_when_it_starts_and_again_when_it_ends(api: Api) -> None:
    sid = await api.session()
    reader = JournalReader(api.factory)

    async def row() -> RunTraceRow | None:
        async with api.factory() as db:
            return await db.get(RunTraceRow, (sid, "r1"))

    await api.journal(sid, "r1", (1_000, RunStart()))
    await record_run(api.factory, reader, sid, "r1")
    started = await row()
    assert started is not None and started.status == "running" and started.ended_ms is None

    await api.journal(sid, "r1", (1_500, TurnEnd("stop")), (1_500, RunEnd(messages=[])))
    await record_run(api.factory, reader, sid, "r1")
    ended = await row()
    assert ended is not None and ended.status == "ok" and ended.duration_ms == 500

    await record_run(api.factory, reader, "no-such-session", "r1")  # never raises

    # A row an older builder wrote is rebuilt the next time it is read.
    async with api.factory() as db, db.begin():
        stale = await db.get(RunTraceRow, (sid, "r1"))
        assert stale is not None
        stale.rollup, stale.status, stale.duration_ms = {"v": 1}, "error", 1
    listed = (await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)).json()["items"]
    assert [(i["status"], i["duration_ms"]) for i in listed] == [("ok", 500)]
