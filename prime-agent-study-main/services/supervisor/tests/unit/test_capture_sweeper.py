"""Each worker's capture sweeper (G3) and how a leased job ends, against fakes of the queue.

The queue's own rules (claims, leases, cursors) are tested against the service in
``tests/memory_store/test_capture_queue.py``; here, what the worker does with them.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from supervisor.memory import sweeper, turn
from supervisor.memory.client import RouteMissing
from supervisor.memory_service.capture.errors import CaptureInvalid, StaleLease
from supervisor.memory_service.capture.queue import CaptureJob, CaptureLease
from supervisor.memory_service.notebooks.errors import NotebookNotFound

pytestmark = pytest.mark.asyncio


def _lease(tenant: str, n: int = 1) -> CaptureLease:
    job = CaptureJob(f"cap-{tenant}-{n}", tenant, f"s-{tenant}", f"{tenant}~nb", (f"r{n}",), n, n, 1)
    return CaptureLease(job, "host:1", 0)


class FakeQueue:
    """What the sweeper and ``run_capture`` call on ``NotebookClient``."""

    def __init__(self, work: dict[str, list[CaptureLease]]) -> None:
        self.work = work
        self.listed: list[tuple[int, float]] = []
        self.claims: list[str] = []
        self.ended: list[tuple[str, str, Any]] = []

    async def capture_tenants(self, *, limit: int = 32, pending_grace_s: float = 0) -> list[str]:
        self.listed.append((limit, pending_grace_s))
        return [t for t, jobs in self.work.items() if jobs][:limit]

    async def capture_claim(self, *, tenant_id: str, **_: Any) -> CaptureLease | None:
        self.claims.append(tenant_id)
        jobs = self.work.get(tenant_id) or []
        return jobs.pop(0) if jobs else None

    async def capture_commit(self, lease: CaptureLease, outcome: dict[str, Any], **_: Any) -> bool:
        self.ended.append(("commit", lease.job.job_id, outcome))
        return False

    async def capture_fail(self, lease: CaptureLease, error: str, *, terminal: bool) -> None:
        self.ended.append(("fail", lease.job.job_id, terminal))

    async def capture_release(self, lease: CaptureLease, error: str) -> None:
        self.ended.append(("release", lease.job.job_id, error))

    async def capture_renew(self, lease: CaptureLease, lease_s: float) -> CaptureLease:
        self.ended.append(("renew", lease.job.job_id, lease_s))
        return lease


class FakeMemory:
    def __init__(self, hold: asyncio.Event | None = None) -> None:
        self.ran: list[str] = []
        self.cancelled: list[str] = []
        self.hold = hold

    async def run_capture(self, lease: CaptureLease) -> int:
        self.ran.append(lease.job.job_id)
        try:
            if self.hold is not None:
                await self.hold.wait()
        except asyncio.CancelledError:
            self.cancelled.append(lease.job.job_id)  # ``run_capture`` releases its lease here
            raise
        return 0


def test_sweep_concurrency_is_read_from_the_environment() -> None:
    assert sweeper.sweep_concurrency({}) == sweeper.DEFAULT_CONCURRENCY
    assert sweeper.sweep_concurrency({sweeper.CONCURRENCY_ENV: " 5 "}) == 5
    assert sweeper.sweep_concurrency({sweeper.CONCURRENCY_ENV: "0"}) == 0
    for bad in ("-1", "two", "1.5"):
        with pytest.raises(ValueError, match=sweeper.CONCURRENCY_ENV):
            sweeper.sweep_concurrency({sweeper.CONCURRENCY_ENV: bad})


async def test_a_round_serves_every_tenant_once_before_any_tenant_twice() -> None:
    queue = FakeQueue({"busy": [_lease("busy", n) for n in range(1, 4)], "quiet": [_lease("quiet")]})
    memory = FakeMemory()
    rounds = iter([False, True])

    await sweeper.capture_sweeper(memory, queue, should_stop=lambda: next(rounds), concurrency=4, interval_s=0)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert memory.ran[:2] == ["cap-busy-1", "cap-quiet-1"], "one each first, in the service's order"
    assert sorted(memory.ran[2:]) == ["cap-busy-2", "cap-busy-3"], "then free slots go to the tenant left with work"
    assert queue.listed == [(4 * sweeper.LIST_PER_SLOT, sweeper.PENDING_GRACE_S)], "pending jobs are their activity's first"


async def test_the_sweeper_runs_no_more_than_its_concurrency_and_releases_on_stop() -> None:
    queue = FakeQueue({t: [_lease(t)] for t in ("a", "b", "c")})
    hold = asyncio.Event()
    memory = FakeMemory(hold)
    stop = asyncio.Event()

    task = asyncio.create_task(
        sweeper.capture_sweeper(memory, queue, should_stop=stop.is_set, concurrency=2, interval_s=0.01)  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.1)
    assert len(memory.ran) == 2 and queue.work["c"], "two at once; the third waits for a free slot"
    stop.set()
    await asyncio.wait_for(task, 5)
    assert sorted(memory.cancelled) == sorted(memory.ran), "cancelled when it stopped, not waited on"


async def test_a_sweeper_set_to_zero_never_claims() -> None:
    queue = FakeQueue({"a": [_lease("a")]})
    await sweeper.capture_sweeper(FakeMemory(), queue, should_stop=lambda: False, concurrency=0)  # type: ignore[arg-type]
    assert queue.listed == []


async def test_a_claim_failing_mid_round_still_runs_the_leases_taken() -> None:
    class Flaky(FakeQueue):
        async def capture_claim(self, *, tenant_id: str, **kw: Any) -> CaptureLease | None:
            if tenant_id == "b":
                raise OSError("rate limited")
            return await super().capture_claim(tenant_id=tenant_id, **kw)

    queue = Flaky({"a": [_lease("a")], "b": [_lease("b")]})
    memory = FakeMemory()
    rounds = iter([False, True])
    await sweeper.capture_sweeper(memory, queue, should_stop=lambda: next(rounds), concurrency=2, interval_s=0)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    assert memory.ran == ["cap-a-1"], "taken, so run: never held until its lease runs out"


async def test_a_lease_is_renewed_while_its_job_runs(monkeypatch: pytest.MonkeyPatch, writable: None) -> None:
    monkeypatch.setattr(turn, "RENEW_EVERY_S", 0.01)
    queue = FakeQueue({})

    async def slow(*a: Any, **k: Any) -> int:
        await asyncio.sleep(0.1)
        return 0

    await _session_memory(queue, slow).run_capture(_job_lease())
    renewals = [e for e in queue.ended if e[0] == "renew"]
    assert len(renewals) >= 3 and renewals[0][2] == turn.CAPTURE_LEASE_S
    queue.ended.clear()
    await asyncio.sleep(0.05)
    assert queue.ended == [], "never renewed once its job ended"


async def test_an_unreachable_service_is_tried_again_next_round() -> None:
    class Down(FakeQueue):
        calls = 0

        async def capture_tenants(self, **kw: Any) -> list[str]:
            Down.calls += 1
            raise OSError("connection refused")

    rounds = iter([False, False, True])
    await sweeper.capture_sweeper(FakeMemory(), Down({}), should_stop=lambda: next(rounds), concurrency=1, interval_s=0)  # type: ignore[arg-type]
    assert Down.calls == 2


# --- how a leased job ends (``SessionMemory.run_capture``) ------------------------------------


def _session_memory(queue: FakeQueue, capture: Any, scope: Any = "scope") -> turn.SessionMemory:
    memory = object.__new__(turn.SessionMemory)
    memory._queue_for = lambda notebook_id: queue  # type: ignore[method-assign]

    async def _scope(session_id: str) -> Any:
        return scope

    memory.scope = _scope  # type: ignore[method-assign]
    memory._capture = capture  # type: ignore[method-assign]
    return memory


@pytest.fixture
def writable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(turn, "_writable", lambda scope: None if scope is None else "t~nb")


def _job_lease() -> CaptureLease:
    return CaptureLease(CaptureJob("cap-1", "t", "s", "t~nb", ("r1",), 1, 1, 1), "host:1", 0)


@pytest.mark.usefixtures("writable")
async def test_a_job_whose_session_no_longer_captures_there_is_committed_as_nothing() -> None:
    queue = FakeQueue({})

    async def never(*a: Any, **k: Any) -> int:
        raise AssertionError("nothing to extract")

    assert await _session_memory(queue, never, scope=None).run_capture(_job_lease()) == 0
    assert queue.ended == [("commit", "cap-1", turn.NOOP_OUTCOME)]


@pytest.mark.usefixtures("writable")
@pytest.mark.parametrize(
    ("raised", "ended"),
    [
        (turn.CaptureFailed("the model refused", retryable=True), ("fail", "cap-1", False)),
        (turn.CaptureFailed("too long at the floor", retryable=False), ("fail", "cap-1", True)),
        (NotebookNotFound("t~nb"), ("fail", "cap-1", True)),
        (CaptureInvalid("an outcome that cannot be kept"), ("fail", "cap-1", True)),
        (OSError("the service is unreachable"), ("fail", "cap-1", False)),
        (RouteMissing("t~nb"), ("fail", "cap-1", False)),  # a replica older than G3, mid-upgrade
    ],
)
async def test_a_failed_job_is_recorded_retryable_or_for_good(raised: Exception, ended: tuple[str, str, bool]) -> None:
    queue = FakeQueue({})

    async def capture(*a: Any, **k: Any) -> int:
        raise raised

    assert await _session_memory(queue, capture).run_capture(_job_lease()) == 0
    assert queue.ended == [ended]


@pytest.mark.usefixtures("writable")
async def test_a_job_taken_over_is_left_to_its_new_owner() -> None:
    queue = FakeQueue({})

    async def capture(*a: Any, **k: Any) -> int:
        raise StaleLease()

    assert await _session_memory(queue, capture).run_capture(_job_lease()) == 0
    assert queue.ended == [], "the new lease's holder records how it ends"


@pytest.mark.usefixtures("writable")
async def test_an_interrupted_job_is_released_at_once() -> None:
    queue = FakeQueue({})
    started = asyncio.Event()

    async def capture(*a: Any, **k: Any) -> int:
        started.set()
        await asyncio.sleep(3600)
        return 0

    running = asyncio.create_task(_session_memory(queue, capture).run_capture(_job_lease()))
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert queue.ended == [("release", "cap-1", "interrupted")]
