"""A harness whose sandbox cannot be built here is refused at once, with a reason to show."""

from __future__ import annotations

import docker.errors
import pytest

from supervisor.api.workflow_errors import to_http
from supervisor.sandbox.base import SandboxUnavailable
from supervisor.sandbox.providers.local_docker import LocalDockerProvider
from supervisor.temporal.domain import domain_kind
from supervisor.temporal.sessions import SessionCommandError

IMAGE = "ghcr.io/example/sandbox@sha256:" + "a" * 64


@pytest.mark.asyncio
async def test_docker_not_running_is_sandbox_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/docker.sock")  # no daemon, anywhere
    with pytest.raises(SandboxUnavailable) as caught:
        await LocalDockerProvider().preflight(image=IMAGE)
    assert "Docker is not reachable" in str(caught.value) and "Host process" in str(caught.value)
    assert caught.value.provider == "local_docker"


class _Images:
    def get(self, image: str) -> None:
        raise docker.errors.ImageNotFound("no such image")

    def get_registry_data(self, image: str) -> None:
        raise docker.errors.NotFound("manifest unknown")


class _Client:
    images = _Images()

    def ping(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_missing_image_is_sandbox_unavailable() -> None:
    with pytest.raises(SandboxUnavailable, match="is not on this machine"):
        await LocalDockerProvider(client=_Client()).preflight(image=IMAGE)


def test_it_is_final_and_shown_to_the_caller() -> None:
    err = SandboxUnavailable("Docker is not reachable ...", provider="local_docker")
    assert domain_kind(err) == "SandboxUnavailable"  # not retried by the worker
    http = to_http(SessionCommandError("SandboxUnavailable", str(err)))
    assert http.status_code == 422 and http.detail == "Docker is not reachable ..."
