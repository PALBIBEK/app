"""Shared ASGI + projector helpers for D13 blind acceptance (production tracing)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from supervisor.obs import tracing as tracing_mod
from supervisor.obs.tracing import setup_tracing, shutdown_tracing
from supervisor.telemetry_contract.fakes import FakeSpanSink
from tests.support.session_env import make_catalog

TENANT = "d13-tenant"
HEADERS = {"X-Tenant-Id": TENANT}

CREATE_BODY = {
    "harness_version": "kg-v1",
    "runtime": "echo",
}

# Repo roots for compose / docs file asserts (T5).
SUPERVISOR_ROOT = Path(__file__).resolve().parents[2]
AI_ROOT = SUPERVISOR_ROOT.parents[1]  # prime-agent-study-main/


@pytest.fixture(autouse=True)
def _fast_sse_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor.api import events as events_mod

    monkeypatch.setattr(events_mod, "_HEARTBEAT_SECS", 0.05)


@pytest.fixture(autouse=True)
def _reset_global_projector() -> AsyncIterator[None]:
    """Isolate global OTLP projector between tests (production uses process singleton)."""
    tracing_mod._projector = None
    tracing_mod._provider = None
    yield
    shutdown_tracing()
    tracing_mod._projector = None
    tracing_mod._provider = None


async def _build_stack(
    tmp_path: Path,
    factory,
    *,
    sink: FakeSpanSink | None = None,
) -> dict[str, Any]:
    chosen = sink or FakeSpanSink(validate=False)
    setup_tracing(
        tenant_id=TENANT,
        harness_id="kg",
        harness_version="kg-v1",
        sink=chosen,
    )
    state = build_supervisor(
        session_factory=factory,
        catalog=make_catalog(),
        blob_dir=tmp_path / "blobs",
        live_queue_max=32,
    )
    app = create_app(state=state)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return {
        "client": client,
        "state": state,
        "factory": factory,
        "app": app,
        "tmp_path": tmp_path,
        "reader": state.reader,
        "manager": state.manager,
        "headers": HEADERS,
        "sink": chosen,
        "projector": tracing_mod.get_projector(),
    }


@pytest.fixture
async def supervisor_stack(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    """ASGI client + FakeSpanSink wired through setup_tracing (fan-out path)."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd13.db'}") as (_, factory):
        stack = await _build_stack(tmp_path, factory)
        try:
            yield stack
        finally:
            await stack["client"].aclose()


async def create_session(
    client: AsyncClient,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    r = await client.post(
        "/v1/sessions",
        headers=headers or HEADERS,
        json=body or CREATE_BODY,
    )
    assert r.status_code == 201, r.text
    return r.json()


def w3c_traceparent(trace_id: str, *, span_id: str | None = None) -> str:
    """Build a valid W3C traceparent for inbound seeding."""
    sid = span_id or uuid4().hex[:16]
    return f"00-{trace_id.lower()}-{sid}-01"


async def wait_for_journal(
    reader: Any,
    session_id: str,
    *,
    min_events: int = 1,
    require_types: set[str] | None = None,
    timeout_secs: float = 3.0,
) -> list[Any]:
    deadline = asyncio.get_event_loop().time() + timeout_secs
    last: list[Any] = []
    while asyncio.get_event_loop().time() < deadline:
        last = await reader.read(session_id, since=-1)
        if len(last) >= min_events:
            if not require_types:
                return last
            names = {type(e.payload).__name__ for e in last}
            if require_types.issubset(names):
                return last
        await asyncio.sleep(0.02)
    return last


def journal_payloads(events: list[Any], cls: type) -> list[Any]:
    return [e.payload for e in events if isinstance(e.payload, cls)]


def new_command_id(prefix: str = "d13") -> str:
    return f"{prefix}-{uuid4().hex}"


def span_attr_blob(spans: list[Any]) -> str:
    """Flatten span attributes for content-leak asserts (T8)."""
    parts: list[str] = []
    for s in spans:
        parts.append(repr(getattr(s, "attributes", {}) or {}))
        parts.append(repr(getattr(s, "name", "")))
    return "\n".join(parts)
