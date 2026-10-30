"""The supervisor API says, for alerting, what it answered and which dependencies answer it.

There were alerts on the worker fleet only: nothing watched the API's error rate, the
database, Temporal, the model gateway or the memory service.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from supervisor.obs import dependencies
from supervisor.obs.responses import CountResponses, ResponseCounts
from tests.support.session_env import TrackingProvider, make_catalog

pytestmark = pytest.mark.asyncio


def _value(text: str, series: str) -> float | None:
    found = re.search(rf"^{re.escape(series)} (\S+)$", text, re.MULTILINE)
    return None if found is None else float(found.group(1))


async def test_responses_are_counted_by_code_failures_included() -> None:
    counts = ResponseCounts()
    app = FastAPI()
    app.add_middleware(CountResponses, counts=counts)

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {}

    @app.get("/refused")
    def refused() -> None:
        raise HTTPException(status_code=403)

    @app.get("/broken")
    def broken() -> None:
        raise RuntimeError("bug")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        for path in ("/ok", "/ok", "/refused", "/broken", "/missing"):
            await client.get(path)
    assert counts.snapshot() == {200: 2, 403: 1, 404: 1, 500: 1}


async def test_each_dependency_in_use_is_probed_and_a_failing_one_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependencies, "PROBE_TIMEOUT_S", 0.2)
    monkeypatch.setenv("MODEL_GATEWAY_URL", "http://model-gateway:8090/v1")
    monkeypatch.setenv("MEMORY_SERVICE_URL", "http://memory:8092")
    asked: list[str] = []

    def http(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.host}{request.url.path}")
        return httpx.Response(200 if request.url.host == "model-gateway" else 503)

    async def database() -> bool:
        return True

    async def temporal() -> bool:
        raise ConnectionError("refused")

    async def sandbox() -> bool:
        await asyncio.sleep(5)  # longer than a probe may take
        return True

    state = SimpleNamespace(check_db=database, workflows=SimpleNamespace(check_reachable=temporal), check_sandbox=sandbox)
    up = await dependencies.dependencies_up(state, transport=httpx.MockTransport(http))
    assert up == {"database": True, "temporal": False, "sandbox": False, "model_gateway": True, "memory": False}
    assert sorted(asked) == ["memory/v1/readyz", "model-gateway/v1/readyz"]


async def test_only_what_the_deployment_uses_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_URL", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_URL", raising=False)
    assert await dependencies.dependencies_up(SimpleNamespace(check_db=None, workflows=None, check_sandbox=None)) == {}


@pytest.fixture
async def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.delenv("MODEL_GATEWAY_URL", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_URL", raising=False)
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory, catalog=make_catalog(), blob_dir=tmp_path / "blobs", sandbox_provider=TrackingProvider()
        )
        async with AsyncClient(transport=ASGITransport(app=create_app(state=state)), base_url="http://t") as client:
            yield client


async def test_the_api_reports_its_responses_and_its_dependencies(api: AsyncClient) -> None:
    await api.get("/healthz")
    await api.get("/v1/sessions/nope", headers={"X-Tenant-Id": "t1"})
    text = (await api.get("/v1/ops/metrics")).text
    assert "# TYPE yal_api_responses_total counter" in text
    assert _value(text, 'yal_api_responses_total{code="200"}') == 1
    assert _value(text, 'yal_api_responses_total{code="404"}') == 1
    assert "# TYPE yal_dependency_up gauge" in text
    assert _value(text, 'yal_dependency_up{dependency="database"}') == 1
    assert "tenant" not in text and "t1" not in text  # counts only
