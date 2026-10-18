"""Supervisor service entrypoint — FastAPI app, lifespan, DI, drain."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from supervisor.api import (
    approvals,
    catalog,
    events,
    health,
    memory,
    metrics,
    scopes,
    sessions,
    subagents,
    tools,
    workspace,
)
from supervisor.app import SupervisorState
from supervisor.bootstrap import bootstrap_supervisor
from supervisor.config import Settings
from supervisor.session import drain_for_deploy

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state: SupervisorState | None = getattr(app.state, "supervisor", None)
    if state is None:
        settings = Settings.from_env()
        state = await bootstrap_supervisor(settings)
        app.state.supervisor = state
        _log.info(
            "supervisor bootstrapped catalog_dir=%s compositions=%s",
            settings.resolved_catalog_dir(),
            "loaded" if state.composition_catalog is not None else "disabled",
        )
    loop = asyncio.get_running_loop()

    reap_task: asyncio.Task[None] | None = None
    if state is not None and state.idle_reap_max_seconds > 0:
        from supervisor.session import reap_idle

        async def _idle_reap_loop() -> None:
            interval = max(5.0, state.idle_reap_interval_seconds)
            while not state.draining:
                try:
                    reaped = await reap_idle(
                        state.manager,
                        max_idle_seconds=state.idle_reap_max_seconds,
                    )
                    if reaped:
                        _log.info(
                            "idle reaper: reaped %d sessions %s",
                            len(reaped),
                            reaped[:8],
                        )
                except Exception:
                    _log.exception("idle reaper failed")
                await asyncio.sleep(interval)

        reap_task = asyncio.create_task(_idle_reap_loop(), name="idle-reaper")

    approval_sweeper_task: asyncio.Task[None] | None = None
    if state is not None:
        async def _approval_sweeper_loop() -> None:
            interval = 5.0
            while not state.draining:
                try:
                    expired = await state.manager.expire_overdue_approvals()
                    if expired:
                        _log.info(
                            "approval-sweeper: expired %d approvals",
                            len(expired),
                        )
                except Exception:
                    _log.exception("approval sweeper failed")
                await asyncio.sleep(interval)

        approval_sweeper_task = asyncio.create_task(
            _approval_sweeper_loop(), name="approval-sweeper"
        )
        _log.info("approval_sweeper started interval_s=5")

    span_reaper_task: asyncio.Task[None] | None = None
    span_reaper_stop = asyncio.Event()
    if state is not None:
        from supervisor.obs.reaper import (
            ReaperConfig,
            SpanReaper,
            otlp_export_enabled,
            run_reaper_loop,
        )
        from supervisor.obs.tracing import get_projector

        if otlp_export_enabled():
            projector = get_projector()
            if projector is not None:
                cfg = ReaperConfig.from_env()
                span_reaper = SpanReaper(projector=projector, config=cfg)
                span_reaper_task = asyncio.create_task(
                    run_reaper_loop(
                        span_reaper, cfg.reaper_interval_s, span_reaper_stop
                    ),
                    name="span-reaper",
                )
                _log.info(
                    "span_reaper started interval_s=%s",
                    cfg.reaper_interval_s,
                )

    if state is not None and state.sandbox_provider is not None:
        try:
            from supervisor.sandbox.reaper import reap_orphans

            async def _reattach(session_id: str, sbx) -> None:  # type: ignore[no-untyped-def]
                state.manager.reattach_sandbox(session_id, sbx)

            stats = await reap_orphans(
                state.sandbox_provider,
                lookup_session=state.manager.session_status,
                reattach=_reattach,
            )
            _log.info("orphan reaper: %s", stats)
        except Exception:
            _log.exception("orphan reaper failed")

    # The server (uvicorn) owns SIGTERM and runs the shutdown path below when it
    # receives it. Capture its handler so ours can mark the service as draining
    # first and then hand off; replacing it outright leaves the process running
    # forever with /readyz stuck at 503.
    server_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _begin_drain() -> None:
        _log.info("SIGTERM received — entering drain")
        if state is not None:
            state.draining = True
        if callable(server_sigterm_handler):
            server_sigterm_handler(signal.SIGTERM, None)
        else:
            # No server handler installed (e.g. run under a bare event loop):
            # fall back to the default behaviour of terminating the process.
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.raise_signal(signal.SIGTERM)

    try:
        loop.add_signal_handler(signal.SIGTERM, _begin_drain)
    except (NotImplementedError, RuntimeError):
        pass

    yield

    if span_reaper_task is not None:
        span_reaper_stop.set()
        try:
            await asyncio.wait_for(span_reaper_task, timeout=10)
        except TimeoutError:
            span_reaper_task.cancel()
            try:
                await span_reaper_task
            except asyncio.CancelledError:
                pass
            _log.warning("span_reaper did not stop cleanly; cancelled")
        except asyncio.CancelledError:
            pass

    if reap_task is not None:
        reap_task.cancel()
        try:
            await reap_task
        except asyncio.CancelledError:
            pass

    if approval_sweeper_task is not None:
        approval_sweeper_task.cancel()
        try:
            await approval_sweeper_task
        except asyncio.CancelledError:
            pass

    if state is not None:
        state.draining = True
        try:
            await drain_for_deploy(
                state.manager, timeout_seconds=state.drain_grace_s
            )
        except Exception:
            _log.exception("drain failed")
        try:
            from supervisor.obs.tracing import shutdown_tracing

            shutdown_tracing()
        except Exception:
            _log.exception("tracing shutdown failed")


def create_app(*, state: SupervisorState | None = None) -> FastAPI:
    app = FastAPI(title="YAL Supervisor", lifespan=lifespan)
    if state is not None:
        app.state.supervisor = state
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(catalog.router)
    app.include_router(sessions.router)
    app.include_router(events.router)
    app.include_router(approvals.router)
    app.include_router(tools.router)
    app.include_router(workspace.router)
    app.include_router(scopes.router)
    app.include_router(memory.router)
    app.include_router(subagents.router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    from supervisor.config import Settings

    settings = Settings.from_env()
    uvicorn.run(
        "supervisor.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
