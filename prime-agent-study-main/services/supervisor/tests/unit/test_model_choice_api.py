"""Choosing a session's model over the API: an offered model is kept and shown, any other is a
422 before anything starts, and a steering message cannot change a run's model."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from supervisor.models import reset_model_router, use_model_router
from tests.support.session_env import make_catalog

HEADERS = {"X-Tenant-Id": "t1"}
ECHO = {"harness_version": "kg-v1", "runtime": "echo"}


@pytest.fixture
def offered() -> Iterator[None]:
    use_model_router(
        SimpleNamespace(  # type: ignore[arg-type]
            selectable_models=lambda: [SimpleNamespace(id="gateway/deepseek-flash")],
            target_for=lambda m: m.removeprefix("gateway/"),
        )
    )
    yield
    reset_model_router()


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory, catalog=make_catalog(), blob_dir=tmp_path / "b"
        )
        async with AsyncClient(
            transport=ASGITransport(app=create_app(state=state)), base_url="http://t"
        ) as c:
            yield c


async def test_a_session_created_with_an_offered_model_shows_it(
    client: AsyncClient, offered: None
) -> None:
    created = await client.post(
        "/v1/sessions", json=ECHO | {"model": "gateway/deepseek-flash"}, headers=HEADERS
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    assert (await client.get(f"/v1/sessions/{sid}", headers=HEADERS)).json()[
        "model"
    ] == "gateway/deepseek-flash"


async def test_without_a_choice_the_session_shows_its_pinned_model(
    client: AsyncClient, offered: None
) -> None:
    sid = (await client.post("/v1/sessions", json=ECHO, headers=HEADERS)).json()["session_id"]
    detail = (await client.get(f"/v1/sessions/{sid}", headers=HEADERS)).json()
    assert detail["model"] != "gateway/deepseek-flash"


async def test_a_message_chooses_the_model_for_the_next_run(
    client: AsyncClient, offered: None
) -> None:
    sid = (await client.post("/v1/sessions", json=ECHO, headers=HEADERS)).json()["session_id"]
    sent = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi", "command_id": "c1", "model": "gateway/deepseek-flash"},
        headers=HEADERS,
    )
    assert sent.status_code == 202, sent.text
    assert (await client.get(f"/v1/sessions/{sid}", headers=HEADERS)).json()[
        "model"
    ] == "gateway/deepseek-flash"


async def test_a_model_not_offered_is_422_and_nothing_starts(
    client: AsyncClient, offered: None
) -> None:
    created = await client.post(
        "/v1/sessions", json=ECHO | {"model": "gateway/nope"}, headers=HEADERS
    )
    assert created.status_code == 422
    assert created.json()["detail"]["reason"] == "model_not_offered"
    assert (await client.get("/v1/sessions", headers=HEADERS)).json() == []

    sid = (await client.post("/v1/sessions", json=ECHO, headers=HEADERS)).json()["session_id"]
    sent = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi", "command_id": "c1", "model": "gateway/nope"},
        headers=HEADERS,
    )
    assert (sent.status_code, sent.json()["detail"]["reason"]) == (422, "model_not_offered")


async def test_without_a_gateway_no_model_can_be_chosen(client: AsyncClient) -> None:
    use_model_router(None)
    try:
        created = await client.post(
            "/v1/sessions", json=ECHO | {"model": "gateway/deepseek-flash"}, headers=HEADERS
        )
    finally:
        reset_model_router()
    assert created.status_code == 422
    assert "no model gateway" in created.json()["detail"]["message"]


async def test_a_steering_message_cannot_change_the_model(
    client: AsyncClient, offered: None
) -> None:
    sent = await client.post(
        "/v1/sessions/s1/messages",
        json={"text": "hi", "command_id": "c1", "steer": True, "model": "gateway/deepseek-flash"},
        headers=HEADERS,
    )
    assert sent.status_code == 422
    assert "steering" in sent.text
