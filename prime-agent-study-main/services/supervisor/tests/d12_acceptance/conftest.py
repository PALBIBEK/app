"""Shared ASGI / journal helpers for D12 blind acceptance (subagents + HA)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog

TENANT = "d12-tenant"
HEADERS = {"X-Tenant-Id": TENANT}

CREATE_BODY = {
    "harness_version": "kg-v1",
    "runtime": "echo",
}

# Harness githi-v1 documents max_depth: 3; enforce against that product default.
HARNESS_MAX_DEPTH = 3

AI_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def _fast_sse_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid 20s hangs when closing SSE streams."""
    from supervisor.api import events as events_mod

    monkeypatch.setattr(events_mod, "_HEARTBEAT_SECS", 0.05)


def resolve_lab_provider(state: Any | None = None) -> str:
    """Prefer ``lab_echo`` when registered; else stock echo stub name ``memory``."""
    if state is not None:
        manager = getattr(state, "manager", None)
        seams_map = getattr(manager, "_seams", None) if manager is not None else None
        if isinstance(seams_map, dict):
            for seams in seams_map.values():
                transport = getattr(seams, "subagent_transport", None)
                registry = getattr(transport, "_registry", None)
                if registry is not None and hasattr(registry, "names"):
                    names = set(registry.names())
                    if "lab_echo" in names:
                        return "lab_echo"
                    if "memory" in names:
                        return "memory"
        fallback = getattr(manager, "_fallback_seams", None) if manager else None
        transport = getattr(fallback, "subagent_transport", None) if fallback else None
        registry = getattr(transport, "_registry", None)
        if registry is not None and hasattr(registry, "names"):
            names = set(registry.names())
            if "lab_echo" in names:
                return "lab_echo"
            if "memory" in names:
                return "memory"
    # Provider module rename path
    providers = (
        AI_ROOT
        / "services"
        / "supervisor"
        / "src"
        / "supervisor"
        / "seams"
        / "providers"
    )
    if (providers / "lab_echo_subagent.py").is_file():
        return "lab_echo"
    return "memory"


async def _build_stack(
    tmp_path: Path,
    factory,
    *,
    auth_required: bool = False,
    service_jwt_secret: str | None = None,
    live_queue_max: int = 32,
) -> dict[str, Any]:
    state = build_supervisor(
        session_factory=factory,
        catalog=make_catalog(),
        blob_dir=tmp_path / "blobs",
        live_queue_max=live_queue_max,
        auth_required=auth_required,
        service_jwt_secret=service_jwt_secret,
    )
    app = create_app(state=state)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return {
        "client": client,
        "state": state,
        "factory": factory,
        "app": app,
        "tmp_path": tmp_path,
        "reader": state.reader,
        "manager": state.manager,
        "headers": HEADERS,
    }


@pytest.fixture
async def supervisor_stack(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    """Single-process ASGI client + SupervisorState (shared journal DB)."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd12.db'}") as (_, factory):
        stack = await _build_stack(tmp_path, factory)
        try:
            yield stack
        finally:
            await stack["client"].aclose()


@pytest.fixture
async def dual_peer_stacks(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    """Two supervisors sharing one journal DB + blob dir — cold-resume sims."""
    shared_blobs = tmp_path / "shared_blobs"
    shared_blobs.mkdir(parents=True, exist_ok=True)

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd12_peers.db'}") as (
        _,
        factory,
    ):

        async def _peer(label: str) -> dict[str, Any]:
            peer_tmp = tmp_path / label
            peer_tmp.mkdir(parents=True, exist_ok=True)
            state = build_supervisor(
                session_factory=factory,
                catalog=make_catalog(),
                blob_dir=shared_blobs,
                live_queue_max=8,
            )
            app = create_app(state=state)
            client = AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            )
            return {
                "client": client,
                "state": state,
                "factory": factory,
                "app": app,
                "tmp_path": peer_tmp,
                "reader": state.reader,
                "manager": state.manager,
                "headers": HEADERS,
            }

        a = await _peer("a")
        b = await _peer("b")
        try:
            yield {"a": a, "b": b, "factory": factory, "tmp_path": tmp_path}
        finally:
            await a["client"].aclose()
            await b["client"].aclose()


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


async def wait_for_journal(
    reader: Any,
    session_id: str,
    *,
    min_events: int = 1,
    require_types: set[str] | None = None,
    timeout_secs: float = 3.0,
) -> list[Any]:
    """Poll journal until min events (and optional payload type names) appear."""
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


def parse_sse_json_events(raw: str) -> list[dict[str, Any]]:
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


def spawn_body(
    *,
    prompt: str = "d12 child work",
    provider: str | None = None,
    parent_depth: int = 0,
    persona: str | None = None,
    description: str = "d12",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "prompt": prompt,
        "description": description,
        "provider": provider or "memory",
        "parent_depth": parent_depth,
    }
    if persona is not None:
        body["persona"] = persona
    return body


def new_command_id(prefix: str = "d12") -> str:
    return f"{prefix}-{uuid4().hex}"
