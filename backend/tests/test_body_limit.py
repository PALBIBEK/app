"""Request bodies over the limit are refused before they are read into memory.

There was no limit: a 64 MB JSON body was read and parsed whole before validation refused it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from prime_agent.config import get_settings
from prime_agent.main import create_app
from prime_agent.web.body_limit import MAX_REQUEST_BYTES, MULTIPART_OVERHEAD_BYTES
from prime_agent.web.dependencies import get_db

pytestmark = pytest.mark.asyncio
ORIGIN = "http://localhost:3000"


@pytest.fixture
async def api(db_session) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield client


def _login(size: int) -> bytes:
    return b'{"email":"a@b.co","password":"' + b"x" * size + b'"}'


async def test_a_declared_body_over_the_limit_is_refused_at_once(api: httpx.AsyncClient) -> None:
    answer = await api.post(
        "/api/v1/auth/login",
        content=_login(MAX_REQUEST_BYTES + 1),
        headers={"Content-Type": "application/json", "Origin": ORIGIN},
    )
    assert answer.status_code == 413
    assert answer.json()["detail"]["error_code"] == "REQUEST_TOO_LARGE"
    assert answer.headers["access-control-allow-origin"] == ORIGIN  # the browser can read why


async def test_a_chunked_body_is_refused_once_it_passes_the_limit(api: httpx.AsyncClient) -> None:
    sent = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal sent
        for _ in range(64):  # 64 MiB if it were all read
            sent += 1
            yield b"x" * (1024 * 1024)

    answer = await api.post("/api/v1/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert answer.status_code == 413
    assert sent <= 4, "reading stopped near the limit"


async def test_a_body_within_the_limit_reaches_its_route(api: httpx.AsyncClient) -> None:
    answer = await api.post(
        "/api/v1/auth/login",
        content=_login(1000),
        headers={"Content-Type": "application/json", "Origin": ORIGIN},
    )
    assert answer.status_code == 422  # the route's own validation (a password over 256 characters)


async def test_the_proxied_workspace_upload_may_be_as_large_as_a_file_may(api: httpx.AsyncClient) -> None:
    limit = get_settings().workspace_max_upload_bytes + MULTIPART_OVERHEAD_BYTES
    path = "/api/v1/workspace/scopes/s1/files/big.bin"
    within = await api.put(path, headers={"Content-Length": str(limit)}, content=b"")
    assert within.status_code == 401  # past the size check: sign-in is next
    over = await api.put(path, headers={"Content-Length": str(limit + 1)}, content=b"")
    assert over.status_code == 413
    # Only that route: the same size elsewhere is refused.
    elsewhere = await api.post("/api/v1/sessions", headers={"Content-Length": str(limit)}, content=b"")
    assert elsewhere.status_code == 413
