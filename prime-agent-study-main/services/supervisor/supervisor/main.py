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

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from supervisor.api import (
    agui,
    approvals,
    browser,
    catalog,
    events,
    harness,
    health,
    memory,
    metrics,
    model_providers,
    models,
    notebooks,
    ops,
    overview,
    principals,
    runners,
    runs,
    scopes,
    session_files,
    sessions,
    subagents,
    tenant_secrets,
    tools,
    traces,
    workspace,
)
from supervisor.api.sessions import end_and_erase
from supervisor.app import SupervisorState
from supervisor.blocking import threads, widen_request_threads
from supervisor.bootstrap import bootstrap_supervisor
from supervisor.config import Settings
from supervisor.obs.responses import CountResponses, ResponseCounts
from supervisor.session.retention import Retention, RetentionSweeper, run_retention_loop

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
            await connect(temporal, lazy=True), task_queue=temporal.task_queue,
            backlog_alert_s=temporal.backlog_alert_s,
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

    retention = _start_retention(state)
    blob_sweep = _start_blob_sweep(state)

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

    for background in (retention, blob_sweep):
        if background is not None:
            await _stop_loop(*background)
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
            await state.manager.drain_trace_rollups()
        except Exception:
            logger.exception("drain failed")
        if state.watch is not None:
            await state.watch.close()
        try:
            from supervisor.obs.tracing import shutdown_tracing

            shutdown_tracing()
        except Exception:
            logger.exception("tracing shutdown failed")


def _start_retention(state: SupervisorState | None) -> tuple[asyncio.Task[None], asyncio.Event] | None:
    """Erase ended sessions past ``SESSION_RETENTION_DAYS`` (``supervisor.session.retention``)."""
    if state is None or (retention := Retention.from_env()) is None:
        return None
    stop = asyncio.Event()
    sweeper = RetentionSweeper(state.reader.session_factory, retention, erase=lambda sid: end_and_erase(state, sid))
    task = asyncio.create_task(run_retention_loop(sweeper, retention.sweep_s, stop), name="session-retention")
    logger.info("session retention: ended sessions are erased after %d days", retention.days)
    return task, stop


def _start_blob_sweep(state: SupervisorState | None) -> tuple[asyncio.Task[None], asyncio.Event] | None:
    """Delete, hourly, the blobs no journal event names (a write that failed after storing
    one) once they are a day old (``supervisor.journal.blobrefs``)."""
    if state is None or state.reader.blobs is None:
        return None
    from supervisor.journal.blobrefs import BlobSweeper, run_blob_sweep_loop

    stop = asyncio.Event()
    sweeper = BlobSweeper(state.reader.session_factory, state.reader.blobs)
    return asyncio.create_task(run_blob_sweep_loop(sweeper, 3600.0, stop), name="journal-blob-sweep"), stop


async def _stop_loop(task: asyncio.Task[None], stop: asyncio.Event) -> None:
    stop.set()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=10)


async def _reject_nul_in_url(request: Request, call_next: Any) -> Any:
    """A NUL byte in a path or query reached Postgres (500 'invalid byte sequence')."""
    if "\x00" in request.url.path or "%00" in str(request.url.query).lower():
        return JSONResponse({"detail": "the URL must not contain NUL characters"}, status_code=400)
    return await call_next(request)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Validation errors without echoing the input: echoing a deeply nested body back
    recursed past Python's limit and turned a bad request into a 500."""
    errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
    try:
        return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)
    except RecursionError:
        return JSONResponse({"detail": "the request body is nested too deeply"}, status_code=422)


