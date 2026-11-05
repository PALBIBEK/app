"""A route the gateway cannot call until someone sets its provider up answers 424 with a code
and a reason a person can act on, never as a passing upstream failure the caller retries."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.gateway import app as gateway_app
from supervisor.gateway.vendors import VENDORS

TOKEN = "svc-token-for-setup-error-tests"
SVC = {"Authorization": f"Bearer {TOKEN}"}
CALL = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 10}


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    for vendor in VENDORS.values():
        monkeypatch.delenv(vendor.key_env, raising=False)
        monkeypatch.delenv(vendor.base_url_env, raising=False)
    for name in ("AUTH_REQUIRED", "ENV", "MODEL_GATEWAY_KEYS_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", TOKEN)
    monkeypatch.setenv("MODEL_GATEWAY_KEYS_FILE", str(tmp_path / "keys.json"))
    routes = {
        "claude": {"provider": "anthropic", "upstream_model": "claude-x"},
        "mine": {"provider": "local", "upstream_model": "qwen3:8b"},
    }
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(routes))
    with TestClient(gateway_app.app) as client:
        yield client


def _error(response: httpx.Response) -> dict[str, str]:
    return dict(response.json()["detail"]["error"])


def test_a_vendor_without_a_key_says_so(gateway: TestClient) -> None:
    answer = gateway.post("/v1/chat/completions", headers=SVC, json={**CALL, "model": "claude"})

    assert answer.status_code == 424
    assert _error(answer) == {
        "code": "PROVIDER_UNCONFIGURED",
        "vendor": "anthropic",
        "message": "Anthropic has no API key: an admin can add it in Policies & secrets",
    }


def test_a_local_model_without_a_server_says_so(gateway: TestClient) -> None:
    answer = gateway.post("/v1/chat/completions", headers=SVC, json={**CALL, "model": "mine"})

    assert answer.status_code == 424 and _error(answer)["code"] == "PROVIDER_UNCONFIGURED"
    assert _error(answer)["message"].startswith("no local model server is set up")


def test_a_local_server_that_does_not_answer_says_so(gateway: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway.put("/v1/vendors/local/base-url", headers=SVC, json={"base_url": "http://127.0.0.1:9/v1", "updated_by": "u1"})
    local = gateway.app.state.registry.get("local")  # type: ignore[attr-defined]

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(local._http, "transport", httpx.MockTransport(down))
    monkeypatch.setattr(local._http, "retries", 0)

    answer = gateway.post("/v1/chat/completions", headers=SVC, json={**CALL, "model": "mine"})

    assert answer.status_code == 424
    assert _error(answer) == {"code": "LOCAL_UNREACHABLE", "vendor": "local", "message": "the local model server is not answering"}
