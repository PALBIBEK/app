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
    Checkout,
    InvalidLogin,
    Login,
    login_secret_name,
    normalize_origin,
    parse_item,
    parse_login,
)
from supervisor.vault.totp import totp_now

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


async def test_a_results_images_are_not_scrubbed_as_text(vault: Vault) -> None:
    """Base64 holds any short run of letters: replacing one would break the image."""
    await vault.put(Owner.tenant("t1"), SHOP, json.dumps({"username": "ada", "password": "AAAA"}))
    shot = {"mime_type": "image/jpeg", "data": "/9j/AAAA" * 10}
    result = ToolResult(ok=True, payload={"output": "typed AAAA", "images": [shot]})

    clean = await BrowserLogins(vault).redact("t1", result)

    assert clean.payload == {"output": f"typed {REDACTED}", "images": [shot]}


# Hermes's vault kinds: a login's authenticator key, cards, addresses ------------------------

CARD = {"origins": ["https://shop.example.com/checkout"], "card_number": "4242 4242 4242 4242", "exp_month": "4", "exp_year": "2031", "cvc": "731", "pin": "9999"}
ADDRESS = {"origins": ["https://shop.example.com"], "address_line1": "12 Analytical St", "city": "London", "postal_code": "N1 9GU", "country": "United Kingdom"}


async def test_cards_and_addresses_keep_only_their_fields_and_need_their_sites() -> None:
    card = parse_item("card:Visa-4242", json.dumps(CARD))
    address = parse_item("address:Home", json.dumps(ADDRESS))

    assert isinstance(card, Checkout) and card.kind == "card" and card.origins == ("https://shop.example.com",)
    assert "pin" not in card.fields, "only a card's own fields are kept"
    assert card.described() == "Visa-4242 (card ending 4242)" and card.secrets == ["4242 4242 4242 4242", "731"]
    assert isinstance(address, Checkout) and address.secrets == [], "an address is not a secret"
    for name, value, why in [
        ("card:Visa", json.dumps({**CARD, "origins": []}), "the sites it may be filled on"),
        ("card:Visa", json.dumps({k: v for k, v in CARD.items() if k != "cvc"}), "needs cvc"),
        ("card:My Visa", json.dumps(CARD), "a label of letters"),
        ("address:Home", json.dumps({**ADDRESS, "origins": ["shop.example.com"]}), r"not an http\(s\) origin"),
        ("wallet:x", "{}", "not a vault item"),
    ]:
        with pytest.raises(InvalidLogin, match=why):
            parse_item(name, value)


async def test_a_login_keeps_its_authenticator_key_normalized_and_makes_its_codes() -> None:
    login = parse_item(SHOP, json.dumps({"username": "ada", "password": PASSWORD, "otp": "otpauth://totp/Shop:ada?secret=jbsw y3dp ehpk3pxp"}))

    assert isinstance(login, Login) and login.otp == "JBSWY3DPEHPK3PXP"
    assert login.code(at=0) == totp_now("JBSWY3DPEHPK3PXP", at=0)
    with pytest.raises(InvalidLogin, match="base32"):
        parse_item(SHOP, json.dumps({"username": "ada", "password": PASSWORD, "otp": "not base32!"}))


async def test_the_listing_shows_handles_and_never_a_secret(vault: Vault) -> None:
    owner = Owner.tenant("t1")
    await vault.put(owner, SHOP, json.dumps({"username": "ada", "password": PASSWORD, "otp": "JBSWY3DPEHPK3PXP"}))
    await vault.put(owner, "card:Visa-4242", json.dumps(CARD))
    await vault.put(owner, "address:Home", json.dumps(ADDRESS))
    await vault.put(owner, "card:broken", "{}")
    tenant = BrowserLogins(vault).bind("t1")

    listing = await tenant.listing()

    assert listing == [
        {"handle": SHOP, "kind": "login", "origin": "https://shop.example.com", "identifier": "ada", "two_factor": "automatic"},
        {"handle": "address:Home", "kind": "address", "label": "Home (London)", "origins": ["https://shop.example.com"]},
        {"handle": "card:Visa-4242", "kind": "card", "label": "Visa-4242 (card ending 4242)", "origins": ["https://shop.example.com"]},
    ]
    shown = json.dumps(listing)
    assert PASSWORD not in shown and "JBSWY3DPEHPK3PXP" not in shown and "4242 4242" not in shown and "731" not in shown
    assert isinstance(await tenant.item("card:Visa-4242"), Checkout)
    assert isinstance(await tenant.item("https://shop.example.com/login"), Login), "a bare origin names its login"
    assert await tenant.item("card:none") is None


async def test_card_numbers_codes_keys_and_typed_codes_are_cleaned_from_results(vault: Vault) -> None:
    owner = Owner.tenant("t1")
    await vault.put(owner, SHOP, json.dumps({"username": "ada", "password": PASSWORD, "otp": "JBSWY3DPEHPK3PXP"}))
    await vault.put(owner, "card:Visa-4242", json.dumps(CARD))
    logins = BrowserLogins(vault)
    logins.typed("t1", "246810")

    clean = await logins.redact("t1", ToolResult(ok=True, payload="4242 4242 4242 4242 / 731 / JBSWY3DPEHPK3PXP / 246810 / London"))

    # A 3-digit security code is not hunted for, as no value under MIN_REDACTED is: every "731"
    # in a page (a price, a page number) would go with it.
    assert clean.payload == f"{REDACTED} / 731 / {REDACTED} / {REDACTED} / London"
