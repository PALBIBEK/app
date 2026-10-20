"""The runner command queue: one claim per command, only its runner completes it, offline is gone."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from supervisor.db.engine import open_db
from supervisor.hands import runners
from supervisor.hands.runners import RunnerHub, RunnerOffline, RunnerRefused


@pytest_asyncio.fixture
async def hub(tmp_path: Path) -> AsyncIterator[RunnerHub]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}") as (_, factory):
        yield RunnerHub(factory)


async def _answer(hub: RunnerHub, *, ok: bool = True, result: dict | None = None) -> str:
    command = await hub.next_command("t1", "office", wait_s=5)
    assert command is not None
    assert await hub.complete("t1", "office", command.id, ok=ok, result=result or {"echo": command.args})
    return command.op


async def test_a_call_waits_for_its_runner_and_gets_the_result(hub: RunnerHub) -> None:
    token = await hub.register("t1", "office")
    assert await hub.verify("t1", "office", token) and not await hub.verify("t2", "office", token)
    await hub.next_command("t1", "office", wait_s=0)  # the runner polls: it is online
    call = asyncio.create_task(hub.call("t1", "office", "exists", {"path": "a"}, sandbox_id="h1"))
    assert await _answer(hub) == "exists"
    assert await call == {"echo": {"path": "a"}}


async def test_a_refusal_and_an_offline_runner_are_raised(hub: RunnerHub, monkeypatch: pytest.MonkeyPatch) -> None:
    await hub.register("t1", "office")
    with pytest.raises(RunnerOffline):  # never polled
        await hub.call("t1", "office", "exists", {})
    await hub.next_command("t1", "office", wait_s=0)
    call = asyncio.create_task(hub.call("t1", "office", "read", {"path": "x"}))
    await _answer(hub, ok=False, result={"kind": "FileNotFoundError", "error": "no x"})
    with pytest.raises(RunnerRefused, match="no x"):
        await call
    monkeypatch.setattr(runners, "LIVE_WITHIN_S", 0.05)
    await asyncio.sleep(0.1)  # the runner stops polling
    with pytest.raises(RunnerOffline):
        await hub.call("t1", "office", "exists", {})


async def test_each_command_is_claimed_once_and_only_its_runner_completes_it(hub: RunnerHub) -> None:
    await hub.register("t1", "office")
    await hub.register("t1", "lab")
    await hub.next_command("t1", "office", wait_s=0)
    call = asyncio.create_task(hub.call("t1", "office", "list", {}))
    claims = await asyncio.gather(*(hub.next_command("t1", "office", wait_s=1) for _ in range(4)))
    claimed = [c for c in claims if c is not None]
    assert len(claimed) == 1
    assert await hub.next_command("t1", "lab", wait_s=0) is None  # not another runner's
    assert not await hub.complete("t1", "lab", claimed[0].id, ok=True, result={})
    assert await hub.complete("t1", "office", claimed[0].id, ok=True, result={"paths": []})
    assert await call == {"paths": []}
