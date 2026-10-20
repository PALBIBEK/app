"""Routes that address a logical session by id answer only its own tenant (others: 404)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from memory_service.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("MEMORY_SERVICE_TOKEN", "mem-secret")
    monkeypatch.setenv("MEMORY_ALLOW_STATIC_TOKEN", "1")
    for name in ("SERVICE_JWT_SECRET", "MEMORY_SERVICE_JWT_SECRET", "AUTH_REQUIRED", "ENV"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(database_url="sqlite+aiosqlite:///:memory:")) as c:
        yield c


def _as(tenant: str) -> dict[str, str]:
    return {"Authorization": "Bearer mem-secret", "X-Tenant-Id": tenant}


def test_another_tenant_cannot_touch_a_session_by_id(client: TestClient) -> None:
    created = client.post(
        "/v1/sessions",
        json={"scope": {"tenant": "a", "subject": "s", "namespace": "n"}},
        headers=_as("a"),
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    attempts = {
        "load": client.get(f"/v1/sessions/{sid}", headers=_as("b")),
        "buffer": client.post(
            f"/v1/sessions/{sid}/events:buffer",
            json={"events": [{"type": "note", "data": {}}]},
            headers=_as("b"),
        ),
        "flush": client.post(f"/v1/sessions/{sid}/flush", json={}, headers=_as("b")),
        "fork": client.post(f"/v1/sessions/{sid}/fork", json={}, headers=_as("b")),
        "lease": client.post(f"/v1/sessions/{sid}/lease", json={"owner": "x"}, headers=_as("b")),
        "archive": client.post(f"/v1/sessions/{sid}/archive", headers=_as("b")),
    }
    assert {k: r.status_code for k, r in attempts.items()} == dict.fromkeys(attempts, 404)
    assert client.get(f"/v1/sessions/{sid}", headers=_as("a")).status_code == 200


def test_creating_a_session_in_another_tenant_is_refused(client: TestClient) -> None:
    r = client.post(
        "/v1/sessions",
        json={"scope": {"tenant": "a", "subject": "s", "namespace": "n"}},
        headers=_as("b"),
    )
    assert r.status_code == 403
