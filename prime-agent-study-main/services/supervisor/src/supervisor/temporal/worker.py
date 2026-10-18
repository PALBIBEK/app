"""Session worker: runs session workflows and owns every live session runtime.

Two pollers share one client:
- the shared task queue: workflows, plus activities that may run anywhere
  (create, adopt);
- this process's own queue: activities that must reach the runtime it holds
  in memory (the worker-specific task queue pattern).

Run with ``python -m supervisor.temporal.worker``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import socket
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

from temporalio.client import Client
from temporalio.worker import Worker

from supervisor.app import SupervisorState
from supervisor.bootstrap import bootstrap_supervisor
from supervisor.config import Settings
from supervisor.maintenance import approval_sweeper, idle_reaper
from supervisor.session import drain_for_deploy
from supervisor.temporal.activities import SessionActivities
from supervisor.temporal.client import connect
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.workflow import AgentSessionWorkflow

_log = logging.getLogger(__name__)

# In-flight activities get this long to finish on shutdown before being cancelled;
# a cancelled turn is retried or adopted by another worker.
GRACEFUL_SHUTDOWN = timedelta(seconds=30)


def home_queue(task_queue: str) -> str:
    return f"{task_queue}@{socket.gethostname()}-{uuid4().hex[:8]}"


@contextlib.asynccontextmanager
async def session_worker(
    client: Client, state: SupervisorState, *, task_queue: str
) -> AsyncIterator[SessionActivities]:
    """Run the shared and home-queue workers for as long as the context is open."""
    assert state.writer is not None
    activities = SessionActivities(
        state.manager,
        reader=state.reader,
        writer=state.writer,
        home_queue=home_queue(task_queue),
    )
    shared = Worker(
        client,
        task_queue=task_queue,
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        activities=activities.all(),
        graceful_shutdown_timeout=GRACEFUL_SHUTDOWN,
    )
    home = Worker(
        client,
        task_queue=activities.home_queue,
        activities=activities.all(),
        graceful_shutdown_timeout=GRACEFUL_SHUTDOWN,
    )
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
        _log.info("orphan reaper: %s", stats)
    except Exception:
        _log.exception("orphan reaper failed")


async def run(settings: Settings, stop: asyncio.Event) -> None:
    state = await bootstrap_supervisor(settings)
    client = await connect(settings.temporal)
    await _reap_orphan_sandboxes(state)
    async with session_worker(client, state, task_queue=settings.temporal.task_queue) as activities:
        _log.info(
            "session worker up queue=%s home=%s",
            settings.temporal.task_queue,
            activities.home_queue,
        )
        loops = [asyncio.create_task(approval_sweeper(state.manager, should_stop=stop.is_set))]
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
        _log.info("shutting down: finishing in-flight activities")
        for loop in loops:
            loop.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
    # Workers have stopped taking work; park the runtimes this process still holds.
    await drain_for_deploy(state.manager, timeout_seconds=state.drain_grace_s)


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
