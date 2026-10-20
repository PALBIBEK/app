"""A tenant's catalog artifacts are its own; shipped artifacts are everyone's and no one's to change.

The first tenant to publish a name owns it. Another tenant cannot publish a
version under it, deprecate it, read it, list it or start a session from a
composition that pins it, and every refusal but one reads as "not offered" so
a name's owner isn't disclosed. The one exception: changing a shipped
artifact is a 403, since everyone can already see it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.catalog.loading.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.repository import hydrate_composition_catalog
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.auth import SECRET, ServiceAuth
from tests.support.platform import all_variants, shipped_harness_catalog
from tests.support.session_env import TrackingProvider

T1, T2 = ServiceAuth("t1"), ServiceAuth("t2")
Boot = Callable[[], Any]


def _shipped(kind_dir: str, name: str) -> dict[str, Any]:
    body = json.loads((default_catalog_dir() / kind_dir / f"{name}.json").read_text())
    body.pop("digest", None)
    return body


@pytest.fixture
async def boot(tmp_path: Path) -> AsyncIterator[Callable[[], Any]]:
    """Start an API process on a shared database; call again for a second process."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'tenancy.db'}") as (_, factory):
        clients: list[AsyncClient] = []

        async def start() -> AsyncClient:
            compositions = bootstrap_composition_catalog(default_catalog_dir())
            await hydrate_composition_catalog(compositions, factory)
            state = build_supervisor(
                session_factory=factory,
                catalog=all_variants(shipped_harness_catalog()),
                blob_dir=tmp_path / "blobs",
                composition_catalog=compositions,
                sandbox_provider=TrackingProvider(),
                auth_required=True,
                service_jwt_secret=SECRET,
            )
            client = AsyncClient(transport=ASGITransport(app=create_app(state=state)), base_url="http://t")
            clients.append(client)
            return client

        yield start
        for client in clients:
            await client.aclose()


async def _publish(client: AsyncClient, headers: dict[str, str], kind: str, body: dict[str, Any]) -> Any:
    return await client.post(f"/v1/catalog/{kind}/publish", json=body, headers=headers)


async def test_a_name_belongs_to_the_tenant_that_published_it_first(boot: Boot) -> None:
    client = await boot()
    mine = _shipped("prompt_packs", "coding-prompt") | {"name": "t1-prompt"}
    r = await _publish(client, T1, "prompt_pack", mine)
    assert r.status_code == 200, r.text
    digest = r.json()["digest"]

    hijack = await _publish(client, T2, "prompt_pack", mine | {"semver": "9.9.9"})
    assert hijack.status_code == 403
    assert (await client.get(f"/v1/catalog/prompt_pack/t1-prompt/{digest}", headers=T2)).status_code == 404
    deprecate = f"/v1/catalog/prompt_pack/t1-prompt/{digest}/deprecate"
    assert (await client.post(deprecate, headers=T2)).status_code == 404
    assert (await client.get(f"/v1/catalog/prompt_pack/t1-prompt/{digest}", headers=T1)).status_code == 200
    assert (await client.post(deprecate, headers=T1)).status_code == 204


async def test_compositions_are_listed_only_to_their_tenant(boot: Boot) -> None:
    client = await boot()
    r = await _publish(client, T1, "composition", _shipped("compositions", "githi-v1") | {"name": "t1-agent"})
    assert r.status_code == 200, r.text
    t1 = {a["artifact_id"] for a in (await client.get("/v1/catalog/compositions", headers=T1)).json()}
    t2 = {a["artifact_id"] for a in (await client.get("/v1/catalog/compositions", headers=T2)).json()}
    assert {"t1-agent", "githi-v1"} <= t1
    assert "githi-v1" in t2 and "t1-agent" not in t2


async def test_another_tenant_cannot_start_a_session_from_my_composition(boot: Boot) -> None:
    client = await boot()
    r = await _publish(client, T1, "composition", _shipped("compositions", "githi-v1") | {"name": "t1-private"})
    digest = r.json()["digest"]
    create = {"harness_version": "githi-v1", "composition_id": "t1-private", "composition_digest": digest}
    refused = await client.post("/v1/sessions", json=create, headers=T2)
    assert refused.status_code in (404, 409), refused.text
    assert "NOT_OFFERED" in refused.text or "not offered" in refused.text.lower()


async def test_shipped_artifacts_are_read_only_to_tenants(boot: Boot) -> None:
    client = await boot()
    digest = (await client.get("/v1/catalog/compositions", headers=T1)).json()
    shipped = next(a for a in digest if a["artifact_id"] == "githi-v1")
    r = await client.post(f"/v1/catalog/composition/githi-v1/{shipped['digest']}/deprecate", headers=T1)
    assert r.status_code == 403
    variant = _shipped("compositions", "githi-v1") | {"semver": "9.9.9"}
    assert (await _publish(client, T1, "composition", variant)).status_code == 403


async def test_ownership_survives_a_restart(boot: Boot) -> None:
    first = await boot()
    body = _shipped("prompt_packs", "coding-prompt") | {"name": "durable-name"}
    assert (await _publish(first, T1, "prompt_pack", body)).status_code == 200
    second = await boot()  # a new process: ownership comes back from the database
    assert (await _publish(second, T2, "prompt_pack", body | {"semver": "9.9.9"})).status_code == 403