def create_app(*, state: SupervisorState | None = None) -> FastAPI:
    app = FastAPI(title="PRIME Supervisor", lifespan=lifespan)
    if state is not None:
        app.state.supervisor = state
    app.middleware("http")(_reject_nul_in_url)
    # Outermost: every response counts, the ones refused above too (GET /v1/ops/metrics).
    app.state.responses = ResponseCounts()
    app.add_middleware(CountResponses, counts=app.state.responses)
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(catalog.router)
    app.include_router(sessions.router)
    app.include_router(principals.router)
    app.include_router(events.router)
    app.include_router(agui.router)
    app.include_router(browser.router)
    app.include_router(approvals.router)
    app.include_router(tools.router)
    app.include_router(harness.router)
    app.include_router(workspace.router)
    app.include_router(session_files.router)
    app.include_router(runs.router)
    app.include_router(ops.router)
    app.include_router(scopes.router)
    app.include_router(memory.router)
    app.include_router(notebooks.router)
    app.include_router(subagents.router)
    app.include_router(models.router)
    app.include_router(model_providers.router)
    app.include_router(tenant_secrets.router)
    app.include_router(runners.router)
    app.include_router(overview.router)
    app.include_router(traces.router)
    return app


app = create_app()


def _flag(name: str) -> bool:
    """A listener is on unless its flag says off (``0``, ``false``, ``no``, ``off``)."""
    return os.environ.get(name, "1").strip().lower() not in {"0", "false", "no", "off", ""}


class WithRequestThreads:
    """An app served with its loop's request threads widened as it starts
    (``supervisor.blocking.widen_request_threads``): Starlette runs a synchronous route,
    and each read of a synchronous stream, on 40 threads per loop otherwise, and the model
    gateway's every model call is one, held for the whole reply. Done here, where the apps
    are served, because the gateway and the memory service import nothing of the rest of
    the supervisor.

    A wrapper, not a change to the app: the app's own lifespan stays its own, and wrapping
    one twice widens nothing twice (the size is a maximum). The limiter belongs to the
    loop, so it is widened on the loop's first message, before the app's own startup."""

    def __init__(self, app: ASGIApp) -> None:
        # Checked here, as the server is built, never first in the lifespan: an error raised
        # there is taken by uvicorn ("auto") for a lifespan the app lacks, and the app would
        # be served without its own startup.
        self.threads = threads()
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            widen_request_threads(self.threads)  # the size checked as the server was built
        await self.app(scope, receive, send)

    def __getattr__(self, name: str) -> Any:
        """The app's own attributes (``state``, ``router``), for whatever reads them through
        the server's config. ``app`` itself, asked before it is set (a copy, an unpickle),
        is missing, not looked up through itself without end."""
        if name == "app":
            raise AttributeError(name)
        return getattr(self.app, name)


def listeners(settings: Settings) -> list[tuple[str, Any]]:
    """What this process serves: the API, the model gateway, the memory service.

    Each with ``lifespan="on"``: under uvicorn's "auto", a failing startup is taken for an
    app without a lifespan, and served without its startup having run.

    One process, one port each (8080, 8090, 8092 by default), so their URLs stay
    what they were as separate services. Each can be switched off: a deploy with
    several API copies serves the gateway (whose virtual keys live in its
    memory) from one copy only (``SERVE_MODEL_GATEWAY=0`` on the others).
    """
    import uvicorn

    out: list[tuple[str, Any]] = []
    if _flag("SERVE_SUPERVISOR_API"):
        out.append(("api", uvicorn.Config(WithRequestThreads(app), host=settings.host, port=settings.port, lifespan="on")))
    if _flag("SERVE_MODEL_GATEWAY"):
        port = int(os.environ.get("MODEL_GATEWAY_PORT", "8090"))
        from supervisor.gateway.app import app as gateway_app

        out.append(("model-gateway", uvicorn.Config(WithRequestThreads(gateway_app), host=settings.host, port=port, lifespan="on")))
    if _flag("SERVE_MEMORY_SERVICE"):
        from supervisor.memory_service.app import create_app as memory_app
        from supervisor.memory_service.db import database_url_from_env

        url = database_url_from_env()
        host = os.environ.get("MEMORY_HOST", settings.host)
        port = int(os.environ.get("MEMORY_PORT", "8092"))
        out.append(("memory-service", uvicorn.Config(WithRequestThreads(memory_app(database_url=url)), host=host, port=port, lifespan="on")))
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
