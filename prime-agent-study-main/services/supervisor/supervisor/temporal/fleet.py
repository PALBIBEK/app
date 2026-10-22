"""The worker fleet: who is serving the session queue, how busy they are, what is waiting.

The production shape (LangGraph's API servers and queue workers, Temporal workers): API
processes only accept work; workers only do it, and you add workers for more load. That only
works if someone can see the fleet, so three signals are combined here:

- **Pollers**, from the Temporal server: a worker that recently asked the queue for work. This
  is the ground truth for "is anyone serving", and needs nothing from the workers.
- **Backlog**, from the Temporal server's task queue stats: how many tasks are waiting and how
  long the oldest has waited. Work that waits means too few workers (or none).
- **Heartbeats**, from the workers themselves (a row each, refreshed every few seconds): how
  many runs each can carry (``WORKER_MAX_CONCURRENT_RUNS``) and how many it carries now.

``assess`` turns those into ``ok``, ``degraded`` or ``down`` with plain alerts; the API serves
it as JSON (``GET /v1/ops/workers``) and as Prometheus text for alerting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)

from supervisor.db.models import WorkerHeartbeatRow

logger = logging.getLogger(__name__)

HEARTBEAT_S: Final = 10.0
"""How often a worker refreshes its heartbeat row."""

STALE_AFTER_S: Final = 3 * HEARTBEAT_S
"""A heartbeat older than this belongs to a worker that is gone (crashed, or cut off)."""

POLLED_WITHIN_S: Final = 90.0
"""A poller seen this recently is serving (a long poll lasts up to a minute)."""


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


# --- What a worker carries now -------------------------------------------------------------


class Load:
    """Runs a worker is carrying right now: activities executing (one per run's current step)."""

    def __init__(self) -> None:
        self.busy = 0

    def interceptor(self) -> Interceptor:
        load = self

        class _Inbound(ActivityInboundInterceptor):
            async def execute_activity(self, input: ExecuteActivityInput) -> Any:
                load.busy += 1
                try:
                    return await super().execute_activity(input)
                finally:
                    load.busy -= 1

        class _Counting(Interceptor):
            def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
                return _Inbound(next)

        return _Counting()


# --- Heartbeats ----------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerBeat:
    worker_id: str
    host: str
    task_queue: str
    home_queue: str
    max_runs: int
    busy: int
    started_at_ms: int
    last_seen_ms: int
    version: str = ""


class WorkerHeartbeats:
    """One row per live worker, refreshed every ``HEARTBEAT_S``; removed on a clean stop."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def beat(self, b: WorkerBeat) -> None:
        async with self._sf() as db, db.begin():
            row = await db.get(WorkerHeartbeatRow, b.worker_id)
            if row is None:
                db.add(WorkerHeartbeatRow(**asdict(b)))
            else:
                for k, v in asdict(b).items():
                    setattr(row, k, v)

    async def gone(self, worker: str) -> None:
        async with self._sf() as db, db.begin():
            await db.execute(delete(WorkerHeartbeatRow).where(WorkerHeartbeatRow.worker_id == worker))

    async def live(self, task_queue: str, *, now_ms: int | None = None) -> list[WorkerBeat]:
        """Workers on ``task_queue`` that beat recently; rows long gone stale are cleared."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        fresh_since = now - int(STALE_AFTER_S * 1000)
        async with self._sf() as db, db.begin():
            await db.execute(
                delete(WorkerHeartbeatRow).where(WorkerHeartbeatRow.last_seen_ms < now - 3_600_000)
            )
            rows = (
                await db.execute(
                    select(WorkerHeartbeatRow)
                    .where(WorkerHeartbeatRow.task_queue == task_queue)
                    .where(WorkerHeartbeatRow.last_seen_ms >= fresh_since)
                    .order_by(WorkerHeartbeatRow.started_at_ms)
                )
            ).scalars().all()
        return [
            WorkerBeat(
                worker_id=r.worker_id, host=r.host, task_queue=r.task_queue, home_queue=r.home_queue,
                max_runs=r.max_runs, busy=r.busy, started_at_ms=r.started_at_ms,
                last_seen_ms=r.last_seen_ms, version=r.version or "",
            )
            for r in rows
        ]


async def heartbeat_loop(
    store: WorkerHeartbeats,
    beat: Callable[[], WorkerBeat],
    stop: asyncio.Event,
    *,
    on_beat: Callable[[bool], None] | None = None,
    interval_s: float = HEARTBEAT_S,
) -> None:
    """Refresh this worker's row until ``stop``, then remove it (a clean stop is not a crash)."""
    last: WorkerBeat | None = None
    while not stop.is_set():
        last = beat()
        try:
            await store.beat(last)
            ok = True
        except Exception:
            logger.warning("worker heartbeat failed", exc_info=True)
            ok = False
        if on_beat is not None:
            on_beat(ok)
        try:
            await asyncio.wait_for(stop.wait(), interval_s)
        except TimeoutError:
            pass
    if last is not None:
        try:
            await store.gone(last.worker_id)
        except Exception:
            logger.warning("could not clear the worker heartbeat", exc_info=True)


# --- The queue, from the Temporal server ---------------------------------------------------


@dataclass(frozen=True)
class QueueStats:
    """The shared session queue as the Temporal server sees it (workflow and activity tasks)."""

    workflow_pollers: list[str] = field(default_factory=list)
    activity_pollers: list[str] = field(default_factory=list)
    backlog: int = 0
    backlog_age_s: float = 0.0
    add_rate: float = 0.0
    dispatch_rate: float = 0.0


async def queue_stats(client: Client, task_queue: str, *, timeout_s: float = 3.0) -> QueueStats:
    from datetime import timedelta

    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

    now = time.time()
    pollers: dict[int, list[str]] = {}
    backlog = 0
    age = add = dispatch = 0.0
    for kind in (TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY):
        resp = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=client.namespace,
                task_queue=TaskQueue(name=task_queue),
                task_queue_type=kind,
                report_stats=True,
            ),
            timeout=timedelta(seconds=timeout_s),
        )
        pollers[kind] = sorted(
            {p.identity for p in resp.pollers if now - p.last_access_time.ToSeconds() <= POLLED_WITHIN_S}
        )
        if resp.HasField("stats"):
            st = resp.stats
            backlog += int(st.approximate_backlog_count)
            age = max(age, st.approximate_backlog_age.ToTimedelta().total_seconds())
            add += float(st.tasks_add_rate)
            dispatch += float(st.tasks_dispatch_rate)
    return QueueStats(
        workflow_pollers=pollers[TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW],
        activity_pollers=pollers[TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY],
        backlog=backlog,
        backlog_age_s=round(age, 1),
        add_rate=round(add, 3),
        dispatch_rate=round(dispatch, 3),
    )


