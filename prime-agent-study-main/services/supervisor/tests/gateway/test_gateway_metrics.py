"""The gateway says, for alerting, how its model calls went and what they cost.

Nothing did: a provider failing every call, or spend running away, showed up only as
sessions failing or as the bill.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from supervisor.gateway import app as gateway
from supervisor.gateway.auth import Caller
from supervisor.gateway.virtual_keys import (
    MintRequest,
    SignedVirtualKeyStore,
    derive_virtual_key_secret,
)

SERVICE = {"Authorization": "Bearer svc-test"}
CHAT = {"model": "echo", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", "svc-test")
    monkeypatch.delenv("MODEL_GATEWAY_VIRTUAL_KEY_SECRET", raising=False)
    with TestClient(gateway.app) as client:
        yield client


def _metric(client: TestClient, series: str) -> float:
    text = client.get("/v1/metrics").text
    found = re.search(rf"^{re.escape(series)} (\S+)$", text, re.MULTILINE)
    return 0.0 if found is None else float(found.group(1))


def test_model_calls_are_counted_by_status_and_nothing_else_is(client: TestClient) -> None:
    ok, refused = 'yal_gateway_responses_total{code="200"}', 'yal_gateway_responses_total{code="401"}'
    before = _metric(client, ok), _metric(client, refused)
    mint = {"tenant_id": "t1", "session_id": "s1", "principal_id": "alice", "allowed_models": ["echo"]}
    token = client.post("/v1/virtual-keys", json=mint, headers=SERVICE).json()["token"]
    assert client.post("/v1/chat/completions", json=CHAT, headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post("/v1/chat/completions", json=CHAT, headers={"Authorization": "Bearer vk_forged.key"}).status_code == 401
    client.get("/v1/routes", headers=SERVICE)  # not a model call
    assert (_metric(client, ok), _metric(client, refused)) == (before[0] + 1, before[1] + 1)
    assert "tenant" not in client.get("/v1/metrics").text  # counts only


def test_every_charge_counts_toward_the_platforms_spend(client: TestClient) -> None:
    store = SignedVirtualKeyStore(derive_virtual_key_secret("svc-test"))
    key = store.mint(MintRequest(tenant_id="t1", session_id="s1", principal_id="alice", allowed_models=("echo",)))
    before = _metric(client, "yal_gateway_spend_usd_total")
    gateway._charge(store, Caller(claims=key), 0.25)  # a session's call
    gateway._charge(store, Caller(), 0.5)  # a service's own call, with no key
    gateway._charge(store, Caller(), 0.0)
    assert _metric(client, "yal_gateway_spend_usd_total") == pytest.approx(before + 0.75)
    assert store.spent_usd(key.token) == pytest.approx(0.25)
