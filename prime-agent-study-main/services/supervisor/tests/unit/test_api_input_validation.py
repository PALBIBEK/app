"""Malformed input is a client error, never a 500.

Found by sending hostile input to a live stack: a 10k-character command id (it
becomes part of a Temporal update id, which has a limit), a deeply nested JSON
body (echoing it in the validation error recursed past Python's limit), and a NUL
byte in a session id (Postgres refused it) each answered 500.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog

pytestmark = pytest.mark.asyncio
ALICE = {"X-Tenant-Id": "t1", "X-Principal-Id": "alice"}


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}") as (_, factory):
        state = build_supervisor(session_factory=factory, catalog=make_catalog(), blob_dir=tmp_path / "b")
        transport = ASGITransport(app=create_app(state=state))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("command id too long", {"json": {"text": "hi", "command_id": "x" * 10_000}}),
        ("NUL in the text", {"json": {"text": "a\u0000b", "command_id": "c1"}}),
        ("NUL in the command id", {"json": {"text": "hi", "command_id": "c\u00001"}}),
        ("deeply nested body",
         {"content": ('{"text":' + "[" * 5000 + "]" * 5000 + "}").encode(),
          "headers": {"Content-Type": "application/json"}}),
    ],
)
async def test_malformed_messages_are_422(client: AsyncClient, name: str, kwargs: dict) -> None:
    headers = ALICE | kwargs.pop("headers", {})
    r = await client.post("/v1/sessions/s1/messages", headers=headers, **kwargs)
    assert r.status_code == 422, (name, r.status_code, r.text[:200])


async def test_a_nul_in_the_url_is_400(client: AsyncClient) -> None:
    assert (await client.get("/v1/sessions/%00", headers=ALICE)).status_code == 400
    assert (await client.get("/v1/sessions?x=%00", headers=ALICE)).status_code == 400
