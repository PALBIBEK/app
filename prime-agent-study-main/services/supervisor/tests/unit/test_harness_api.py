"""A user discovers, reads and registers harnesses through the API (F4, F5, F6, F12).

And publish refuses configurations that can never work (F11, F17, F28).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.catalog.loading.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.repository import hydrate_composition_catalog
from supervisor.db.engine import open_db
from supervisor.harness.repository import hydrate_harness_catalog
from supervisor.main import create_app
from supervisor.models import ModelUnavailable, reset_model_router, use_model_router
from tests.support.auth import SECRET, ServiceAuth
from tests.support.platform import all_variants, shipped_harness_catalog
from tests.support.session_env import TrackingProvider

VERSIONS = Path(__file__).resolve().parents[4] / "harness" / "versions"


T1, T2 = ServiceAuth("t1"), ServiceAuth("t2")


def _kg(version: str, **changes: Any) -> dict[str, Any]:
    manifest = yaml.safe_load((VERSIONS / "kg-v1.yaml").read_text())
    return manifest | {"harness_version": version} | changes


@pytest.fixture
async def boot(tmp_path: Path) -> AsyncIterator[Callable[[], Any]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        clients: list[AsyncClient] = []

        async def start() -> AsyncClient:
            compositions = bootstrap_composition_catalog(default_catalog_dir())
            await hydrate_composition_catalog(compositions, factory)
            harnesses = all_variants(shipped_harness_catalog())
            await hydrate_harness_catalog(harnesses, factory)
            state = build_supervisor(
                session_factory=factory,
                catalog=harnesses,
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


async def test_a_user_lists_and_reads_the_shipped_harnesses(boot: Callable[[], Any]) -> None:
    client = await boot()
    listed = {h["harness_version"]: h for h in (await client.get("/v1/harness_versions", headers=T1)).json()}
    assert {"githi-v1", "kg-v1"} <= set(listed)
    assert listed["githi-v1"]["owner"] == "shipped" and listed["githi-v1"]["runtime"] == "githi"
    one = (await client.get("/v1/harness_versions/githi-v1", headers=T1)).json()
    assert one["manifest"]["harness_version"] == "githi-v1" and "budget" in one["manifest"]


async def test_a_registered_harness_runs_sessions_in_any_process_and_only_for_its_tenant(
    boot: Callable[[], Any],
) -> None:
    api = await boot()
    r = await api.post("/v1/harness_versions", json=_kg("t1-kg", budget={"max_cost_per_session": 1.5}), headers=T1)
    assert r.status_code == 201, r.text
    assert r.json()["manifest"]["budget"]["max_cost_per_session"] == 1.5
    again = await api.post("/v1/harness_versions", json=_kg("t1-kg", budget={"max_cost_per_session": 1.5}), headers=T1)
    assert again.status_code == 200  # idempotent
    changed = await api.post("/v1/harness_versions", json=_kg("t1-kg", budget={"max_cost_per_session": 9}), headers=T1)
    assert changed.status_code == 409  # immutable once registered

    worker = await boot()  # another process, booted after the registration
    created = await worker.post("/v1/sessions", json={"harness_version": "t1-kg"}, headers=T1)
    assert created.status_code == 201, created.text

    assert (await api.get("/v1/harness_versions/t1-kg", headers=T2)).status_code == 404
    assert "t1-kg" not in {h["harness_version"] for h in (await api.get("/v1/harness_versions", headers=T2)).json()}
    refused = await api.post("/v1/sessions", json={"harness_version": "t1-kg"}, headers=T2)
    assert refused.status_code in (404, 422), refused.text


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"loop": {"max_steps": 501}}, "loop.max_steps"),
        (
            {
                "loop": {"max_steps": 30, "hard_run_seconds": 300},
                "approvals": {"timeout_s": 300, "on_timeout": "deny", "require_approval": ["echo"]},
            },
            "approvals.timeout_s",
        ),
    ],
    ids=["more-steps-than-one-history-holds", "approval-wait-outlasts-the-hard-limit"],
)
async def test_a_new_version_is_refused_what_cannot_work(
    boot: Callable[[], Any], changes: dict[str, Any], field: str
) -> None:
    api = await boot()
    r = await api.post("/v1/harness_versions", json=_kg("t1-bad", **changes), headers=T1)
    assert r.status_code == 422 and field in r.text, r.text


def test_a_stored_version_past_those_checks_still_reads() -> None:
    """A session pinned to a version registered before the checks keeps working."""
    from supervisor.harness.storage.converters import manifest_from_dict

    stored = manifest_from_dict(_kg("t1-old", loop={"max_steps": 900}))
    assert stored.loop is not None and stored.loop.max_steps == 900


async def test_shipped_versions_cannot_be_overwritten_or_deprecated(boot: Callable[[], Any]) -> None:
    client = await boot()
    assert (await client.post("/v1/harness_versions", json=_kg("kg-v1"), headers=T1)).status_code == 409
    assert (await client.post("/v1/harness_versions/kg-v1/deprecate", headers=T1)).status_code == 403
    await client.post("/v1/harness_versions", json=_kg("t1-old"), headers=T1)
    assert (await client.post("/v1/harness_versions/t1-old/deprecate", headers=T1)).status_code == 204
    refused = await client.post("/v1/sessions", json={"harness_version": "t1-old"}, headers=T1)
    assert refused.status_code in (404, 409, 422), refused.text


async def test_a_published_artifact_can_be_read_back_to_change_it(boot: Callable[[], Any]) -> None:
    client = await boot()
    body = json.loads((default_catalog_dir() / "prompt_packs" / "coding-prompt.json").read_text())
    body = {k: v for k, v in body.items() if k != "digest"} | {"name": "t1-prompt"}
    digest = (await client.post("/v1/catalog/prompt_pack/publish", json=body, headers=T1)).json()["digest"]
    read = (await client.get(f"/v1/catalog/prompt_pack/t1-prompt/{digest}", headers=T1)).json()
    assert read["body"]["sections"] == body["sections"]


@pytest.mark.parametrize(
    ("kind", "change", "reason"),
    [
        ("memory_profile", {"summarization_model": "x"}, "unknown field(s): summarization_model"),
        ("memory_profile", {"compaction": {"enabled": True, "keep_recent_tokens": 1, "typo": 1}}, "compaction.typo"),
        ("memory_profile", {"recall": {"enabled": True, "backend": "mcp"}}, "needs recall.mcp_server_url"),
    ],
)
async def test_publish_refuses_settings_that_would_be_ignored(
    boot: Callable[[], Any], kind: str, change: dict[str, Any], reason: str
) -> None:
    client = await boot()
    base = json.loads((default_catalog_dir() / "memory_profiles" / "default.json").read_text())
    body = {k: v for k, v in base.items() if k != "digest"} | {"name": "bad"} | change
    r = await client.post(f"/v1/catalog/{kind}/publish", json=body, headers=T1)
    assert r.status_code == 422 and reason in r.text, r.text


async def test_publish_refuses_a_model_the_gateway_does_not_serve(boot: Callable[[], Any]) -> None:
    def resolve(model: str) -> object:
        if model != "served":
            raise ModelUnavailable(model)
        return object()

    stub = SimpleNamespace(
        refresh=lambda: None,
        resolve=resolve,
        catalog=SimpleNamespace(admitted=SimpleNamespace(chat_models=lambda include_aliased=False: ["served"])),
    )
    use_model_router(stub)  # type: ignore[arg-type]
    try:
        client = await boot()
        comp = json.loads((default_catalog_dir() / "compositions" / "githi-v1.json").read_text())
        comp = {k: v for k, v in comp.items() if k != "digest"} | {"name": "t1-agent"}
        comp["plug_bindings"] = dict(comp["plug_bindings"], model_route="nope")
        r = await client.post("/v1/catalog/composition/publish", json=comp, headers=T1)
        assert r.status_code == 422 and "not served" in r.text, r.text
        comp["plug_bindings"]["model_route"] = "served"
        assert (await client.post("/v1/catalog/composition/publish", json=comp, headers=T1)).status_code == 200
    finally:
        reset_model_router()


def _shipped_composition(name: str) -> dict[str, Any]:
    comp = json.loads((default_catalog_dir() / "compositions" / "githi-v1.json").read_text())
    return {k: v for k, v in comp.items() if k != "digest"} | {"name": name}


@pytest.mark.parametrize(
    ("url", "refusal"),
    [
        ("http://169.254.169.254/latest/meta-data", "does not trust"),  # cloud metadata
        ("http://localhost:5432", "does not trust"),
        ("file:///etc/passwd", "must be an http(s) URL"),
    ],
)
async def test_publish_refuses_a_service_url_on_a_host_the_platform_does_not_trust(
    boot: Callable[[], Any], monkeypatch: pytest.MonkeyPatch, url: str, refusal: str
) -> None:
    """Workers call a composition's service URLs: an untrusted one would have them call
    anything they can reach, and an MCP server's error body comes back to the caller."""
    monkeypatch.delenv("PLUG_ALLOWED_HOSTS", raising=False)
    client = await boot()
    for field in ("gateway_base_url", "mcp_server_url", "acp_base_url"):
        comp = _shipped_composition(f"t1-{field.replace('_', '-')}")
        comp["plug_bindings"] = dict(comp["plug_bindings"], **{field: url})
        r = await client.post("/v1/catalog/composition/publish", json=comp, headers=T1)
        assert r.status_code == 422 and refusal in r.text and f"plug_bindings.{field}" in r.text, r.text


