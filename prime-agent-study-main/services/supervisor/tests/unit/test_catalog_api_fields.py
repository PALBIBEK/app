"""The catalog's fields over HTTP: metadata, usage, a session's pins, and what publish refuses.

Runs the API in-process on SQLite; sessions are inserted as rows (their pins are
what usage and the pins view read), so no worker is needed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.app import build_supervisor
from supervisor.catalog import SessionPins
from supervisor.catalog.loading.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.repository import hydrate_composition_catalog
from supervisor.catalog.services import resolve_composition
from supervisor.db.engine import open_db
from supervisor.db.models import SessionRow
from supervisor.main import create_app
from tests.support.auth import SECRET, ServiceAuth
from tests.support.platform import all_variants, shipped_harness_catalog
from tests.support.session_env import TrackingProvider

ALICE = ServiceAuth("t1", "alice")
T1 = ServiceAuth("t1")
T2 = ServiceAuth("t2")


@dataclass
class Api:
    client: AsyncClient
    factory: async_sessionmaker[AsyncSession]
    catalog: Any

    async def add_session(self, *, tenant: str, owner: str | None, composition: str, status: str = "active") -> str:
        pins: SessionPins = resolve_composition(self.catalog, composition_id=composition)
        sid = uuid4().hex
        async with self.factory() as db, db.begin():
            db.add(
                SessionRow(
                    id=sid,
                    tenant_id=tenant,
                    owner_principal_id=owner,
                    harness_version=pins.harness_version,
                    runtime="githi",
                    composition_pins=pins.to_dict(),
                    status=status,
                )
            )
        return sid


def _shipped(kind_dir: str, name: str) -> dict[str, Any]:
    body = json.loads((default_catalog_dir() / kind_dir / f"{name}.json").read_text())
    body.pop("digest", None)
    return body


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[Api]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}") as (_, factory):
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
        async with AsyncClient(transport=ASGITransport(app=create_app(state=state)), base_url="http://t") as client:
            yield Api(client, factory, compositions)


async def test_a_version_says_who_published_it_and_who_deprecated_it(api: Api) -> None:
    body = _shipped("prompt_packs", "coding-prompt") | {"name": "alice-prompt"}
    published = await api.client.post("/v1/catalog/prompt_pack/publish", json=body, headers=ALICE)
    assert published.status_code == 200, published.text
    out = published.json()
    assert out["metadata"]["created_by"] == "alice" and out["metadata"]["owner_id"] == "t1"
    assert out["protocol_version"] == 1

    path = f"/v1/catalog/prompt_pack/alice-prompt/{out['digest']}"
    assert (await api.client.post(f"{path}/deprecate", headers=T1)).status_code == 204
    read = (await api.client.get(path, headers=T1)).json()
    assert read["status"] == "deprecated"
    assert read["metadata"]["deprecated_by"] == "t1" and read["metadata"]["deprecated_at"]
    assert "metadata" not in read["body"]  # the body is content only


async def test_a_malformed_digest_is_a_422_before_any_lookup(api: Api) -> None:
    r = await api.client.get("/v1/catalog/composition/githi-v1/sha256:nothex", headers=T1)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_DIGEST"
    r = await api.client.post("/v1/catalog/composition/githi-v1/not-a-digest/deprecate", headers=T1)
    assert r.status_code == 422


async def test_errors_are_the_catalog_error_body_and_never_name_another_tenant(api: Api) -> None:
    body = _shipped("prompt_packs", "coding-prompt") | {"name": "taken"}
    assert (await api.client.post("/v1/catalog/prompt_pack/publish", json=body, headers=T1)).status_code == 200
    r = await api.client.post("/v1/catalog/prompt_pack/publish", json=body | {"semver": "2.0.0"}, headers=T2)
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert set(detail) == {"code", "message", "retryable", "detail"}
    assert detail["code"] == "CATALOG_OWNERSHIP_REQUIRED" and "t1" not in json.dumps(detail)


async def test_publish_refuses_a_sticky_harness_on_a_sandbox_that_may_not_be_kept(api: Api) -> None:
    profile = _shipped("sandbox_profiles", "githi-docker-v1") | {"name": "throwaway", "sticky_eligible": False}
    r = await api.client.post("/v1/catalog/sandbox_profile/publish", json=profile, headers=T1)
    assert r.status_code == 200, r.text
    comp = _shipped("compositions", "githi-tools-v1") | {
        "name": "t1-throwaway",
        "sandbox_profile": {"id": "throwaway", "digest": r.json()["digest"]},
    }
    check = (await api.client.post("/v1/catalog/composition/check", json=comp, headers=T1)).json()
    failed = [c for c in check["checks"] if not c["ok"]]
    assert [c["code"] for c in failed] == ["COMPOSITION_INCOMPATIBLE"]
    refused = await api.client.post("/v1/catalog/composition/publish", json=comp, headers=T1)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "COMPOSITION_INCOMPATIBLE"


async def test_usage_counts_the_callers_sessions_by_version(api: Api) -> None:
    mine = await api.add_session(tenant="t1", owner="alice", composition="githi-v1")
    await api.add_session(tenant="t1", owner="alice", composition="githi-v1", status="terminated")
    await api.add_session(tenant="t1", owner="bob", composition="githi-v1")  # another user of the tenant
    await api.add_session(tenant="t2", owner=None, composition="githi-v1")  # another tenant
    await api.add_session(tenant="t1", owner="alice", composition="githi-tools-v1")

    usage = (await api.client.get("/v1/catalog/composition/githi-v1/usage", headers=ALICE)).json()
    assert usage["session_count"] == 2 and usage["active_sessions"] == 1 and usage["unique_principals"] == 1
    assert [v["sessions"] for v in usage["versions"]] == [2] and usage["last_used_at"]

    tenant_wide = (await api.client.get("/v1/catalog/composition/githi-v1/usage", headers=T1)).json()
    assert tenant_wide["session_count"] == 3 and tenant_wide["unique_principals"] == 2

    # A part is counted through every composition that pins it.
    prompt = (await api.client.get("/v1/catalog/prompt_pack/coding-prompt/usage", headers=T1)).json()
    assert prompt["session_count"] == 4
    assert (await api.client.get("/v1/catalog/composition/nope/usage", headers=T1)).status_code == 404
    assert mine


async def test_a_session_shows_what_it_runs_part_by_part(api: Api) -> None:
    sid = await api.add_session(tenant="t1", owner="alice", composition="githi-tools-v1")
    r = await api.client.get(f"/v1/sessions/{sid}/pins", headers=ALICE)
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["composition_id"] == "githi-tools-v1" and view["composition_status"] == "offered"
    roles = {p["role"]: p for p in view["parts"]}
    assert set(roles) == {"prompt", "tools", "hand:default", "memory"}
    assert roles["hand:default"]["settings"]["provider"] == "local_docker"
    assert "gateway_base_url" not in json.dumps(view)  # internal addresses are not shown

    assert (await api.client.get(f"/v1/sessions/{sid}/pins", headers=ServiceAuth("t1", "bob"))).status_code == 404
    assert (await api.client.get(f"/v1/sessions/{sid}/pins", headers=T2)).status_code == 404
