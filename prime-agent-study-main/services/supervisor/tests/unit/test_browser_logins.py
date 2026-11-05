"""Saved browser logins: what a login is, where it may be typed, and that it never comes back."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from supervisor.db.engine import open_db
from supervisor.hands.browser.runner import origin as runtime_origin
from supervisor.seams.core.types import ToolResult
from supervisor.vault import Keyring, Owner, Vault
from supervisor.vault.logins import (
    REDACTED,
    BrowserLogins,
    InvalidLogin,
    login_secret_name,
    normalize_origin,
    parse_login,
)

pytestmark = pytest.mark.asyncio

PASSWORD = "S3cret-pass!"
SHOP = "login:https://shop.example.com"


@pytest.fixture
async def vault(tmp_path: Path) -> AsyncIterator[Vault]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'v.db'}") as (_, factory):
        yield Vault(factory, Keyring({"k1": b"k1".ljust(32, b".")}, active="k1"))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://Shop.Example.com/login?next=/", "https://shop.example.com"),
        ("https://shop.example.com:443/", "https://shop.example.com"),
        ("http://127.0.0.1:8800/admin/", "http://127.0.0.1:8800"),
        ("http://user:pw@shop.example.com/", "http://shop.example.com"),
        ("http://localhost:80", "http://localhost"),
    ],
)
async def test_an_origin_is_the_same_to_the_supervisor_and_to_the_sandbox(url: str, expected: str) -> None:
    """The runtime in the sandbox cannot import the supervisor: its copy must agree."""
    assert normalize_origin(url) == expected == runtime_origin(url)


async def test_a_login_is_named_for_its_origin_and_holds_a_username_and_password() -> None:
    login = parse_login(SHOP, json.dumps({"username": "ada", "password": PASSWORD}))

    assert (login.origin, login.username, login.password) == ("https://shop.example.com", "ada", PASSWORD)
    assert login_secret_name("https://SHOP.example.com/account") == SHOP


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("login:https://shop.example.com/login", '{"username": "a", "password": "b"}'),  # a path is not an origin
        ("login:ftp://shop.example.com", '{"username": "a", "password": "b"}'),
        (SHOP, "ada:secret"),
        (SHOP, '{"username": "ada"}'),
        (SHOP, '{"username": "ada", "password": ""}'),
    ],
)
async def test_what_is_not_a_login_is_refused(name: str, value: str) -> None:
    with pytest.raises(InvalidLogin):
        parse_login(name, value)


async def test_a_tenants_logins_are_its_own_and_malformed_ones_are_left_out(vault: Vault) -> None:
    t1 = Owner.tenant("t1")
    await vault.put(t1, SHOP, json.dumps({"username": "ada", "password": PASSWORD}))
    await vault.put(t1, "login:https://broken.example.com", "not json")
    await vault.put(t1, "mcp:https://tools.example.com", "token-0123456789")
    await vault.put(Owner.tenant("t2"), "login:https://other.example.com", json.dumps({"username": "b", "password": "bbbbbbbb"}))

    t1_logins = BrowserLogins(vault).bind("t1")

    assert await t1_logins.origins() == ["https://shop.example.com"]
    found = await t1_logins.get("https://shop.example.com/checkout")
    assert found is not None and found.password == PASSWORD
    assert await t1_logins.get("https://other.example.com") is None


async def test_every_saved_password_is_cleaned_from_a_result(vault: Vault) -> None:
    await vault.put(Owner.tenant("t1"), SHOP, json.dumps({"username": "ada", "password": PASSWORD}))
    await vault.put(Owner.tenant("t1"), "login:https://pin.example.com", json.dumps({"username": "b", "password": "123"}))
    logins = BrowserLogins(vault)
    result = ToolResult(
        ok=True,
        payload={"output": f"field holds {PASSWORD}", "events": [{"text": f"[{PASSWORD}]"}], "page": "page 123 of 400"},
        error=f"echo {PASSWORD}",
    )

    clean = await logins.redact("t1", result)

    assert PASSWORD not in json.dumps(clean.payload) + str(clean.error)
    assert clean.payload["output"] == f"field holds {REDACTED}"
    assert clean.payload["page"] == "page 123 of 400", "a too-short password (123) is not hunted for"
    assert await logins.redact("t2", result) is result, "another tenant's passwords are not this one's"


async def test_a_changed_password_is_redacted_once_the_cache_forgets(vault: Vault) -> None:
    owner, logins = Owner.tenant("t1"), BrowserLogins(vault)
    await vault.put(owner, SHOP, json.dumps({"username": "ada", "password": PASSWORD}))
    await logins.logins("t1")
    await vault.put(owner, SHOP, json.dumps({"username": "ada", "password": "N3w-pass-2026"}))

    logins.forget("t1")  # what the secrets API does on a change

    clean = await logins.redact("t1", ToolResult(ok=True, payload="N3w-pass-2026"))
    assert clean.payload == REDACTED
