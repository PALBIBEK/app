"""D2 gateway readiness — /v1/readyz vs process-only /v1/healthz."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from model_gateway.app import app

_ECHO_ONLY = json.dumps(
    {
        "echo": {"provider": "echo", "upstream_model": "echo"},
        "fake": {"provider": "echo", "upstream_model": "fake"},
    }
)


@pytest.fixture()
def echo_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", _ECHO_ONLY)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def default_routes_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Default routes include openrouter githi without a vendor key."""
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with TestClient(app) as c:
        yield c


def test_readyz_ok_echo_only(echo_client: TestClient) -> None:
    r = echo_client.get("/v1/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_readyz_503_credentials_when_vendor_key_missing(
    default_routes_client: TestClient,
) -> None:
    r = default_routes_client.get("/v1/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["reason"] == "credentials"


def test_healthz_still_200_when_readyz_not_ready(
    default_routes_client: TestClient,
) -> None:
    assert default_routes_client.get("/v1/readyz").status_code == 503
    hz = default_routes_client.get("/v1/healthz")
    assert hz.status_code == 200
    assert hz.json()["status"] == "ok"


def test_readyz_503_auth_when_token_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", _ECHO_ONLY)
    with TestClient(app) as c:
        r = c.get("/v1/readyz")
        assert r.status_code == 503
        assert r.json()["reason"] == "auth"


def test_echo_only_routes_auto_enable_githi_when_openrouter_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staging pins echo-only JSON; setting OPENROUTER_API_KEY must unlock githi."""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", "staging-gateway-token")
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", _ECHO_ONLY)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(app) as c:
        assert c.get("/v1/readyz").status_code == 200
        routes = c.get(
            "/v1/routes",
            headers={"Authorization": "Bearer staging-gateway-token"},
        ).json()["routes"]
        ids = {r["id"] for r in routes}
        assert "echo" in ids
        assert "githi" in ids
        assert any(r["id"] == "githi" and r["provider"] == "openrouter" for r in routes)


def test_echo_only_routes_stay_echo_without_vendor_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", _ECHO_ONLY)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(app) as c:
        assert c.get("/v1/readyz").status_code == 200
        routes = c.get("/v1/routes").json()["routes"]
        ids = {r["id"] for r in routes}
        assert ids == {"echo", "fake"}
        assert "githi" not in ids
        assert "openai" not in ids