# --- Assessment ----------------------------------------------------------------------------


@dataclass(frozen=True)
class FleetAlert:
    code: str  # no_workers | backlog | at_capacity
    message: str


@dataclass(frozen=True)
class FleetStatus:
    status: str  # ok | degraded | down
    task_queue: str
    polling: int  # workers the Temporal server saw polling
    workers: list[WorkerBeat]
    slots_max: int
    slots_busy: int
    backlog: int
    backlog_age_s: float
    add_rate: float
    dispatch_rate: float
    alerts: list[FleetAlert]
    checked_at_ms: int


def assess(
    task_queue: str, queue: QueueStats, workers: list[WorkerBeat], *, backlog_alert_s: float = 30.0,
    heartbeats: bool = True, now_ms: int | None = None,
) -> FleetStatus:
    """The fleet's health from what the server and the workers say.

    The server keeps listing a stopped worker as a poller for a minute or more (an idle long
    poll looks the same), so with ``heartbeats`` tracked a worker serves only while its
    heartbeat is fresh: a crash shows within ``STALE_AFTER_S``, a clean stop at once. The
    pollers still have to be there (a worker that cannot reach Temporal serves nobody).
    """
    polling = len(set(queue.workflow_pollers) | set(queue.activity_pollers))
    serving = bool(queue.workflow_pollers) and bool(queue.activity_pollers)
    if heartbeats:
        serving = serving and bool(workers)
    slots_max = sum(w.max_runs for w in workers)
    slots_busy = sum(w.busy for w in workers)
    alerts: list[FleetAlert] = []
    if not serving:
        alerts.append(FleetAlert(
            "no_workers",
            "No agent worker is running: new sessions and messages wait until one starts",
        ))
    if queue.backlog and queue.backlog_age_s >= backlog_alert_s:
        alerts.append(FleetAlert(
            "backlog",
            f"Work has waited {queue.backlog_age_s:.0f}s for a worker ({queue.backlog} queued)",
        ))
    if serving and workers and slots_busy >= slots_max:
        alerts.append(FleetAlert(
            "at_capacity",
            f"Every worker is at capacity ({slots_busy}/{slots_max} runs): new runs wait for a free slot",
        ))
    status = "down" if not serving else ("degraded" if alerts else "ok")
    return FleetStatus(
        status=status, task_queue=task_queue, polling=polling, workers=workers,
        slots_max=slots_max, slots_busy=slots_busy, backlog=queue.backlog,
        backlog_age_s=queue.backlog_age_s, add_rate=queue.add_rate, dispatch_rate=queue.dispatch_rate,
        alerts=alerts, checked_at_ms=now_ms if now_ms is not None else int(time.time() * 1000),
    )


def prometheus(s: FleetStatus) -> str:
    """The fleet as Prometheus text (``deploy/alerts/workers.rules.yml`` alerts on it)."""
    q = s.task_queue.replace("\\", "\\\\").replace('"', '\\"')
    up = {"ok": 1, "degraded": 1, "down": 0}[s.status]
    lines = [
        "# HELP yal_workers_polling Workers the Temporal server saw polling the session queue.",
        "# TYPE yal_workers_polling gauge",
        f'yal_workers_polling{{task_queue="{q}"}} {s.polling}',
        "# HELP yal_workers_serving 1 while at least one worker serves the session queue.",
        "# TYPE yal_workers_serving gauge",
        f'yal_workers_serving{{task_queue="{q}"}} {up}',
        "# HELP yal_worker_slots Runs the fleet can carry at once (sum of WORKER_MAX_CONCURRENT_RUNS).",
        "# TYPE yal_worker_slots gauge",
        f'yal_worker_slots{{task_queue="{q}"}} {s.slots_max}',
        "# HELP yal_worker_slots_busy Runs the fleet is carrying now.",
        "# TYPE yal_worker_slots_busy gauge",
        f'yal_worker_slots_busy{{task_queue="{q}"}} {s.slots_busy}',
        "# HELP yal_task_queue_backlog Tasks waiting for a worker.",
        "# TYPE yal_task_queue_backlog gauge",
        f'yal_task_queue_backlog{{task_queue="{q}"}} {s.backlog}',
        "# HELP yal_task_queue_backlog_age_seconds How long the oldest waiting task has waited.",
        "# TYPE yal_task_queue_backlog_age_seconds gauge",
        f'yal_task_queue_backlog_age_seconds{{task_queue="{q}"}} {s.backlog_age_s}',
    ]
    return "\n".join(lines) + "\n"
