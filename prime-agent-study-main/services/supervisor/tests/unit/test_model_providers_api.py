"""The supervisor's model-providers API, against the real gateway app.

The supervisor passes a key to the gateway once and keeps nothing; the gateway's answer, and its
refusal, reach the caller as they are; a change makes the supervisor re-read the gateway's models.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from supervisor.api import model_providers
from supervisor.gateway import app as gateway_app
from supervisor.gateway.key_check import KeyCheck
from supervisor.gateway.vendors import VENDORS
from supervisor.models import ModelRouter, reset_model_router, use_model_router
from supervisor.models.endpoints import GatewayConfig
from tests.support.auth import SECRET, ServiceAuth

TOKEN = "svc-token-for-provider-api-tests"
ADMIN = ServiceAuth("t1", "admin-1")
_ECHO = {
    "echo": {
        "provider": "echo",
        "upstream_model": "echo",
        "context_window": 1000,
        "max_output_tokens": 100,
        "input_cost_per_token": 0.001,
        "output_cost_per_token": 0.001,
    }
}


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    for vendor in VENDORS.values():
        monkeypatch.delenv(vendor.key_env, raising=False)
        monkeypatch.delenv(vendor.base_url_env, raising=False)
    for name in (
        "AUTH_REQUIRED",
        "ENV",
        "MODEL_GATEWAY_KEYS_SECRET",
        "MODEL_GATEWAY_API_KEY_HEADER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", TOKEN)
    monkeypatch.setenv("MODEL_GATEWAY_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(_ECHO))
    monkeypatch.setattr(gateway_app, "check_key", lambda vendor, key, base_url: KeyCheck.ACCEPTED)
    with TestClient(gateway_app.app) as client:
        yield client


@pytest.fixture
def supervisor(gateway: TestClient) -> Iterator[TestClient]:
    seen: list[httpx.Request] = []

    def relay(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = gateway.request(
            request.method, request.url.path, params=request.url.params,
            headers=request.headers, content=request.content,
        )  # fmt: skip
        return httpx.Response(answer.status_code, content=answer.content, headers=answer.headers)

    router = ModelRouter(
        GatewayConfig(
            url="http://model-gateway:8090", api_key=TOKEN, api_key_header="Authorization"
        ),
        transport=httpx.MockTransport(relay),
    )
    use_model_router(router)
    app = FastAPI()
    app.state.supervisor = SimpleNamespace(service_jwt_secret=SECRET)
    app.include_router(model_providers.router)
    try:
        with TestClient(app) as client:
            client.seen = seen  # type: ignore[attr-defined]
            yield client
    finally:
        reset_model_router()


def _models(supervisor: TestClient) -> list[str]:
    router = model_providers.model_router()
    assert router is not None
    return [spec.id for spec in router.selectable_models()]


def test_listing_shows_each_vendor_and_whether_keys_can_be_set(supervisor: TestClient) -> None:
    listing = supervisor.get("/v1/model-providers", headers=ADMIN).json()
    assert listing["editable"] is True
    assert [v["id"] for v in listing["vendors"]] == list(VENDORS)


def test_a_key_set_reaches_the_gateway_once_and_its_models_appear(supervisor: TestClient) -> None:
    assert _models(supervisor) == ["gateway/echo"]
    put = supervisor.put("/v1/model-providers/deepseek/key", headers=ADMIN, json={"key": "sk-d"})
    assert put.status_code == 200, put.text
    assert (put.json()["source"], put.json()["updated_by"], put.json()["ready"]) == (
        "set",
        "admin-1",
        True,
    )
    assert "sk-d" not in put.text
    assert _models(supervisor) == ["gateway/echo", "gateway/deepseek-flash"]

    removed = supervisor.delete("/v1/model-providers/deepseek/key", headers=ADMIN)
    assert removed.json()["source"] == "removed"
    assert _models(supervisor) == ["gateway/echo"]


def test_the_gateway_is_called_with_the_supervisors_key(supervisor: TestClient) -> None:
    supervisor.get("/v1/model-providers", headers=ADMIN)
    request = supervisor.seen[-1]  # type: ignore[attr-defined]
    assert (request.method, str(request.url)) == ("GET", "http://model-gateway:8090/v1/vendors")
    assert request.headers["authorization"] == TOKEN


def test_a_refused_key_comes_back_as_the_gateway_said(
    supervisor: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway_app, "check_key", lambda vendor, key, base_url: KeyCheck.REFUSED)
    put = supervisor.put("/v1/model-providers/openai/key", headers=ADMIN, json={"key": "bad"})
    assert put.status_code == 400
    assert put.json()["detail"] == {"code": "KEY_REFUSED", "message": "OpenAI refused this key"}


def test_an_unknown_vendor_is_404(supervisor: TestClient) -> None:
    put = supervisor.put("/v1/model-providers/nope/key", headers=ADMIN, json={"key": "k"})
    assert (put.status_code, put.json()["detail"]["code"]) == (404, "VENDOR_UNKNOWN")


def test_a_caller_without_a_service_identity_is_refused(supervisor: TestClient) -> None:
    assert supervisor.get("/v1/model-providers").status_code == 401
    assert supervisor.put("/v1/model-providers/openai/key", json={"key": "k"}).status_code == 401


def test_without_a_gateway_there_are_no_providers() -> None:
    use_model_router(None)
    app = FastAPI()
    app.state.supervisor = SimpleNamespace(service_jwt_secret=SECRET)
    app.include_router(model_providers.router)
    try:
        with TestClient(app) as client:
            got = client.get("/v1/model-providers", headers=ADMIN)
    finally:
        reset_model_router()
    assert (got.status_code, got.json()["detail"]["code"]) == (404, "NO_GATEWAY")
