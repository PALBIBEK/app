"""WP-A5: sessions API acceptance for offline ``runtime=githi``.

ASGI + journal / ``iter_sse`` only — no live OpenRouter, Docker sandbox, or
compose. Mirrors ``test_echo_full_round_trip``; asserts githi offline event
order, workspace_probe, ToolProvider inject, and no sandbox create.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import RunEnd, ToolEnd, ToolStart
from supervisor.agent.runtimes.githi.tools.provider import GithiToolProvider
from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import TrackingProvider, make_catalog

HEADERS = {"X-Tenant-Id": "t1"}

# Offline AgentLoop (no model_gateway) — journal payload type names.
_OFFLINE_ORDER = [
    "RunStart",
    "TurnStart",
    "ToolStart",
    "ToolEnd",
    "MessageEnd",
    "TurnEnd",
    "RunEnd",
]


async def _drain_until_run_end(manager, sid: str, *, timeout: float = 2.0) -> list[str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        payloads = await manager.event_payloads(sid)
        if "RunEnd" in payloads:
            return payloads
        if loop.time() >= deadline:
            raise AssertionError(f"timed out waiting for RunEnd; saw={payloads}")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_githi_offline_round_trip_via_api(tmp_path) -> None:
    tracker = TrackingProvider()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'githi.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=tracker,
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            cr = await client.post(
                "/v1/sessions",
                json={"harness_version": "githi-v1", "runtime": "githi"},
                headers=HEADERS,
            )
            assert cr.status_code == 201, cr.text
            body = cr.json()
            assert body["runtime"] == "githi"
            assert body["harness_version"] == "githi-v1"
            sid = body["session_id"]

            assert tracker.create_calls == []

            runtime = state.manager._registry.get(sid)
            assert runtime is not None
            assert runtime.seams is not None
            assert isinstance(runtime.seams.tool_provider, GithiToolProvider)
            names = {t.name for t in runtime.seams.tool_provider.list_tools()}
            assert {"read", "write", "ls"} <= names

            mr = await client.post(
                f"/v1/sessions/{sid}/messages",
                json={"text": "list workspace", "command_id": "githi-1"},
                headers=HEADERS,
            )
            assert mr.status_code == 202, mr.text

            payloads = await _drain_until_run_end(state.manager, sid)
            start = payloads.index("RunStart")
            end = payloads.index("RunEnd", start)
            assert payloads[start : end + 1] == _OFFLINE_ORDER

            envs = await state.reader.read(sid, since=-1)
            starts = [e.payload for e in envs if isinstance(e.payload, ToolStart)]
            ends = [e.payload for e in envs if isinstance(e.payload, ToolEnd)]
            assert starts and ends
            assert starts[0].tool_name == "workspace_probe"
            assert ends[0].tool_name == "workspace_probe"
            assert starts[0].tool_call_id == ends[0].tool_call_id
            assert ends[0].is_error is False

            seqs = [e.seq for e in envs]
            assert seqs == list(range(len(seqs)))

            st = await runtime.get_state()
            assert st.settled is True
            assert st.message_count >= 2

            from supervisor.api.events import iter_sse

            buf = ""
            stop = (await state.manager.snapshot_events(sid))[-1][0]
            async for chunk in iter_sse(
                session_id=sid,
                cursor=-1,
                manager=state.manager,
                reader=state.reader,
                max_events=stop + 1,
            ):
                buf += chunk
            compact = buf.replace(" ", "")
            assert "run_end" in compact
            assert not re.search(r"(?i)(api[_-]?key|sk-[a-z0-9]|openrouter)", buf)

            detail = await client.get(f"/v1/sessions/{sid}", headers=HEADERS)
            assert detail.status_code == 200
            assert detail.json()["status"] == "active"
            assert detail.json().get("is_busy") in (False, None)

            dr = await client.delete(f"/v1/sessions/{sid}", headers=HEADERS)
            assert dr.status_code == 204


@pytest.mark.asyncio
async def test_githi_no_sandbox_on_create(tmp_path) -> None:
    tracker = TrackingProvider()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'nosb.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=tracker,
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            cr = await client.post(
                "/v1/sessions",
                json={"harness_version": "githi-v1", "runtime": "githi"},
                headers=HEADERS,
            )
            assert cr.status_code == 201, cr.text
            sid = cr.json()["session_id"]
            await client.post(
                f"/v1/sessions/{sid}/messages",
                json={"text": "hi", "command_id": "c-nosb"},
                headers=HEADERS,
            )
            await _drain_until_run_end(state.manager, sid)
            assert tracker.create_calls == []


@pytest.mark.asyncio
async def test_githi_duplicate_command_id_sends_once(tmp_path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'dedup.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            sid = (
                await client.post(
                    "/v1/sessions",
                    json={"harness_version": "githi-v1", "runtime": "githi"},
                    headers=HEADERS,
                )
            ).json()["session_id"]
            r1 = await client.post(
                f"/v1/sessions/{sid}/messages",
                json={"text": "one", "command_id": "same"},
                headers=HEADERS,
            )
            r2 = await client.post(
                f"/v1/sessions/{sid}/messages",
                json={"text": "two", "command_id": "same"},
                headers=HEADERS,
            )
            assert r1.status_code == 202
            assert r2.status_code == 202
            assert r1.json() == r2.json()
            payloads = await _drain_until_run_end(state.manager, sid)
            assert payloads.count("RunEnd") == 1


@pytest.mark.asyncio
async def test_githi_manager_offline_turn_event_order(tmp_path) -> None:
    """SessionManager path (no HTTP) — same offline journal contract."""
    tracker = TrackingProvider()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'mgr.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=tracker,
        )
        sid = await state.manager.create(
            tenant_id="t1",
            harness_version="githi-v1",
            runtime_name="githi",
        )
        assert tracker.create_calls == []
        await state.manager.send(sid, "probe", command_id="m1")
        payloads = await _drain_until_run_end(state.manager, sid)
        start = payloads.index("RunStart")
        end = payloads.index("RunEnd", start)
        assert payloads[start : end + 1] == _OFFLINE_ORDER
        envs = await state.reader.read(sid, since=-1)
        assert any(
            isinstance(e.payload, ToolStart)
            and e.payload.tool_name == "workspace_probe"
            for e in envs
        )
        assert any(isinstance(e.payload, RunEnd) for e in envs)
        await state.manager.terminate(sid)
