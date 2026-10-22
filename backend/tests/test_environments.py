"""Environments: shared sandbox setups that harnesses name instead of spelling out a sandbox."""

from __future__ import annotations

import pytest

from prime_agent.services.harness_service import HarnessService
from tests.test_harness_publish import FakeSupervisor, _api


async def _harness(db, owner, name: str = "builder"):
    return await HarnessService.acreate(
        {"name": name, "model": "gateway/echo", "mode": "sticky", "user_id": owner.id, "created_by": owner.id}, db
    )


@pytest.fixture
async def owner(make_user):
    return await make_user(email="env-owner@example.com", name="Env Owner")

DOCKER = {"provider": "local_docker", "file_effects": "workspace_write", "approval_default": "ask",
          "start": "with_run", "idle_stop_s": 600}


async def test_an_environment_is_the_sandbox_of_every_harness_that_names_it(db_session, owner, monkeypatch):
    first, second = await _harness(db_session, owner, "alpha"), await _harness(db_session, owner, "beta")
    supervisor = FakeSupervisor()
    async with _api(db_session, owner, supervisor, monkeypatch) as api:
        made = await api.post("/api/v1/environments", json={"name": "Docker, fast start", "description": "py", "sandbox": DOCKER})
        assert made.status_code == 201, made.text
        env = made.json()
        for h in (first, second):
            draft = (await api.get(f"/api/v1/harnesses/{h.id}/manifest")).json()["manifest"]
            saved = await api.put(f"/api/v1/harnesses/{h.id}/manifest", json=draft | {"environment_id": env["id"]})
            assert saved.status_code == 200, saved.text
            assert saved.json()["manifest"]["sandbox"] == DOCKER, "the environment's settings are the sandbox"

        published = await api.post(f"/api/v1/harnesses/{first.id}/publish")
        assert published.status_code == 200, published.text
        listed = (await api.get("/api/v1/environments")).json()

        # Changing the environment changes every harness that uses it, until republished.
        changed = DOCKER | {"idle_stop_s": 0, "start": "on_first_tool"}
        put = await api.put(f"/api/v1/environments/{env['id']}", json={"name": "Docker, fast start", "sandbox": changed})
        view = (await api.get(f"/api/v1/harnesses/{first.id}/manifest")).json()
        refused = await api.delete(f"/api/v1/environments/{env['id']}")

    sandbox_body = next(body for kind, body in supervisor.calls if kind == "sandbox_profile")
    assert (sandbox_body["start"], sandbox_body["idle_stop_s"]) == ("with_run", 600), "copied into the release"
    assert [(u["name"], u["published"]) for u in listed[0]["used_by"]] == [("alpha", True), ("beta", False)]
    assert put.status_code == 200 and put.json()["sandbox"] == changed
    assert view["manifest"]["sandbox"] == changed and view["unpublished_changes"] is True
    assert refused.status_code == 409 and "2 harness" in refused.json()["detail"]


async def test_names_are_unique_per_user_and_a_missing_environment_is_named_in_the_draft(
    db_session, owner, make_user, monkeypatch
):
    harness = await _harness(db_session, owner)
    stranger = await make_user(email="other@example.com", name="Other")
    async with _api(db_session, owner, FakeSupervisor(), monkeypatch) as api:
        env = (await api.post("/api/v1/environments", json={"name": "Host", "sandbox": DOCKER})).json()
        twice = await api.post("/api/v1/environments", json={"name": "Host", "sandbox": DOCKER})
        bad = await api.post("/api/v1/environments", json={"name": "x", "sandbox": DOCKER | {"idle_stop_s": -5}})
        draft = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()["manifest"]
        missing = await api.put(f"/api/v1/harnesses/{harness.id}/manifest", json=draft | {"environment_id": "nope"})
        gone = await api.delete(f"/api/v1/environments/{env['id']}")
    async with _api(db_session, stranger, FakeSupervisor(), monkeypatch) as other:
        hidden = await other.get(f"/api/v1/environments/{env['id']}")
    assert twice.status_code == 409
    assert bad.status_code == 422 and bad.json()["details"][0]["field"] == "body.sandbox.idle_stop_s"
    assert missing.status_code == 422 and missing.json()["details"][0]["field"] == "body.environment_id"
    assert gone.status_code == 204, "unused: deleted"
    assert hidden.status_code == 404


async def test_existing_drafts_and_releases_are_unchanged_by_the_new_optional_fields(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    async with _api(db_session, owner, FakeSupervisor(), monkeypatch) as api:
        await api.post(f"/api/v1/harnesses/{harness.id}/publish")
        view = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()
    assert view["unpublished_changes"] is False
    assert "start" not in view["manifest"]["sandbox"] or view["manifest"]["sandbox"]["start"] is None
