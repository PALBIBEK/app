"""Phase 2 step 7 — catalog persist API + DB-backed store."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.catalog.repository import hydrate_composition_catalog, persist_offer
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import AgentComposition, DigestRef
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog
from tests.unit.test_catalog_pins import _offer_composition, _sample_prompt_pack, _sample_tool_set


@pytest.mark.asyncio
async def test_repository_hydrate_restores_deprecation(tmp_path) -> None:
    from supervisor.catalog.errors import NotOffered
    from supervisor.catalog.repository import persist_deprecate

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}") as (_, factory):
        cat = CompositionCatalog()
        comp, _, _ = _offer_composition(cat)
        await persist_offer(factory, cat, "composition", comp.name, comp.digest)
        cat.deprecate("composition", comp.name, comp.digest)
        await persist_deprecate(factory, "composition", comp.name, comp.digest)

        fresh = CompositionCatalog()
        n = await hydrate_composition_catalog(fresh, factory)
        assert n == 1
        with pytest.raises(NotOffered):
            fresh.get("composition", comp.name, comp.digest, require_offered=True)


@pytest.fixture
async def catalog_api(tmp_path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}") as (_, factory):
        cat = CompositionCatalog()
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            composition_catalog=cat,
            blob_dir=tmp_path / "blobs",
        )
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state, cat, factory


@pytest.mark.asyncio
async def test_api_publish_prompt_pack(catalog_api) -> None:
    client, _, cat, factory = catalog_api
    headers = {"X-Tenant-Id": "t1"}
    pack_body = {
        "name": "api-pack",
        "semver": "1.0.0",
        "protocol_version": 1,
        "status": "offered",
        "persona": "api",
        "sections": [
            {"id": "s1", "order": 1, "text": "API prompt.", "complete": True},
        ],
        "vars": [],
    }
    resp = await client.post(
        "/v1/catalog/prompt_pack/publish",
        json=pack_body,
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["artifact_id"] == "api-pack"
    assert data["digest"].startswith("sha256:")
    assert data["status"] == "offered"
    offered = cat.get("prompt_pack", data["artifact_id"], data["digest"])
    assert offered.persona == "api"
    n = await hydrate_composition_catalog(CompositionCatalog(), factory)
    assert n >= 1


@pytest.mark.asyncio
async def test_api_deprecate_blocks_session_create(catalog_api) -> None:
    client, state, cat, _factory = catalog_api
    headers = {"X-Tenant-Id": "t1"}
    pack = cat.offer(_sample_prompt_pack(name="dep-pack"))
    tools = cat.offer(_sample_tool_set(name="dep-tools"))
    comp = cat.offer(
        AgentComposition(
            name="dep-comp",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="kg-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
        )
    )
    dep = await client.post(
        f"/v1/catalog/composition/{comp.name}/{comp.digest}/deprecate",
        headers=headers,
    )
    assert dep.status_code == 204
    create = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "kg-v1",
            "runtime": "echo",
            "composition_id": comp.name,
        },
        headers=headers,
    )
    assert create.status_code == 409


@pytest.mark.asyncio
async def test_api_list_compositions(catalog_api) -> None:
    client, _, cat, _ = catalog_api
    headers = {"X-Tenant-Id": "t1"}
    _offer_composition(cat)
    listed = await client.get("/v1/catalog/compositions", headers=headers)
    assert listed.status_code == 200
    names = {row["artifact_id"] for row in listed.json()}
    assert "coding-v1" in names
