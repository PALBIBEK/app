"""D5 §6 A / T1–T4 — fail-closed auth, readyz, echo happy path."""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from model_gateway import app as app_module
from model_gateway.app import app

from .helpers import SERVICE_TOKEN, configure_app


def test_t1_prod_empty_token_fail_closed_on_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T1 / §6 A: empty MODEL_GATEWAY_TOKEN in prod → chat fails closed (not open)."""
    configure_app(monkeypatch, token="", env="production")
    with TestClient(app) as client:
        configure_app(monkeypatch, token="", env="production")
        r = client.post(
            "/v1/chat/completions",
            json={"model": "echo", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code in (401, 503)
    body = r.json()
    detail = body.get("detail") or body
    err = detail.get("error") if isinstance(detail, dict) else None
    code = (err or {}).get("code") if isinstance(err, dict) else None
    assert code in ("AUTH_UNCONFIGURED", "AUTH_REQUIRED", "AUTH_INVALID")


def test_t1_auth_required_empty_token_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6 A: AUTH_REQUIRED=true with empty token → protected routes fail closed."""
    configure_app(monkeypatch, token="", env="development", auth_required=True)
    with TestClient(app) as client:
        configure_app(monkeypatch, token="", env="development", auth_required=True)
        health = client.get("/v1/healthz")
        assert health.status_code == 200
        chat = client.post(
            "/v1/chat/completions",
            json={"model": "echo", "messages": [{"role": "user", "content": "x"}]},
        )
        assert chat.status_code in (401, 503)


def test_constant_time_token_compare_uses_hmac() -> None:
    """§6 A: service token compare is constant-time (hmac.compare_digest)."""
    src = inspect.getsource(app_module._bearer_matches)
    assert "compare_digest" in src
    assert app_module._bearer_matches("abcd", "abcd") is True
    assert app_module._bearer_matches("abcd", "abce") is False
    assert app_module._bearer_matches("short", "longer!") is False


def test_t2_valid_service_token_echo_ok(
    client: TestClient, auth_header: dict[str, str]
) -> None:
    """T2: valid service token + echo → 200."""
    r = client.post(
        "/v1/chat/completions",
        headers=auth_header,
        json={"model": "echo", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"].startswith("echo:")


def test_t3_wrong_token_401(client: TestClient) -> None:
    """T3: wrong bearer → 401."""
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer wrong-token-not-service"},
        json={"model": "echo", "messages": [{"role": "user", "content": "nope"}]},
    )
    assert r.status_code == 401
    detail = r.json().get("detail") or {}
    err = detail.get("error") if isinstance(detail, dict) else {}
    assert (err or {}).get("code") in ("AUTH_INVALID", "AUTH_REQUIRED")


def test_t4_githi_without_openrouter_readyz_or_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T4: githi without OpenRouter key → readyz 503 credentials and/or chat 502."""
    configure_app(monkeypatch, openrouter_key="", openai_key="")
    with TestClient(app) as client:
        configure_app(monkeypatch, openrouter_key="", openai_key="")
        ready = client.get("/v1/readyz")
        chat = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "model": "githi",
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
    # Documented: ready 503 (credentials) OR chat 502 (upstream / missing key).
    assert ready.status_code == 503 or chat.status_code in (502, 503)
    if ready.status_code == 503:
        assert ready.json().get("reason") in (
            "credentials",
            "auth",
            "provider",
            "routes",
        )
    if chat.status_code >= 400:
        detail = chat.json().get("detail") or {}
        err = detail.get("error") if isinstance(detail, dict) else {}
        assert (err or {}).get("code") in (
            "UPSTREAM_ERROR",
            "PROVIDER_MISSING",
            "AUTH_UNCONFIGURED",
        )


def test_readyz_echo_only_ready_without_vendor_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6 A / contract: echo-only route profile is ready without vendor API keys."""
    configure_app(
        monkeypatch,
        routes={"echo": {"provider": "echo", "upstream_model": "echo"}},
        openrouter_key="",
        openai_key="",
    )
    with TestClient(app) as client:
        configure_app(
            monkeypatch,
            routes={"echo": {"provider": "echo", "upstream_model": "echo"}},
            openrouter_key="",
            openai_key="",
        )
        r = client.get("/v1/readyz")
    assert r.status_code == 200
    assert r.json().get("status") == "ready"


def test_healthz_always_ok_when_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Liveness ≠ readiness: healthz stays 200 when credentials missing."""
    configure_app(monkeypatch, openrouter_key="")
    with TestClient(app) as client:
        configure_app(monkeypatch, openrouter_key="")
        assert client.get("/v1/healthz").status_code == 200
        ready = client.get("/v1/readyz")
        assert ready.status_code == 503
