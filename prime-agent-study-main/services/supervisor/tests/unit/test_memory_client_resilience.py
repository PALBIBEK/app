"""The supervisor's memory client: a failing memory service is skipped fast (a circuit
breaker), and a recall reads each notebook's facts once and its home once per process."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory.client import MemoryUnavailable, NotebookClient, _Breaker
from supervisor.memory.cross import recall_across
from supervisor.memory.disclosure import Audience
from supervisor.memory.platform import MemoryPlatform
from supervisor.memory_service.notebooks.errors import NotebookNotFound
from supervisor.memory_service.notebooks.types import CaptureMetadata, RecallContext, Sensitivity
from tests.support.notebooks import MemoryServiceHandle

pytestmark = pytest.mark.asyncio

TRACKED = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=())


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Down(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls = 0
        self.status: int | None = None  # None: unreachable

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status is None:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(self.status, json=[] if self.status < 400 else {"detail": "x"})


async def test_after_repeated_failures_calls_fail_fast_until_one_trial_succeeds() -> None:
    clock, down = _Clock(), _Down()
    client = NotebookClient(
        "http://mem", transport=down, credential=lambda t: None,
        breaker=_Breaker(threshold=3, cooldown_s=30, clock=clock),
    )
    for _ in range(3):
        with pytest.raises(MemoryUnavailable, match="unreachable"):
            await client.facts("t1~a")
    with pytest.raises(MemoryUnavailable, match="skipped"):
        await client.facts("t1~a")
    assert down.calls == 3, "open: no request was made"
    clock.now = 31  # half-open: one trial, which fails and reopens
    with pytest.raises(MemoryUnavailable, match="unreachable"):
        await client.facts("t1~a")
    with pytest.raises(MemoryUnavailable, match="skipped"):
        await client.facts("t1~a")
    clock.now = 62
    down.status = 200  # back: the trial succeeds and closes the breaker
    assert await client.facts("t1~a") == []
    assert await client.facts("t1~a") == []
    assert down.calls == 6
    await client.aclose()


async def test_5xx_counts_as_failing_but_a_4xx_is_an_answer() -> None:
    clock, down = _Clock(), _Down()
    client = NotebookClient(
        "http://mem", transport=down, credential=lambda t: None,
        breaker=_Breaker(threshold=2, cooldown_s=30, clock=clock),
    )
    down.status = 503
    with pytest.raises(MemoryUnavailable):
        await client.facts("t1~a")
    down.status = 404  # the service is up: the failure count starts again
    with pytest.raises(NotebookNotFound):
        await client.facts("t1~a")
    down.status = 503
    with pytest.raises(MemoryUnavailable, match="503"):
        await client.facts("t1~a")
    with pytest.raises(MemoryUnavailable, match="503"):
        await client.facts("t1~a")
    with pytest.raises(MemoryUnavailable, match="skipped"):
        await client.facts("t1~a")
    await client.aclose()


async def test_a_recall_reads_each_notebook_once_and_its_home_once_per_process(
    memory_svc: MemoryServiceHandle, supervisor_db: async_sessionmaker[AsyncSession]
) -> None:
    calls: list[str] = []
    inner = httpx.ASGITransport(app=memory_svc.app)

    class Count(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            return await inner.handle_async_request(request)

    client = NotebookClient("http://memory.test", transport=Count())
    work = await client.ensure(tenant_id="t", owner="alice", agent_pin="p", name="work")
    home = await client.ensure(tenant_id="t", owner="alice", agent_pin="p", name="home")
    await client.capture(work.id, ["Owns billing"], context=TRACKED)
    await client.capture(home.id, ["Prefers tea"], context=TRACKED)

    async def one_shot(system: str, content: str) -> str:
        return "ordinary"

    platform = MemoryPlatform(supervisor_db, notebooks=client, one_shot=one_shot)
    calls.clear()
    view = platform.view(Audience.private("alice"))
    first = await recall_across(view, [work.id, home.id], RecallContext(query="x"))
    said = " ".join(str(r) for r in first)
    assert "Owns billing" in said and "Prefers tea" in said
    assert sorted(calls) == sorted(
        [f"GET /v1/notebooks/{n}/facts" for n in (work.id, home.id)]
        + [f"GET /v1/notebooks/{n}" for n in (work.id, home.id)]
    )
    calls.clear()
    await recall_across(platform.view(Audience.private("alice")), [work.id, home.id], RecallContext())
    assert sorted(calls) == sorted(f"GET /v1/notebooks/{n}/facts" for n in (work.id, home.id))
    await client.aclose()
