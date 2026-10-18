"""Shared helpers for independent D4 acceptance against the supervisor HTTP API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.api import events as events_mod
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog

TENANT_A = "d4-tenant-a"
TENANT_B = "d4-tenant-b"

HEADERS_A = {"X-Tenant-Id": TENANT_A}
HEADERS_B = {"X-Tenant-Id": TENANT_B}

# Echo harness from make_catalog — no composition digest required by default.
CREATE_BODY = {
    "harness_version": "kg-v1",
    "runtime": "echo",
}


@pytest.fixture(autouse=True)
def _fast_sse_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid 20s hangs when closing SSE streams in acceptance tests."""
    monkeypatch.setattr(events_mod, "_HEARTBEAT_SECS", 0.05)


def parse_sse_json_events(raw: str) -> list[dict[str, Any]]:
    """Extract JSON payloads from SSE `data:` lines (ignore comments/heartbeats)."""
    out: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        payload = "\n".join(data_lines).strip()
        if not payload:
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return out


@pytest.fixture
async def supervisor_stack(tmp_path) -> AsyncIterator[dict[str, Any]]:
    """ASGI client + SupervisorState for session/SSE acceptance."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd4.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            live_queue_max=2,
        )
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "state": state,
                "factory": factory,
                "app": app,
            }


async def create_session(
    client: AsyncClient,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    r = await client.post(
        "/v1/sessions",
        headers=headers or HEADERS_A,
        json=body or CREATE_BODY,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def wait_for_journal(
    reader: Any,
    session_id: str,
    *,
    min_events: int = 1,
    require_type: str | None = "RunEnd",
    timeout_secs: float = 3.0,
) -> list[Any]:
    """Poll journal until fan-out has persisted events (send returns before append)."""
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout_secs
    last: list[Any] = []
    while asyncio.get_event_loop().time() < deadline:
        last = await reader.read(session_id, since=-1)
        if len(last) >= min_events:
            if require_type is None:
                return last
            if any(type(e.payload).__name__ == require_type for e in last):
                return last
        await asyncio.sleep(0.02)
    return last
