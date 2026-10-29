"""Session worker: runs session workflows and owns every live session runtime.

Two pollers share one client:
- the shared task queue: workflows, plus activities that may run anywhere
  (create, adopt);
- this process's own queue: activities that must reach the runtime it holds
  in memory (the worker-specific task queue pattern).

Run with ``python -m supervisor worker`` (the image's ``worker`` entry point).

Each worker carries at most ``WORKER_MAX_CONCURRENT_RUNS`` runs at once (its activity slots);
run more workers on the same queue for more. It writes a heartbeat (slots and load) every few
seconds, which ``GET /v1/ops/workers`` combines with the Temporal server's pollers and backlog,
and with ``WORKER_HEALTH_PORT`` set it answers ``/healthz`` and ``/readyz`` for its orchestrator.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import socket
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

from temporalio.client import Client
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.worker import Worker, WorkerDeploymentConfig

from supervisor.app import SupervisorState
from supervisor.bootstrap import bootstrap_supervisor
from supervisor.config import Settings
from supervisor.maintenance import approval_sweeper, idle_reaper, sandbox_idle_sweeper
from supervisor.temporal.activities import SessionActivities
from supervisor.temporal.client import connect
from supervisor.temporal.fleet import STALE_AFTER_S, Load, WorkerBeat, heartbeat_loop, worker_id
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.steps import HEARTBEAT_EVERY
from supervisor.temporal.workflow import AgentSessionWorkflow

logger = logging.getLogger(__name__)

# In-flight activities get this long to finish on shutdown before being cancelled;
# a cancelled turn is retried or adopted by another worker.
GRACEFUL_SHUTDOWN = timedelta(seconds=30)


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("yal-supervisor")
    except PackageNotFoundError:
        return ""


def deployment_config(version: WorkerDeploymentVersion) -> WorkerDeploymentConfig:
    """Register as ``version`` of a worker deployment, every workflow auto-upgrading.

    Sessions live for hours or days and a job's worker may be replaced at any time, so each
    workflow moves to a new build at its next workflow task rather than pinning old workers
    in place: a rolling deploy can stop the old ones as soon as the new build is current.
    That is safe because changes to recorded history are guarded with ``workflow.patched``
    and checked by the replay gate (supervisor.temporal.rollout). Temporal refuses a
    behaviour declared on the workflow class where workers run unversioned, hence here.
    """
    return WorkerDeploymentConfig(
        version=version, use_worker_versioning=True, default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE
    )


def home_queue(task_queue: str) -> str:
    return f"{task_queue}@{socket.gethostname()}-{uuid4().hex[:8]}"


@contextlib.asynccontextmanager
async def session_worker(
    client: Client,
    state: SupervisorState,
    *,
    task_queue: str,
    max_concurrent_runs: int | None = None,
    load: Load | None = None,
    identity: str | None = None,
    deployment: WorkerDeploymentVersion | None = None,
) -> AsyncIterator[SessionActivities]:
    """Run the shared and home-queue workers for as long as the context is open.

    ``max_concurrent_runs`` caps the activities each poller runs at once (one per run's current
    step); ``load`` counts the ones running now. ``deployment`` registers both pollers as one
    version of a worker deployment (supervisor.temporal.rollout).
    """
    assert state.writer is not None
    activities = SessionActivities(
        state.manager,
        reader=state.reader,
        writer=state.writer,
        home_queue=home_queue(task_queue),
        heartbeats=state.heartbeats,
    )
    state.manager.holds.holder = activities.home_queue
    tuning: dict[str, Any] = {
        "graceful_shutdown_timeout": GRACEFUL_SHUTDOWN,
        # A cancel (a stop) reaches an activity only on a heartbeat's answer; heartbeats
        # sent at most this often let a stopped tool hear it within seconds, not 24 s.
        "max_heartbeat_throttle_interval": HEARTBEAT_EVERY,
    }
    if max_concurrent_runs:
        tuning["max_concurrent_activities"] = max_concurrent_runs
    if load is not None:
        tuning["interceptors"] = [load.interceptor()]
    if identity:
        tuning["identity"] = identity
    if deployment is not None:
        tuning["deployment_config"] = deployment_config(deployment)
    shared = Worker(
        client,
        task_queue=task_queue,
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        activities=activities.all(),
        **tuning,
    )
    home = Worker(client, task_queue=activities.home_queue, activities=activities.all(), **tuning)
    async with shared, home:
        yield activities


async def _reap_orphan_sandboxes(state: SupervisorState) -> None:
    if state.sandbox_provider is None:
        return
    from supervisor.sandbox.reaper import reap_orphans

    async def reattach(session_id: str, sandbox) -> None:  # type: ignore[no-untyped-def]
        state.manager.reattach_sandbox(session_id, sandbox)

    try:
        stats = await reap_orphans(
            state.sandbox_provider,
            lookup_session=state.manager.session_status,
            reattach=reattach,
        )
        logger.info("orphan reaper: %s", stats)
    except Exception:
        logger.exception("orphan reaper failed")


class Health:
    """What ``/healthz`` and ``/readyz`` answer: alive while heartbeats land; ready while serving."""

    def __init__(self) -> None:
        self.serving = False
        self.stopping = False
        self.last_beat_ok_at = 0.0

    def on_beat(self, ok: bool) -> None:
        if ok:
            self.last_beat_ok_at = time.monotonic()

    def alive(self) -> tuple[bool, str]:
        if self.last_beat_ok_at and time.monotonic() - self.last_beat_ok_at > STALE_AFTER_S:
            return False, "heartbeats are failing (database unreachable?)"
        return True, "ok"

    def ready(self) -> tuple[bool, str]:
        if self.stopping:
            return False, "draining"
        if not self.serving:
            return False, "starting"
        return self.alive()


def health_app(health: Health):  # type: ignore[no-untyped-def]
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="session worker health", docs_url=None, redoc_url=None, openapi_url=None)

    def _answer(result: tuple[bool, str]) -> JSONResponse:
        ok, reason = result
        return JSONResponse({"status": "ok" if ok else "unhealthy", "reason": reason}, status_code=200 if ok else 503)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return _answer(health.alive())

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        return _answer(health.ready())

    return app


async def _serve_health(health: Health, port: int, stop: asyncio.Event) -> None:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(health_app(health), host="0.0.0.0", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    await stop.wait()
    server.should_exit = True
    await task


async def _bootstrap_version(client: Client, deployment: str, build_id: str, mode: str) -> None:
    from supervisor.temporal.rollout import bootstrap

    try:
        outcome = await bootstrap(client, deployment, build_id, mode=mode)
        logger.info("worker version %s.%s: %s", deployment, build_id, outcome)
    except Exception:
        logger.exception("worker version %s.%s could not be promoted", deployment, build_id)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    state = await bootstrap_supervisor(settings)
    client = await connect(settings.temporal)
    await _reap_orphan_sandboxes(state)
    cfg = settings.temporal
    me, load, health = worker_id(), Load(), Health()
    started_ms = int(time.time() * 1000)
    # Health and heartbeats outlive ``stop``: they keep answering while in-flight work drains.
    finished = asyncio.Event()
    side: list[asyncio.Task[None]] = []
    if cfg.worker_health_port:
        side.append(asyncio.create_task(_serve_health(health, cfg.worker_health_port, finished)))
    version = WorkerDeploymentVersion(cfg.deployment_name, cfg.build_id) if cfg.versioned else None
    async with session_worker(
        client, state, task_queue=cfg.task_queue, max_concurrent_runs=cfg.max_concurrent_runs,
        load=load, identity=me, deployment=version,
    ) as activities:
        health.serving = True
        if version is not None:
            side.append(asyncio.create_task(_bootstrap_version(client, cfg.deployment_name, cfg.build_id, cfg.rollout)))
        logger.info(
            "session worker up id=%s queue=%s home=%s max_concurrent_runs=%s",
            me, cfg.task_queue, activities.home_queue, cfg.max_concurrent_runs,
        )
        if state.heartbeats is not None:
            home = activities.home_queue

            def beat() -> WorkerBeat:
                return WorkerBeat(
                    worker_id=me, host=socket.gethostname(), task_queue=cfg.task_queue, home_queue=home,
                    max_runs=cfg.max_concurrent_runs, busy=load.busy, started_at_ms=started_ms,
                    last_seen_ms=int(time.time() * 1000), version=cfg.build_id or _version(),
                )

            side.append(asyncio.create_task(heartbeat_loop(state.heartbeats, beat, finished, on_beat=health.on_beat)))
        loops = [
            asyncio.create_task(approval_sweeper(state.manager, should_stop=stop.is_set)),
            asyncio.create_task(
                sandbox_idle_sweeper(
                    state.manager, should_stop=stop.is_set, interval_s=state.manager.sandbox_lifecycle.sweep_s
                )
            ),
        ]
        if state.idle_reap_max_seconds > 0:
            loops.append(
                asyncio.create_task(
                    idle_reaper(
                        state.manager,
                        should_stop=stop.is_set,
                        max_idle_s=state.idle_reap_max_seconds,
                        interval_s=state.idle_reap_interval_seconds,
                    )
                )
            )
        await stop.wait()
        health.stopping = True
        logger.info("shutting down: finishing in-flight activities")
        for loop in loops:
            loop.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
    # Workers have stopped taking work; park the runtimes this process still holds.
    await state.manager.drain(timeout_seconds=state.drain_grace_s)
    await state.manager.drain_trace_rollups()
    finished.set()
    await asyncio.gather(*side, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(Settings.from_env(), stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
