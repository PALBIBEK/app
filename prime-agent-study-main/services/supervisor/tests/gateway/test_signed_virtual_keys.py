"""Session keys outlive a gateway restart: they carry their claims, signed.

They used to live in the gateway's memory: a deploy or a crash cut every session off
(401 unknown virtual key) until the supervisor renewed its key, up to an hour later.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app
from supervisor.gateway.config import Settings
from supervisor.gateway.virtual_keys import (
    MintRequest,
    SignedVirtualKeyStore,
    derive_virtual_key_secret,
    verify_virtual_key,
)

SECRET = derive_virtual_key_secret("svc-test")
SERVICE = {"Authorization": "Bearer svc-test"}


def _mint(store: SignedVirtualKeyStore, session: str = "s1", **kw: object) -> str:
    request = MintRequest(tenant_id="t1", session_id=session, principal_id="alice", allowed_models=("echo",))
    for name, value in kw.items():
        setattr(request, name, value)
    return store.mint(request).token


def test_a_key_carries_its_claims_and_any_store_with_the_secret_takes_it() -> None:
    token = _mint(SignedVirtualKeyStore(SECRET), scope_id="scope-1")
    claims = SignedVirtualKeyStore(SECRET).get(token)  # another process, or after a restart
    assert claims is not None and claims.token == token
    assert (claims.tenant_id, claims.session_id, claims.principal_id) == ("t1", "s1", "alice")
    assert claims.allowed_models == ("echo",) and claims.scope_id == "scope-1"
    assert claims.expires_at - time.time() == pytest.approx(3600, abs=5)
    assert verify_virtual_key(SignedVirtualKeyStore(SECRET), token, route_id="echo") is not None


@pytest.mark.parametrize(
    "forge",
    [
        lambda t: t[:-4] + ("AAAA" if not t.endswith("AAAA") else "BBBB"),  # another signature
        lambda t: "vk_" + t[3:].split(".")[0][:-2] + "xx." + t.split(".")[1],  # other claims
        lambda t: t.split(".")[0],  # no signature
        lambda t: "vk_%%%.%%%",  # not base64
        lambda t: "sk_" + t[3:],  # not a virtual key
    ],
    ids=["signature", "claims", "unsigned", "garbage", "prefix"],
)
def test_a_key_not_signed_with_the_secret_is_unknown(forge: object) -> None:
    token = _mint(SignedVirtualKeyStore(SECRET))
    assert SignedVirtualKeyStore(SECRET).get(forge(token)) is None  # type: ignore[operator]
    assert SignedVirtualKeyStore(derive_virtual_key_secret("other")).get(token) is None


def test_revoking_a_session_revokes_its_keys_until_then() -> None:
    store = SignedVirtualKeyStore(SECRET)
    earlier, other = _mint(store), _mint(store, session="s2")
    assert store.revoke_session("s1") == 1
    assert store.get(earlier).revoked and not store.get(other).revoked  # type: ignore[union-attr]
    later = _mint(store)
    assert not store.get(later).revoked  # type: ignore[union-attr]
    store.revoke(later)
    assert store.get(later).revoked  # type: ignore[union-attr]
    with pytest.raises(PermissionError, match="REVOKED"):
        verify_virtual_key(store, later, route_id="echo")


def test_spend_is_counted_per_key() -> None:
    store = SignedVirtualKeyStore(SECRET)
    one, two = _mint(store), _mint(store)
    assert store.add_spend(one, 0.25) == 0.25 and store.add_spend(one, 0.5) == 0.75
    assert store.spent_usd(two) == 0.0
    with pytest.raises(LookupError):
        store.add_spend("vk_unknown.key", 1.0)


def test_a_short_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        SignedVirtualKeyStore(b"short")


def test_the_secret_is_stable_for_a_service_token_or_set_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", "svc-test")
    monkeypatch.delenv("MODEL_GATEWAY_VIRTUAL_KEY_SECRET", raising=False)
    assert Settings().virtual_key_secret() == Settings().virtual_key_secret() == SECRET
    monkeypatch.setenv("MODEL_GATEWAY_VIRTUAL_KEY_SECRET", "s" * 40)
    assert Settings().virtual_key_secret() == b"s" * 40


@pytest.fixture
def service_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", "svc-test")
    monkeypatch.delenv("MODEL_GATEWAY_VIRTUAL_KEY_SECRET", raising=False)
    yield


def _chat(client: TestClient, token: str) -> int:
    body = {"model": "echo", "messages": [{"role": "user", "content": "hi"}]}
    return client.post("/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {token}"}).status_code


def test_a_key_minted_before_a_gateway_restart_works_after_it(service_token: None) -> None:
    mint = {"tenant_id": "t1", "session_id": "s1", "principal_id": "alice", "allowed_models": ["echo"]}
    with TestClient(app) as before:
        token = before.post("/v1/virtual-keys", json=mint, headers=SERVICE).json()["token"]
        assert _chat(before, token) == 200
    with TestClient(app) as after:  # a deploy: a new process, nothing kept in memory
        assert _chat(after, token) == 200
        assert _chat(after, token[:-4] + "AAAA") == 401