async def test_publish_refuses_a_recall_server_on_an_untrusted_host(
    boot: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PLUG_ALLOWED_HOSTS", raising=False)
    client = await boot()
    profile = json.loads((default_catalog_dir() / "memory_profiles" / "default.json").read_text())
    profile = {k: v for k, v in profile.items() if k not in ("digest", "status")} | {"name": "t1-recall"}
    profile["recall"] = {"enabled": True, "backend": "mcp", "mcp_server_url": "http://10.0.0.5:8080"}
    r = await client.post("/v1/catalog/memory_profile/publish", json=profile, headers=T1)
    assert r.status_code == 422 and "recall.mcp_server_url" in r.text and "does not trust" in r.text, r.text


async def test_the_shipped_binding_is_trusted_and_an_operator_can_trust_more(
    boot: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PLUG_ALLOWED_HOSTS", raising=False)
    client = await boot()
    shipped = _shipped_composition("t1-shipped-binding")
    assert (await client.post("/v1/catalog/composition/publish", json=shipped, headers=T1)).status_code == 200

    team = _shipped_composition("t1-team-mcp")
    team["plug_bindings"] = dict(team["plug_bindings"], mcp_server_url="https://mcp.team.example/rpc")
    assert (await client.post("/v1/catalog/composition/publish", json=team, headers=T1)).status_code == 422
    monkeypatch.setenv("PLUG_ALLOWED_HOSTS", "mcp.team.example")
    assert (await client.post("/v1/catalog/composition/publish", json=team, headers=T1)).status_code == 200


async def test_a_user_lists_the_tools_a_tool_set_can_name(boot: Callable[[], Any]) -> None:
    client = await boot()
    tools = {t["name"]: t for t in (await client.get("/v1/tools", headers=T1)).json()}
    assert {"read", "write", "bash", "edit", "remember"} <= set(tools)
    assert tools["read"]["tool_class"] == "pure" and "path" in json.dumps(tools["read"]["parameters"])
