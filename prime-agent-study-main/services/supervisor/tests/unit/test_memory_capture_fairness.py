"""One tenant's burst of captures does not hold up another's (``supervisor.memory.fairness``,
G2's scale rule in ``docs/memory-gaps.md``)."""

from __future__ import annotations

import asyncio

import pytest

from supervisor.memory import fairness
from supervisor.memory.fairness import TenantGate

pytestmark = pytest.mark.asyncio


async def test_a_burst_from_one_tenant_runs_at_its_cap_while_another_tenant_goes_straight_through() -> None:
    gate = TenantGate(3)
    release = asyncio.Event()
    peak = running = 0

    async def burst_call() -> None:
        nonlocal peak, running
        async with gate.slot("tenant-a"):
            running += 1
            peak = max(peak, running)
            await release.wait()
            running -= 1

    burst = [asyncio.create_task(burst_call()) for _ in range(40)]
    await asyncio.sleep(0.01)
    assert (gate.held("tenant-a"), gate.waiting("tenant-a")) == (3, 37)

    async def other() -> str:
        async with gate.slot("tenant-b"):
            return "done"

    assert await asyncio.wait_for(other(), 1) == "done", "tenant b never waits on tenant a's burst"

    release.set()
    await asyncio.gather(*burst)
    assert peak == 3, "never more than the cap at once"
    assert (gate.held("tenant-a"), gate.waiting("tenant-a")) == (0, 0)
    assert not gate._lanes.get(asyncio.get_running_loop()), "nothing kept for idle tenants"


async def test_a_cancelled_waiter_gives_up_its_place() -> None:
    gate = TenantGate(1)
    hold = asyncio.Event()

    async def holder() -> None:
        async with gate.slot("t"):
            await hold.wait()

    first = asyncio.create_task(holder())
    await asyncio.sleep(0)
    waiter = asyncio.create_task(holder())
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)

    assert gate.waiting("t") == 0
    hold.set()
    await first
    assert gate.held("t") == 0


def test_the_cap_is_a_setting_and_a_bad_one_names_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fairness, "_gate", None)
    monkeypatch.setenv(fairness.PER_TENANT_ENV, "5")
    assert fairness.capture_gate().limit == 5
    monkeypatch.setattr(fairness, "_gate", None)
    for bad in ("0", "lots"):
        monkeypatch.setenv(fairness.PER_TENANT_ENV, bad)
        with pytest.raises(ValueError, match="MEMORY_CAPTURE_PER_TENANT"):
            fairness.capture_gate()


async def test_a_gated_one_shot_holds_a_slot_only_while_its_call_runs() -> None:
    gate = TenantGate(1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def model(system: str, content: str) -> str:
        entered.set()
        await release.wait()
        return "ok"

    model.model = "m-1"  # type: ignore[attr-defined]
    gated = fairness.GatedOneShot(model, "t", gate)
    call = asyncio.create_task(gated("s", "c"))
    await entered.wait()
    assert gate.held("t") == 1
    release.set()
    assert await call == "ok"
    assert gate.held("t") == 0 and gated.model == "m-1", "nothing held between calls; its model kept"


async def test_a_cancelled_call_holds_its_slot_until_the_call_itself_ends() -> None:
    """A gateway call runs on a thread no cancel stops: its slot is held to its end, so a
    tenant's cancelled captures never let it hold more calls than its slots."""
    gate = TenantGate(1)
    entered = asyncio.Event()
    finish = asyncio.Event()
    running = [0]

    async def model(system: str, content: str) -> str:
        running[0] += 1
        entered.set()
        try:
            await asyncio.shield(finish.wait())  # as a thread: the cancel does not reach it
        finally:
            running[0] -= 1
        return "late"

    gated = fairness.GatedOneShot(model, "t", gate)
    first = asyncio.create_task(gated("s", "c"))
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(gated("s", "c"))
    await asyncio.sleep(0.05)
    assert gate.held("t") == 1 and running[0] == 1, "the cancelled call still holds the only slot"
    finish.set()
    assert await second == "late"
    assert gate.held("t") == 0
