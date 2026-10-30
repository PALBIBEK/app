"""The session list answers for the sessions named, with whether each is busy, at once.

The product's list asked for each session on its page one by one (up to a hundred calls);
it asks for the page in one now, so the list carries what the detail did: ``is_busy``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import RunEnd, RunStart
from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.journal.writer import JournalWriter
from supervisor.main import create_app
from tests.support.session_env import TrackingProvider, make_catalog

pytestmark = pytest.mark.asyncio
T1 = {"X-Tenant-Id": "t1"}


@pytest.fixture
async def platform(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'l.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory, catalog=make_catalog(), blob_dir=tmp_path / "blobs", sandbox_provider=TrackingProvider()
        )
        writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        async with AsyncClient(transport=ASGITransport(app=create_app(state=state)), base_url="http://t") as api:
            yield {"api": api, "writer": writer}


async def _session(writer: JournalWriter, sid: str, *, tenant: str = "t1", runs: tuple[str, ...] = ()) -> None:
    await writer.ensure_session(sid, tenant_id=tenant)
    for n, boundary in enumerate(runs):
        event = RunStart() if boundary == "start" else RunEnd(messages=[])
        await writer.append(sid, event, turn_id=f"r{n}", trace_id=None)


async def test_only_the_sessions_named_with_whether_each_is_busy(platform: dict[str, Any]) -> None:
    writer = platform["writer"]
    await _session(writer, "idle", runs=("start", "end"))
    await _session(writer, "busy", runs=("start", "end", "start"))
    await _session(writer, "new")
    await _session(writer, "unnamed")
    await _session(writer, "theirs", tenant="t2")

    listed = await platform["api"].get(
        "/v1/sessions", params=[("ids", "idle"), ("ids", "busy"), ("ids", "new"), ("ids", "theirs")], headers=T1
    )
    assert listed.status_code == 200, listed.text
    assert {s["session_id"]: s["is_busy"] for s in listed.json()} == {"idle": False, "busy": True, "new": False}


async def test_without_ids_every_session_of_the_caller(platform: dict[str, Any]) -> None:
    for sid in ("a", "b"):
        await _session(platform["writer"], sid)
    listed = await platform["api"].get("/v1/sessions", headers=T1)
    assert sorted(s["session_id"] for s in listed.json()) == ["a", "b"]


async def test_a_list_names_at_most_a_page(platform: dict[str, Any]) -> None:
    params = [("ids", f"s{n}") for n in range(201)]
    assert (await platform["api"].get("/v1/sessions", params=params, headers=T1)).status_code == 422
