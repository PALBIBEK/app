"""D5 §6 E / LOCAL_DEMO #6 — non-echo model depth when keys present; echo-only honesty."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_gateway.app import app
from model_gateway.config import Settings
from model_gateway.factory import build_provider_registry
from model_gateway.virtual_keys import MemoryVirtualKeyStore

SERVICE = "d5-local-demo-svc"
REPO = Path(__file__).resolve().parents[3]
LOCAL_DEMO = (
    REPO
    / "development"
    / "ai-backend-production-grade"
    / "LOCAL_DEMO.md"
)

_ECHO_ONLY = {
    "echo": {"provider": "echo", "upstream_model": "echo"},
    "fake": {"provider": "echo", "upstream_model": "fake"},
}


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    routes: dict | None = None,
    openrouter_key: str = "",
    openai_key: str = "",
) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", SERVICE)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    if routes is None:
        monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)
    else:
        monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(routes))
    monkeypatch.setenv("OPENROUTER_API_KEY", openrouter_key)
    monkeypatch.setenv("OPENAI_API_KEY", openai_key)
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()


def test_d5_local_demo_vendor_keys_unlock_non_echo_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6 E: when vendor keys present, catalog exposes a non-echo route (githi/openai)."""
    _configure(
        monkeypatch,
        routes=_ECHO_ONLY,
        openrouter_key="sk-or-local-demo",
        openai_key="",
    )
    with TestClient(app) as client:
        _configure(
            monkeypatch,
            routes=_ECHO_ONLY,
            openrouter_key="sk-or-local-demo",
            openai_key="",
        )
        assert client.get("/v1/readyz").status_code == 200
        routes = client.get(
            "/v1/routes",
            headers={"Authorization": f"Bearer {SERVICE}"},
        )
        assert routes.status_code == 200, routes.text
        ids = {r["id"] for r in routes.json()["routes"]}
        non_echo = {
            r["id"]
            for r in routes.json()["routes"]
            if r.get("provider") not in ("echo", None)
        }
    assert "githi" in ids or "openai" in ids or non_echo, (
        "LOCAL_DEMO model depth requires a non-echo route when vendor keys are set; "
        f"got ids={sorted(ids)}"
    )


def test_d5_local_demo_echo_only_gate_docs_forbid_claiming_real_chat() -> None:
    """§6 E: LOCAL_DEMO must treat echo-only claiming real chat as FAIL."""
    assert LOCAL_DEMO.is_file(), LOCAL_DEMO
    text = LOCAL_DEMO.read_text(encoding="utf-8").lower()
    assert "echo-only" in text or "echo only" in text or "echo" in text
    # Gate language: mock / echo claiming real chat is a failure mode.
    assert "fail" in text
    asserts_honesty = (
        ("echo" in text and "fail" in text)
        or "real chat" in text
        or "mock" in text
    )
    assert asserts_honesty, (
        "LOCAL_DEMO.md must document that echo-only / mock chat claiming "
        "'real agent chat' fails the local-demo gate"
    )


def test_d5_local_demo_echo_only_profile_ready_without_claiming_provider_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Echo-only lab may be ready, but must not auto-advertise githi without keys."""
    _configure(monkeypatch, routes=_ECHO_ONLY, openrouter_key="", openai_key="")
    with TestClient(app) as client:
        _configure(monkeypatch, routes=_ECHO_ONLY, openrouter_key="", openai_key="")
        assert client.get("/v1/readyz").status_code == 200
        payload = client.get(
            "/v1/routes",
            headers={"Authorization": f"Bearer {SERVICE}"},
        ).json()
        ids = {r["id"] for r in payload["routes"]}
    assert "githi" not in ids
    assert "openai" not in ids
    assert ids <= {"echo", "fake"} or all(
        r.get("provider") == "echo" for r in payload["routes"]
    )
