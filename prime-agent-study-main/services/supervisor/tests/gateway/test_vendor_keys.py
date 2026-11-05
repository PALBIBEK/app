"""Vendor keys set through the gateway: sealed in its file, checked first, never returned."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.gateway import app as gateway_app
from supervisor.gateway.key_check import KeyCheck, check_key
from supervisor.gateway.vendor_keys import VendorKeys, sealing_key
from supervisor.gateway.vendors import VENDORS

TOKEN = "svc-token-for-vendor-key-tests"
SVC = {"Authorization": f"Bearer {TOKEN}"}
_VENDOR_ENV = [name for v in VENDORS.values() for name in (v.key_env, v.base_url_env)]
SECRET = sealing_key("x" * 32)


def _store(path: Path, **environment: str) -> VendorKeys:
    return VendorKeys(environment, path=path, secret=SECRET)


# -- the store ---------------------------------------------------------------------------


def test_a_set_key_is_sealed_in_the_file_and_wins_over_the_environment(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    keys = _store(path, deepseek="sk-env")
    status = keys.set(VENDORS["deepseek"], " sk-set ", by="admin-1")
    assert keys.key("deepseek") == "sk-set"
    assert (status.configured, status.source, status.updated_by) == (True, "set", "admin-1")
    assert b"sk-set" not in path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600


def test_without_an_entry_the_environment_key_is_used(tmp_path: Path) -> None:
    keys = _store(tmp_path / "keys.json", openai="sk-env")
    assert keys.key("openai") == "sk-env"
    assert keys.status("openai").source == "environment"
    assert keys.status("zai").source == "absent"
    assert keys.keyed() == {"openai"}


def test_removing_a_key_turns_the_vendor_off_even_with_one_in_the_environment(
    tmp_path: Path,
) -> None:
    keys = _store(tmp_path / "keys.json", openai="sk-env")
    status = keys.remove(VENDORS["openai"], by="admin-1")
    assert keys.key("openai") == ""
    assert (status.configured, status.source) == (False, "removed")
    assert "openai" not in keys.keyed()


def test_another_process_sees_a_key_set_in_the_shared_file(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    reader, writer = _store(path), _store(path)
    assert reader.key("zai") == ""
    writer.set(VENDORS["zai"], "sk-z", by="admin-1")
    assert reader.key("zai") == "sk-z"
    writer.remove(VENDORS["zai"], by="admin-1")
    assert reader.key("zai") == ""


def test_a_sealed_key_moved_to_another_vendor_does_not_open(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    keys = _store(path)
    keys.set(VENDORS["openai"], "sk-o", by="a")
    document = json.loads(path.read_bytes())
    document["vendors"]["anthropic"] = document["vendors"]["openai"]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="anthropic does not open"):
        _store(path).key("anthropic")


def test_another_sealing_key_does_not_open_the_file(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    _store(path).set(VENDORS["openai"], "sk-o", by="a")
    with pytest.raises(ValueError, match="does not open"):
        VendorKeys({}, path=path, secret=sealing_key("y" * 32)).key("openai")


def test_without_a_file_keys_are_the_environments_and_cannot_be_set() -> None:
    keys = VendorKeys({"openai": "sk-env"})
    assert not keys.editable
    assert keys.key("openai") == "sk-env"
    with pytest.raises(PermissionError, match="MODEL_GATEWAY_KEYS_FILE"):
        keys.set(VENDORS["openai"], "sk", by="a")


# -- checking a key ----------------------------------------------------------------------


def _answering(status: int, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={})

    return httpx.MockTransport(handle)


@pytest.mark.parametrize(
    ("status", "result"),
    [
        (200, KeyCheck.ACCEPTED),
        (401, KeyCheck.REFUSED),
        (403, KeyCheck.REFUSED),
        (503, KeyCheck.UNREACHABLE),
    ],
)
def test_a_key_is_checked_with_one_authenticated_get(status: int, result: KeyCheck) -> None:
    seen: list[httpx.Request] = []
    got = check_key(
        VENDORS["deepseek"], "sk-d", "https://api.deepseek.com", transport=_answering(status, seen)
    )
    assert got is result
    assert (seen[0].method, str(seen[0].url)) == ("GET", "https://api.deepseek.com/models")
    assert seen[0].headers["authorization"] == "Bearer sk-d"


def test_anthropic_and_openrouter_keys_are_checked_their_way() -> None:
    seen: list[httpx.Request] = []
    check_key(
        VENDORS["anthropic"], "sk-a", "https://api.anthropic.com", transport=_answering(200, seen)
    )
    check_key(
        VENDORS["openrouter"],
        "sk-r",
        "https://openrouter.ai/api/v1",
        transport=_answering(200, seen),
    )
    assert str(seen[0].url) == "https://api.anthropic.com/v1/models"
    assert seen[0].headers["x-api-key"] == "sk-a" and "authorization" not in seen[0].headers
    assert str(seen[1].url) == "https://openrouter.ai/api/v1/key"


def test_an_unreachable_vendor_says_nothing_about_the_key() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    got = check_key(
        VENDORS["openai"], "sk", "https://api.openai.com/v1", transport=httpx.MockTransport(fail)
    )
    assert got is KeyCheck.UNREACHABLE


def test_a_vendor_without_a_documented_check_is_unchecked() -> None:
    assert check_key(VENDORS["zai"], "sk", "https://api.z.ai/api/paas/v4") is KeyCheck.UNCHECKED


# -- the API -----------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    for name in (*_VENDOR_ENV, "AUTH_REQUIRED", "ENV", "MODEL_GATEWAY_KEYS_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", TOKEN)
    monkeypatch.setenv("MODEL_GATEWAY_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv(
        "MODEL_GATEWAY_ROUTES_JSON",
        json.dumps({"echo": {"provider": "echo", "upstream_model": "echo"}}),
    )
    monkeypatch.setattr(gateway_app, "check_key", lambda vendor, key, base_url: KeyCheck.ACCEPTED)
    with TestClient(gateway_app.app) as c:
        yield c


def _vendor(client: TestClient, vendor: str) -> dict[str, object]:
    listing = client.get("/v1/vendors", headers=SVC).json()
    return next(v for v in listing["vendors"] if v["id"] == vendor)


def test_the_vendor_list_shows_status_never_a_key(client: TestClient) -> None:
    listing = client.get("/v1/vendors", headers=SVC).json()
    assert listing["editable"] is True
    assert [v["id"] for v in listing["vendors"]] == list(VENDORS)
    assert _vendor(client, "deepseek") == {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "key_required": True,
        "key_checked": True,
        "configured": False,
        "source": "absent",
        "updated_at": None,
        "updated_by": None,
        "ready": False,
    }


def test_setting_a_key_makes_the_vendor_and_its_default_route_ready(client: TestClient) -> None:
    put = client.put(
        "/v1/vendors/deepseek/key", headers=SVC, json={"key": "sk-d", "updated_by": "u1"}
    )
    assert put.status_code == 200
    body = put.json()
    assert (body["configured"], body["source"], body["ready"], body["checked"]) == (
        True,
        "set",
        True,
        True,
    )
    assert "sk-d" not in put.text
    models = [m["id"] for m in client.get("/v1/models", headers=SVC).json()["data"]]
    assert models == ["echo", "deepseek-flash"]
    assert "sk-d" not in client.get("/v1/vendors", headers=SVC).text

    removed = client.delete("/v1/vendors/deepseek/key", headers=SVC, params={"updated_by": "u1"})
    assert (removed.json()["source"], removed.json()["ready"]) == ("removed", False)
    assert [m["id"] for m in client.get("/v1/models", headers=SVC).json()["data"]] == ["echo"]


def test_a_refused_key_is_not_saved(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_app, "check_key", lambda vendor, key, base_url: KeyCheck.REFUSED)
    put = client.put("/v1/vendors/openai/key", headers=SVC, json={"key": "bad", "updated_by": "u1"})
    assert put.status_code == 400
    assert put.json()["detail"]["error"]["code"] == "KEY_REFUSED"
    assert _vendor(client, "openai")["configured"] is False


def test_a_key_that_could_not_be_checked_is_not_saved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gateway_app, "check_key", lambda vendor, key, base_url: KeyCheck.UNREACHABLE
    )
    put = client.put("/v1/vendors/openai/key", headers=SVC, json={"key": "sk", "updated_by": "u1"})
    assert put.status_code == 502
    assert _vendor(client, "openai")["configured"] is False


def test_a_vendor_with_no_documented_check_saves_its_key_unchecked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway_app, "check_key", check_key)
    put = client.put("/v1/vendors/zai/key", headers=SVC, json={"key": "sk-z", "updated_by": "u1"})
    assert (put.status_code, put.json()["checked"], put.json()["ready"]) == (200, False, True)


def test_only_the_service_token_reaches_vendor_keys(client: TestClient) -> None:
    assert client.get("/v1/vendors").status_code == 401
    assert (
        client.put("/v1/vendors/openai/key", json={"key": "k", "updated_by": "u"}).status_code
        == 401
    )
    assert client.delete("/v1/vendors/openai/key", params={"updated_by": "u"}).status_code == 401


def test_an_unknown_vendor_is_404(client: TestClient) -> None:
    put = client.put("/v1/vendors/nope/key", headers=SVC, json={"key": "k", "updated_by": "u"})
    assert put.status_code == 404


def test_without_a_key_file_keys_cannot_be_set(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_KEYS_FILE")
    with TestClient(gateway_app.app) as c:
        assert c.get("/v1/vendors", headers=SVC).json()["editable"] is False
        put = c.put("/v1/vendors/openai/key", headers=SVC, json={"key": "k", "updated_by": "u"})
    assert (put.status_code, put.json()["detail"]["error"]["code"]) == (409, "KEYS_NOT_EDITABLE")
