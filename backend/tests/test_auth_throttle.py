"""Sign-in throttling: guessing a password, or making accounts, gets slower, not free.

There used to be no limit at all: any number of passwords could be tried against an
account, each one an argon2 hash on the API's CPU.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest

from prime_agent.main import create_app
from prime_agent.security import throttle
from prime_agent.web.dependencies import get_db
from tests.conftest import PASSWORD

ORIGIN = {"Origin": "http://localhost:3000"}
WRONG = "not-the-password-1"

pytestmark = pytest.mark.asyncio


@pytest.fixture
def client_from(db_session) -> Callable[[str], AsyncIterator[httpx.AsyncClient]]:
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db

    @asynccontextmanager
    async def connect(address: str) -> AsyncIterator[httpx.AsyncClient]:
        transport = httpx.ASGITransport(app=app, client=(address, 4242))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    return connect


async def _login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN)


async def test_five_wrong_passwords_and_the_account_waits_from_that_address(make_user, client_from) -> None:
    await make_user(email="alice@example.com")
    await make_user(email="bob@example.com")
    async with client_from("203.0.113.7") as client:
        for _ in range(throttle.ACCOUNT_FROM_ADDRESS.attempts):
            assert (await _login(client, "alice@example.com", WRONG)).status_code == 401
        refused = await _login(client, "alice@example.com", PASSWORD)  # even the right one
        assert refused.status_code == 429
        assert 0 < int(refused.headers["Retry-After"]) <= 15 * 60
        assert "try again in 15 minutes" in refused.json()["detail"]
        # Another account from the same address is not held up.
        assert (await _login(client, "bob@example.com", PASSWORD)).status_code == 200
    async with client_from("198.51.100.9") as elsewhere:  # the owner, from home
        assert (await _login(elsewhere, "alice@example.com", PASSWORD)).status_code == 200


async def test_a_successful_sign_in_clears_the_accounts_count(make_user, client_from) -> None:
    await make_user(email="alice@example.com")
    async with client_from("203.0.113.7") as client:
        for _ in range(throttle.ACCOUNT_FROM_ADDRESS.attempts - 1):
            await _login(client, "alice@example.com", WRONG)
        assert (await _login(client, "alice@example.com", PASSWORD)).status_code == 200
        assert (await _login(client, "alice@example.com", WRONG)).status_code == 401  # counting anew


async def test_guesses_spread_over_many_addresses_still_stop(make_user, client_from) -> None:
    await make_user(email="alice@example.com")
    for n in range(throttle.ACCOUNT.attempts):
        async with client_from(f"192.0.2.{n + 1}") as client:
            assert (await _login(client, "alice@example.com", WRONG)).status_code == 401
    async with client_from("192.0.2.200") as client:
        assert (await _login(client, "alice@example.com", WRONG)).status_code == 429


async def test_an_email_with_no_account_is_throttled_the_same(client_from) -> None:
    """The limits never tell which emails have accounts."""
    async with client_from("203.0.113.7") as client:
        answers = [(await _login(client, "nobody@example.com", WRONG)).status_code for _ in range(6)]
    assert answers == [401] * 5 + [429]


async def test_the_wait_ends_with_its_window(make_user, client_from, monkeypatch: pytest.MonkeyPatch) -> None:
    await make_user(email="alice@example.com")
    async with client_from("203.0.113.7") as client:
        for _ in range(throttle.ACCOUNT_FROM_ADDRESS.attempts):
            await _login(client, "alice@example.com", WRONG)
        assert (await _login(client, "alice@example.com", PASSWORD)).status_code == 429
        later = throttle._now() + throttle.ACCOUNT_FROM_ADDRESS.window + timedelta(seconds=1)
        monkeypatch.setattr(throttle, "_now", lambda: later)
        assert (await _login(client, "alice@example.com", PASSWORD)).status_code == 200


async def test_an_address_makes_so_many_accounts_an_hour(client_from) -> None:
    async with client_from("203.0.113.7") as client:
        for n in range(throttle.REGISTRATIONS.attempts):
            made = await client.post(
                "/api/v1/auth/register",
                json={"email": f"new{n}@example.com", "name": "New User", "password": PASSWORD},
                headers=ORIGIN,
            )
            assert made.status_code == 201, made.text
        refused = await client.post(
            "/api/v1/auth/register",
            json={"email": "one-more@example.com", "name": "New User", "password": PASSWORD},
            headers=ORIGIN,
        )
    assert refused.status_code == 429


async def test_a_password_past_the_limit_is_refused_before_it_is_hashed(client_from) -> None:
    async with client_from("203.0.113.7") as client:
        answer = await _login(client, "alice@example.com", "x" * 257)
    assert answer.status_code == 422
