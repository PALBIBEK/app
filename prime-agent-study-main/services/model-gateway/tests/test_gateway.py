"""Independence + API tests — run with only this package on PYTHONPATH."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_gateway.app import app


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def authed_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", "secret-token")
    monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    with TestClient(app) as c:
        yield c


def test_mg_u1_healthz(client: TestClient) -> None:
    r = client.get("/v1/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_mg_u2_chat_completions_echo(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "echo",
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "echo:ping" in body["choices"][0]["message"]["content"]
    assert body["model"] == "echo"


def test_mg_u3_unknown_route(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "no-such-route",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["error"]["code"] == "ROUTE_UNKNOWN"


def test_mg_u4_auth_required(authed_client: TestClient) -> None:
    r = authed_client.post(
        "/v1/chat/completions",
        json={"model": "echo", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 401
    r2 = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret-token"},
        json={"model": "echo", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r2.status_code == 200


def test_mg_u5_list_routes(client: TestClient) -> None:
    r = client.get("/v1/routes")
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()["routes"]}
    assert "echo" in ids
    assert "githi" in ids


def test_mg_u6_githi_route_resolves_without_key_then_fails_upstream(
    client: TestClient,
) -> None:
    """Route is registered; missing OPENROUTER_API_KEY fails closed (no leak)."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "githi",
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    # Provider raises RuntimeError → gateway surfaces 502 UPSTREAM_ERROR.
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["error"]["code"] == "UPSTREAM_ERROR"
    assert "OPENROUTER_API_KEY" in detail["error"]["message"]


def test_mg_c2_no_supervisor_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if "import supervisor" in text or "from supervisor" in text:
            offenders.append(str(path))
    assert offenders == []


def test_mg_i1_round_trip(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"X-YAL-Route-Id": "fake"},
        json={"messages": [{"role": "user", "content": "solo-dev"}]},
    )
    assert r.status_code == 200
    assert "solo-dev" in r.json()["choices"][0]["message"]["content"]

def test_auth_required_without_token_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)
    with TestClient(app) as c:
        r = c.post(
            "/v1/chat/completions",
            json={"model": "echo", "messages": [{"role": "user", "content": "x"}]},
        )
        assert r.status_code == 503
        assert r.json()["detail"]["error"]["code"] == "AUTH_UNCONFIGURED"


def test_routes_report_their_context_window(client: TestClient) -> None:
    rows = {row["id"]: row for row in client.get("/v1/routes").json()["routes"]}
    assert rows["githi"]["context_window"] == 200_000


def test_one_route_is_readable_by_id(client: TestClient) -> None:
    r = client.get("/v1/routes/githi")
    assert r.status_code == 200
    assert r.json() == {
        "id": "githi",
        "provider": "openrouter",
        "upstream_model": "anthropic/claude-sonnet-4",
        "context_window": 200_000,
    }
    missing = client.get("/v1/routes/nope")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "ROUTE_UNKNOWN"


def test_a_configured_route_may_omit_its_window_but_not_mistype_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MODEL_GATEWAY_ROUTES_JSON",
        '{"a": {"provider": "echo", "upstream_model": "x"},'
        ' "b": {"provider": "echo", "upstream_model": "y", "context_window": "big"}}',
    )
    with TestClient(app) as c:
        assert c.get("/v1/routes/a").json()["context_window"] is None
        with pytest.raises(ValueError, match="context_window must be a positive integer"):
            c.get("/v1/routes/b")
