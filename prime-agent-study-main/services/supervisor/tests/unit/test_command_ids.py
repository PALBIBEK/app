"""A command id is an idempotency key: a retry repeats the request, it cannot change it (F33)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog

HEADERS = {"X-Tenant-Id": "t1"}


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'cmd.db'}") as (_, factory):
        state = build_supervisor(session_factory=factory, catalog=make_catalog(), blob_dir=tmp_path / "b")
        async with AsyncClient(transport=ASGITransport(app=create_app(state=state)), base_url="http://t") as c:
            yield c


async def test_a_retry_with_the_same_message_is_the_same_turn_and_another_message_is_refused(
    client: AsyncClient,
) -> None:
    sid = (await client.post("/v1/sessions", json={"harness_version": "kg-v1", "runtime": "echo"}, headers=HEADERS)).json()["session_id"]
    first = await client.post(f"/v1/sessions/{sid}/messages", json={"text": "hi", "command_id": "c1"}, headers=HEADERS)
    retry = await client.post(f"/v1/sessions/{sid}/messages", json={"text": "hi", "command_id": "c1"}, headers=HEADERS)
    assert first.status_code == retry.status_code == 202
    assert retry.json()["turn_id"] == first.json()["turn_id"]
    other = await client.post(f"/v1/sessions/{sid}/messages", json={"text": "bye", "command_id": "c1"}, headers=HEADERS)
    assert other.status_code == 409
