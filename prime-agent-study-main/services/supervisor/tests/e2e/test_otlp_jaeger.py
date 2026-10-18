"""C2 — live OTLP e2e against Jaeger (opt-in: RUN_OTLP_E2E=1)."""

from __future__ import annotations

import os
import time

import pytest

from supervisor.agent.events import (
    Envelope,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.obs.tracing import (
    force_flush_tracing,
    make_inmemory_otlp_projector,
    shutdown_tracing,
)
from tests.e2e.trace_assertions import assert_trace_shape

SECRET = "E2E_SECRET_BODY_ZZZ"
PATH = "/e2e/secret.txt"
TRACE = "1" * 32
SESSION = "e2e-s1"

pytestmark = pytest.mark.e2e


def _env(seq: int, payload: object, *, turn_id: str | None = None, trace_id: str = TRACE) -> Envelope:
    return Envelope(
        session_id=SESSION,
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=2_000 + seq,
        turn_id=turn_id,
        trace_id=trace_id,
        payload=payload,  # type: ignore[arg-type]
    )


def _echo_journal(*, trace_id: str = TRACE) -> list[Envelope]:
    turn = "t1"
    return [
        _env(0, RunStart(), trace_id=trace_id),
        _env(1, TurnStart(), turn_id=turn, trace_id=trace_id),
        _env(
            2,
            MessageEnd(
                role="assistant",
                content=[{"type": "text", "text": SECRET}],
                usage=Usage(10, 5, 0, 100, 0.01),
            ),
            turn_id=turn,
            trace_id=trace_id,
        ),
        _env(
            3,
            ToolStart(tool_call_id="c1", tool_name="read", args={"path": PATH}),
            turn_id=turn,
            trace_id=trace_id,
        ),
        _env(
            4,
            ToolEnd(
                tool_call_id="c1",
                tool_name="read",
                result={"text": SECRET},
                is_error=False,
            ),
            turn_id=turn,
            trace_id=trace_id,
        ),
        _env(5, TurnEnd(stop_reason="end"), turn_id=turn, trace_id=trace_id),
        _env(6, RunEnd(messages=[]), trace_id=trace_id),
    ]


def test_inmemory_otlp_trace_shape_shared_assertions() -> None:
    """Always-on: same shape checks as Jaeger e2e, via InMemorySpanExporter."""
    proj, exporter = make_inmemory_otlp_projector()
    for e in _echo_journal():
        proj.project(e)
    spans = list(exporter.get_finished_spans())
    assert_trace_shape(
        spans,
        session_id=SESSION,
        trace_id=TRACE,
        secret_strings=[SECRET, PATH],
    )


@pytest.mark.skipif(
    os.environ.get("RUN_OTLP_E2E") != "1",
    reason="Set RUN_OTLP_E2E=1 with Jaeger on :4317 (tests/e2e/docker-compose.yml)",
)
def test_echo_run_reaches_jaeger() -> None:
    import httpx
    import supervisor.obs.tracing as tracing_mod

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    tracing_mod._projector = None
    tracing_mod._provider = None

    try:
        proj = tracing_mod.setup_tracing(
            tenant_id="e2e", harness_id="coding", harness_version="coding-v1"
        )
        for e in _echo_journal():
            proj.project(e)
        assert force_flush_tracing(timeout_millis=15000)
    finally:
        shutdown_tracing()
        tracing_mod._projector = None

    url = f"http://localhost:16686/api/traces/{TRACE}"
    last_err: Exception | None = None
    for _ in range(40):
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                data = r.json()
                data_spans: list = []
                for tr in data.get("data") or []:
                    data_spans.extend(tr.get("spans") or [])
                if data_spans:
                    assert_trace_shape(
                        data_spans,
                        session_id=SESSION,
                        trace_id=TRACE,
                        secret_strings=[SECRET, PATH],
                    )
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(0.5)
    raise AssertionError(f"trace {TRACE} not found in Jaeger: {last_err}")


def test_otlp_endpoint_down_run_still_completes() -> None:
    """Fault injection: unreachable collector must not break projection."""
    import supervisor.obs.tracing as tracing_mod

    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://127.0.0.1:1"
    tracing_mod._projector = None
    tracing_mod._provider = None
    try:
        proj = tracing_mod.setup_tracing(
            tenant_id="t", harness_id="h", harness_version="v"
        )
        tid = "2" * 32
        for e in _echo_journal(trace_id=tid)[:2]:  # RunStart + TurnStart enough
            # rewrite session for isolation
            e2 = Envelope(
                session_id="fault-s",
                seq=e.seq,
                event_id=e.event_id,
                ts_ms=e.ts_ms,
                turn_id=e.turn_id,
                trace_id=tid,
                payload=e.payload,
            )
            proj.project(e2)
        proj.project(
            Envelope(
                session_id="fault-s",
                seq=99,
                event_id="end",
                ts_ms=9999,
                turn_id=None,
                trace_id=tid,
                payload=RunEnd(messages=[]),
            )
        )
    finally:
        shutdown_tracing()
        tracing_mod._projector = None
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
