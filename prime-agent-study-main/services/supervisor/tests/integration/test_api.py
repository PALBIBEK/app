"""API integration: round trip, dedup across restart, leak checks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog

HEADERS = {"X-Tenant-Id": "t1"}
SRC = Path(__file__).resolve().parents[2] / "src" / "supervisor"


@pytest.mark.asyncio
async def test_echo_full_round_trip(tmp_path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'i.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            cr = await client.post(
                "/v1/sessions",
                json={"harness_version": "kg-v1", "runtime": "echo"},
                headers=HEADERS,
            )
            assert cr.status_code == 201
            sid = cr.json()["session_id"]

            # SSE over ASGITransport deadlocks; assert stream body via iter_sse.
            from supervisor.api.events import iter_sse

            mr = await client.post(
                f"/v1/sessions/{sid}/messages",
                json={"text": "ping", "command_id": "round-1"},
                headers=HEADERS,
            )
            assert mr.status_code == 202
            await asyncio.sleep(0.15)

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
            assert '"type":"run_end"' in buf.replace(" ", "") or "run_end" in buf

            dr = await client.delete(f"/v1/sessions/{sid}", headers=HEADERS)
            assert dr.status_code == 204


@pytest.mark.asyncio
async def test_dedup_survives_process_restart(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"
    blobs = tmp_path / "blobs"

    async with open_db(db_url) as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=blobs,
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            sid = (
                await client.post(
                    "/v1/sessions",
                    json={"harness_version": "kg-v1", "runtime": "echo"},
                    headers=HEADERS,
                )
            ).json()["session_id"]
            r1 = await client.post(
                f"/v1/sessions/{sid}/messages",
                json={"text": "hi", "command_id": "persist-me"},
                headers=HEADERS,
            )
            assert r1.status_code == 202
            body1 = r1.json()
            await asyncio.sleep(0.15)
            payloads_before = await state.manager.event_payloads(sid)

    async with open_db(db_url) as (_, factory2):
        state2 = build_supervisor(
            session_factory=factory2,
            catalog=make_catalog(),
            blob_dir=blobs,
        )
        app2 = create_app(state=state2)
        async with AsyncClient(
            transport=ASGITransport(app=app2), base_url="http://test"
        ) as client:
            r2 = await client.post(
                f"/v1/sessions/{sid}/messages",
                json={"text": "hi", "command_id": "persist-me"},
                headers=HEADERS,
            )
            assert r2.status_code == 202
            assert r2.json() == body1
            await asyncio.sleep(0.1)
            payloads_after = await state2.manager.event_payloads(sid)
            assert payloads_after.count("RunEnd") == payloads_before.count("RunEnd")




def test_api_leak_checks() -> None:
    api = SRC / "api"
    runtime_leaks: list[str] = []
    for path in api.rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "runtimes" in line:
                runtime_leaks.append(f"{path.name}:{i}:{line.strip()}")
    assert runtime_leaks == [], runtime_leaks
    for path_name in ("sessions.py", "health.py", "schemas.py"):
        text = (api / path_name).read_text()
        assert "journal" not in text, path_name
