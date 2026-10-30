"""Signing out, or changing the password, ends the access tokens of what it revokes.

Access tokens used to live on for their whole half hour: a stolen one kept working after its
owner signed out, or changed the password because of the theft.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest

from prime_agent.main import create_app
from prime_agent.web.dependencies import get_db
from tests.conftest import PASSWORD

pytestmark = pytest.mark.asyncio
ORIGIN = "http://localhost:3000"
NEW_PASSWORD = "AnotherPass456!"


class Device:
    """One browser: its own refresh cookie, CSRF token and access token."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.access = ""
        self.csrf = ""

    async def sign_in(self, password: str = PASSWORD) -> None:
        answer = await self.client.post(
            "/api/v1/auth/login", json={"email": "alice@example.com", "password": password}, headers={"Origin": ORIGIN}
        )
        assert answer.status_code == 200, answer.text
        self.access, self.csrf = answer.json()["access_token"], answer.json()["csrf_token"]

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}", "Origin": ORIGIN, "X-CSRF-Token": self.csrf}

    async def me(self, access: str | None = None) -> int:
        token = access or self.access
        return (await self.client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})).status_code


@pytest.fixture
def device(db_session, make_user) -> Callable[[], AsyncIterator[Device]]:  # noqa: ANN001
    app = create_app()

    async def override_db():  # noqa: ANN202
        yield db_session

    app.dependency_overrides[get_db] = override_db

    @asynccontextmanager
    async def open_device() -> AsyncIterator[Device]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Device(client)

    return open_device


@pytest.fixture(autouse=True)
async def alice(make_user):  # noqa: ANN001, ANN201
    return await make_user(email="alice@example.com")


async def test_signing_out_ends_that_sign_ins_access_token_and_no_other(device) -> None:  # noqa: ANN001
    async with device() as laptop, device() as phone:
        await laptop.sign_in()
        await phone.sign_in()
        stolen = laptop.access
        assert (await laptop.client.post("/api/v1/auth/logout", headers=laptop.headers())).status_code == 204
        assert await laptop.me(stolen) == 401
        assert await phone.me() == 200  # the phone stays signed in


async def test_changing_the_password_ends_every_access_token(device) -> None:  # noqa: ANN001
    async with device() as laptop, device() as phone:
        await laptop.sign_in()
        await phone.sign_in()
        changed = await laptop.client.post(
            "/api/v1/users/me/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers=laptop.headers(),
        )
        assert changed.status_code == 204
        assert await laptop.me() == 401
        assert await phone.me() == 401
        await phone.sign_in(NEW_PASSWORD)  # signing in again works
        assert await phone.me() == 200


async def test_a_refreshed_sign_in_keeps_working(device) -> None:  # noqa: ANN001
    async with device() as laptop:
        await laptop.sign_in()
        before = laptop.access
        refreshed = await laptop.client.post("/api/v1/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": laptop.csrf})
        assert refreshed.status_code == 200
        assert await laptop.me(refreshed.json()["access_token"]) == 200
        assert await laptop.me(before) == 200  # rotation is not revocation
