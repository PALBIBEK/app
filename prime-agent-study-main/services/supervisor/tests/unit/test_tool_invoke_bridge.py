"""Phase 2 step 4 — tool invoke bridge (catalog handlers + session HTTP RPC)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import ToolEnd, ToolStart
from supervisor.api.schemas import InvokeToolRequest
from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.catalog.store import CompositionCatalog
from supervisor.main import create_app
from supervisor.seams.types import ToolInvokeContext
from supervisor.session.manager import ToolInvokeNotConfigured
from tests.support.session_env import make_catalog
from tests.unit.test_catalog_pins import _offer_composition, _sample_tool_set


def test_echo_invoke_returns_text_payload() -> None:
    cat = CompositionCatalog()
    ts = cat.offer(_sample_tool_set())
    provider = CatalogToolProvider(ts)
    result = provider.invoke(ToolInvokeContext(), "echo", {"text": "hello"})
    assert result.ok is True
    assert result.payload == {"text": "hello"}


def test_echo_invoke_defaults_empty_text() -> None:
    cat = CompositionCatalog()
    ts = cat.offer(_sample_tool_set())
    provider = CatalogToolProvider(ts)
    result = provider.invoke(ToolInvokeContext(), "echo", {})
    assert result.ok is True
    assert result.payload == {"text": ""}


def test_catalog_tool_without_handler_not_implemented() -> None:
    cat = CompositionCatalog()
    ts = cat.offer(_sample_tool_set(tool_names=("echo", "grep")))
    provider = CatalogToolProvider(ts)
    result = provider.invoke(ToolInvokeContext(), "grep", {"pattern": "x"})
    assert result.ok is False
    assert result.code == "TOOL_NOT_IMPLEMENTED"


def test_materialize_writes_tool_invoke_json(tmp_path: Path, monkeypatch) -> None:
    from supervisor.catalog.binder import bind_runtime_seams
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.harness import materialize_harness_binds

    monkeypatch.setenv("SUPERVISOR_BASE_URL", "http://supervisor.test:9090")
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = bind_runtime_seams(pins, cat)
    yal = materialize_harness_binds(
        tmp_path,
        pins,
        seams,
        session_id="sess-abc",
        tenant_id="tenant-1",
    )
    raw = json.loads((yal / "tool_invoke.json").read_text(encoding="utf-8"))
    assert raw["url"] == "http://supervisor.test:9090/v1/sessions/sess-abc/tools/invoke"
    assert raw["tenant_id"] == "tenant-1"


@pytest.mark.asyncio
async def test_manager_invoke_tool_journals_start_end(session_env) -> None:
    from unittest.mock import patch

    from supervisor.agent.factory import create_runtime

    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    session_env["manager"]._composition_catalog = cat
    m = session_env["manager"]
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="kg-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )
    outcome = await m.invoke_tool(
        sid,
        tenant_id="t1",
        name="echo",
        arguments={"text": "journaled"},
        tool_call_id="tc-1",
    )
    assert outcome.result.ok is True
    assert outcome.tool_call_id == "tc-1"
    events = await session_env["reader"].read(sid, since=-1)
    types = [type(e.payload).__name__ for e in events]
    assert types.count("ToolStart") == 1
    assert types.count("ToolEnd") == 1
    start = next(e for e in events if isinstance(e.payload, ToolStart))
    end = next(e for e in events if isinstance(e.payload, ToolEnd))
    assert start.payload.tool_name == "echo"
    assert end.payload.result == {"text": "journaled"}
    assert end.payload.is_error is False
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_manager_invoke_without_composition_raises(session_env) -> None:
    m = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    with pytest.raises(ToolInvokeNotConfigured):
        await m.invoke_tool(sid, tenant_id="t1", name="echo", arguments={})
    await m.terminate(sid)


@pytest.fixture
async def api(tmp_path):
    from supervisor.app import build_supervisor
    from supervisor.db.engine import open_db

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            live_queue_max=32,
        )
        cat = CompositionCatalog()
        comp, _, _ = _offer_composition(cat)
        state.composition_catalog = cat
        state.manager._composition_catalog = cat
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state


@pytest.mark.asyncio
async def test_api_invoke_tool_round_trip(api) -> None:
    client, state = api
    headers = {"X-Tenant-Id": "t1"}
    create = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "kg-v1",
            "runtime": "echo",
            "composition_id": "coding-v1",
        },
        headers=headers,
    )
    assert create.status_code == 201
    sid = create.json()["session_id"]
    invoke = await client.post(
        f"/v1/sessions/{sid}/tools/invoke",
        json=InvokeToolRequest(name="echo", arguments={"text": "via-api"}).model_dump(),
        headers=headers,
    )
    assert invoke.status_code == 200
    body = invoke.json()
    assert body["ok"] is True
    assert body["payload"] == {"text": "via-api"}
    assert body["tool_call_id"]
    await state.manager.terminate(sid)
