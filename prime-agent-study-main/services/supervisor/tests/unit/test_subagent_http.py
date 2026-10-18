"""Phase 2 step 5 — subagent HTTP routes + per-composition ACP bindings."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import SubagentEnd, SubagentStart
from supervisor.api.schemas import SubagentCommandRequest
from supervisor.catalog.binder import bind_runtime_seams
from supervisor.catalog.resolve import resolve_composition
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import AgentComposition, DigestRef, PlugBindings
from supervisor.harness import materialize_harness_binds
from supervisor.main import create_app
from supervisor.seams.providers.acp_subagent import AcpSubagentProvider
from tests.support.session_env import make_catalog
from tests.unit.test_catalog_pins import _offer_composition, _sample_prompt_pack, _sample_tool_set


def test_composition_pins_acp_url_over_global_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_BASE_URL", "http://global-acp.test")
    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    tools = cat.offer(_sample_tool_set())
    comp = cat.offer(
        AgentComposition(
            name="acp-pinned",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            plug_bindings=PlugBindings(acp_base_url="http://pinned-acp.test:8091"),
        )
    )
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = bind_runtime_seams(pins, cat)
    acp = seams.subagent_transport._registry.get("acp")  # noqa: SLF001
    assert isinstance(acp, AcpSubagentProvider)
    assert acp._base == "http://pinned-acp.test:8091"  # noqa: SLF001


def test_materialize_writes_subagent_rpc_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_BASE_URL", "http://supervisor.test:9090")
    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    tools = cat.offer(_sample_tool_set())
    comp = cat.offer(
        AgentComposition(
            name="sub-rpc",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            plug_bindings=PlugBindings(acp_base_url="http://acp.test:8091"),
        )
    )
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = bind_runtime_seams(pins, cat)
    yal = materialize_harness_binds(
        tmp_path,
        pins,
        seams,
        session_id="sess-sub",
        tenant_id="tenant-9",
    )
    raw = json.loads((yal / "subagent_rpc.json").read_text(encoding="utf-8"))
    assert raw["base_url"] == "http://supervisor.test:9090"
    assert raw["session_id"] == "sess-sub"
    assert raw["tenant_id"] == "tenant-9"
    assert "acp" in raw["providers"]
    assert raw["default_provider"] == "acp"
    assert raw["spawn_path"] == "/v1/sessions/sess-sub/subagents/spawn"


@pytest.mark.asyncio
async def test_manager_spawn_child_journals(session_env) -> None:
    from supervisor.agent.factory import create_runtime

    m = session_env["manager"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    result = await m.spawn_child(
        sid,
        tenant_id="t1",
        provider="memory",
        request=__import__(
            "supervisor.seams.types", fromlist=["SubagentRequest"]
        ).SubagentRequest(prompt="hello child", description="task"),
    )
    assert result.status == "ok"
    events = await session_env["reader"].read(sid, since=-1)
    assert isinstance(events[0].payload, SubagentStart)
    assert isinstance(events[1].payload, SubagentEnd)
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
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state


@pytest.mark.asyncio
async def test_api_spawn_child_round_trip(api) -> None:
    client, state = api
    headers = {"X-Tenant-Id": "t1"}
    create = await client.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "runtime": "echo"},
        headers=headers,
    )
    sid = create.json()["session_id"]
    body = SubagentCommandRequest(
        prompt="do work",
        description="via http",
        provider="memory",
    ).model_dump()
    resp = await client.post(
        f"/v1/sessions/{sid}/subagents/spawn",
        json=body,
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["child_session_id"]
    assert data["status"] == "ok"
    listed = await client.get(f"/v1/sessions/{sid}/children", headers=headers)
    assert listed.status_code == 200
    children = listed.json()
    assert len(children) == 1
    assert children[0]["activity"] == "done"
    await state.manager.terminate(sid)


@pytest.mark.asyncio
async def test_api_spawn_uses_acp_when_composition_pinned(api) -> None:
    client, state = api
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    pack = cat.get("prompt_pack", comp.prompt_pack.id, comp.prompt_pack.digest)
    tools = cat.get("tool_set", comp.tool_set.id, comp.tool_set.digest)
    plugged = cat.offer(
        AgentComposition(
            name="acp-http",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            plug_bindings=PlugBindings(acp_base_url="http://acp-pinned.test"),
        )
    )
    state.composition_catalog = cat
    state.manager._composition_catalog = cat
    headers = {"X-Tenant-Id": "t1"}
    create = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "kg-v1",
            "runtime": "echo",
            "composition_id": plugged.name,
        },
        headers=headers,
    )
    sid = create.json()["session_id"]
    seams = state.manager._seams[sid]  # noqa: SLF001
    acp = seams.subagent_transport._registry.get("acp")  # noqa: SLF001
    with patch.object(
        acp,
        "start_one_shot",
        return_value=__import__(
            "supervisor.seams.types", fromlist=["SubagentRunResult"]
        ).SubagentRunResult(text="acp-done", child_id="acp-c1", status="ok"),
    ):
        resp = await client.post(
            f"/v1/sessions/{sid}/subagents/spawn",
            json=SubagentCommandRequest(
                prompt="remote",
                provider="acp",
            ).model_dump(),
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["text"] == "acp-done"
    assert resp.json()["child_session_id"] == "acp-c1"
    await state.manager.terminate(sid)
