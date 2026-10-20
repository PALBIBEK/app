"""Supervisor service entrypoint — FastAPI app, lifespan, DI, drain."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from supervisor.api import (
    approvals,
    catalog,
    events,
    harness,
    health,
    memory,
    metrics,
    models,
    notebooks,
    runners,
    scopes,
    sessions,
    subagents,
    tenant_secrets,
    tools,
    workspace,
)
from supervisor.app import SupervisorState
from supervisor.bootstrap import bootstrap_supervisor
from supervisor.config import Settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state: SupervisorState | None = getattr(app.state, "supervisor", None)
    if state is None:
        settings = Settings.from_env()
        state = await bootstrap_supervisor(settings)
        app.state.supervisor = state
        logger.info(
            "supervisor bootstrapped catalog_dir=%s compositions=%s",
            settings.resolved_catalog_dir(),
            "loaded" if state.composition_catalog is not None else "disabled",
        )
    loop = asyncio.get_running_loop()

    if state is not None and state.workflows is None:
        from supervisor.temporal.client import connect
        from supervisor.temporal.sessions import SessionWorkflows

        temporal = Settings.from_env().temporal
        # Lazy: the API boots while Temporal starts; /readyz reports when it is reachable.
        state.workflows = SessionWorkflows(
            await connect(temporal, lazy=True), task_queue=temporal.task_queue
        )

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
                logger.info(
                    "span_reaper started interval_s=%s",
                    cfg.reaper_interval_s,
                )

    # The server (uvicorn) owns SIGTERM and runs the shutdown path below when it
    # receives it. Capture its handler so ours can mark the service as draining
    # first and then hand off; replacing it outright leaves the process running
    # forever with /readyz stuck at 503.
    server_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _begin_drain() -> None:
        logger.info("SIGTERM received — entering drain")
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
            logger.warning("span_reaper did not stop cleanly; cancelled")
        except asyncio.CancelledError:
            pass

    if state is not None:
        state.draining = True
        try:
            await state.manager.drain(timeout_seconds=state.drain_grace_s)
        except Exception:
            logger.exception("drain failed")
        try:
            from supervisor.obs.tracing import shutdown_tracing

            shutdown_tracing()
        except Exception:
            logger.exception("tracing shutdown failed")


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
    app.include_router(harness.router)
    app.include_router(workspace.router)
    app.include_router(scopes.router)
    app.include_router(memory.router)
    app.include_router(notebooks.router)
    app.include_router(subagents.router)
    app.include_router(models.router)
    app.include_router(tenant_secrets.router)
    app.include_router(runners.router)
    return app


app = create_app()


def _flag(name: str) -> bool:
    """A listener is on unless its flag says off (``0``, ``false``, ``no``, ``off``)."""
    return os.environ.get(name, "1").strip().lower() not in {"0", "false", "no", "off", ""}


def listeners(settings: Settings) -> list[tuple[str, Any]]:
    """What this process serves: the API, the model gateway, the memory service.

    One process, one port each (8080, 8090, 8092 by default), so their URLs stay
    what they were as separate services. Each can be switched off: a deploy with
    several API copies serves the gateway (whose virtual keys live in its
    memory) from one copy only (``SERVE_MODEL_GATEWAY=0`` on the others).
    """
    import uvicorn

    out: list[tuple[str, Any]] = []
    if _flag("SERVE_SUPERVISOR_API"):
        out.append(("api", uvicorn.Config("supervisor.main:app", host=settings.host, port=settings.port)))
    if _flag("SERVE_MODEL_GATEWAY"):
        port = int(os.environ.get("MODEL_GATEWAY_PORT", "8090"))
        out.append(("model-gateway", uvicorn.Config("supervisor.gateway.app:app", host=settings.host, port=port)))
    if _flag("SERVE_MEMORY_SERVICE"):
        from supervisor.memory_service.app import create_app as memory_app

        url = os.environ.get("MEMORY_DATABASE_URL", "sqlite+aiosqlite:///./memory.db")
        host = os.environ.get("MEMORY_HOST", settings.host)
        port = int(os.environ.get("MEMORY_PORT", "8092"))
        out.append(("memory-service", uvicorn.Config(memory_app(database_url=url), host=host, port=port)))
        # The API in this process keeps its notebooks there unless told otherwise.
        os.environ.setdefault("MEMORY_SERVICE_URL", f"http://127.0.0.1:{port}")
    return out


def serve(configs: list[tuple[str, Any]]) -> None:
    """Run every listener, each on its own thread and event loop.

    The first runs on the main thread as a plain uvicorn server: it owns SIGTERM
    and SIGINT, so the API's drain (chained onto uvicorn's handler at startup)
    works as when it ran alone. The others run on their own threads, so a
    blocking call from one to another (the API reading the gateway's model
    list) is answered instead of waiting on its own loop. When the first stops,
    the others are stopped and joined.
    """
    import threading

    import uvicorn

    class _Side(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            yield  # signals belong to the main thread's server

    (_, first), *rest = configs
    sides = [(_Side(config), name) for name, config in rest]
    threads = [
        threading.Thread(target=server.run, name=f"serve-{name}", daemon=True) for server, name in sides
    ]

    def stop_sides() -> None:
        for server, _ in sides:
            server.should_exit = True
        for thread in threads:
            thread.join(timeout=30)

    class _Main(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            # uvicorn re-raises the signal it caught when this block exits, which
            # ends the process: the others shut down (lifespans included) first.
            with super().capture_signals():
                try:
                    yield
                finally:
                    stop_sides()

    logger.info("serving %s", ", ".join(f"{name} :{c.port}" for name, c in configs))
    for thread in threads:
        thread.start()
    try:
        _Main(first).run()
    finally:
        stop_sides()


def main() -> None:
    configs = listeners(Settings.from_env())
    if not configs:
        raise SystemExit("nothing to serve: SERVE_SUPERVISOR_API, SERVE_MODEL_GATEWAY and SERVE_MEMORY_SERVICE are all off")
    serve(configs)


if __name__ == "__main__":
    main()
