"""Without a memory service there is no long-term memory, and the supervisor says so.

Sessions still run (the implicit default notebook is simply absent), but a client
that names notebooks is refused rather than silently given none, and the memory
API answers 503. There is no second, local notebook store to fall back on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from supervisor.app import SupervisorState, build_supervisor
from supervisor.db.engine import open_db
from supervisor.db.models import SessionRow
from supervisor.main import create_app
from supervisor.memory.binding import MemoryBinding
from supervisor.memory.client import UnavailableNotebooks
from tests.support.session_env import make_catalog

pytestmark = pytest.mark.asyncio

ALICE = {"X-Tenant-Id": "t1", "X-Principal-Id": "alice"}


@pytest_asyncio.fixture
async def api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, SupervisorState]]:
    monkeypatch.delenv("MEMORY_SERVICE_URL", raising=False)
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory, catalog=make_catalog(), blob_dir=tmp_path / "blobs"
        )
        assert state.memory is not None and isinstance(state.memory.store, UnavailableNotebooks)
        transport = ASGITransport(app=create_app(state=state))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state


async def test_a_session_runs_without_long_term_memory(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, state = api
    created = await client.post("/v1/sessions", json={"harness_version": "kg-v1"}, headers=ALICE)
    assert created.status_code == 201, created.text
    async with state.manager._db() as db:
        row = await db.get(SessionRow, created.json()["session_id"])
    assert row is not None and row.memory
    binding = MemoryBinding.from_dict(row.memory)
    assert (binding.write, binding.read) == (None, ())


async def test_notebooks_a_client_names_are_refused_not_dropped(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, _ = api
    created = await client.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "memory": {"write": "work", "read": ["work"]}},
        headers=ALICE,
    )
    assert created.status_code == 503, created.text
    assert "memory service" in created.text


async def test_the_memory_api_says_memory_is_unavailable(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, _ = api
    listed = await client.get("/v1/memory/notebooks", params={"agent_pin": "p"}, headers=ALICE)
    assert listed.status_code == 503, listed.text
