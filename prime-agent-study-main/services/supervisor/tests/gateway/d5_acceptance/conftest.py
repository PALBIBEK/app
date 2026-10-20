"""Fixtures for independent D5 gateway acceptance (authored from D5.md)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app

from .helpers import SERVICE_TOKEN, configure_app


@pytest.fixture
def gateway_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Callable to (re)configure gateway app state mid-test."""

    def _apply(**kwargs: Any):
        return configure_app(monkeypatch, **kwargs)

    return _apply


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Default: token set, default routes including githi without vendor keys."""
    configure_app(monkeypatch)
    with TestClient(app) as c:
        configure_app(monkeypatch)
        yield c


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}
