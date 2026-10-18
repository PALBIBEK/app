"""D13 §8 T3 — OTLP endpoint down must soft-fail; agent run still completes."""

from __future__ import annotations

import os

import pytest

from supervisor.agent.events import RunEnd
from supervisor.obs import tracing as tracing_mod
from supervisor.obs.tracing import setup_tracing, shutdown_tracing
from tests.d13_acceptance.conftest import (
    HEADERS,
    TENANT,
    create_session,
    new_command_id,
    wait_for_journal,
    w3c_traceparent,
)

TRACE_ID = "c1b2c3d4e5f60718293a4b5c6d7e8f92"


class _BoomSink:
    """Exporter that always fails — must never abort the agent path."""

    def export(self, spans: list) -> None:
        raise RuntimeError("otlp collector unreachable (D13 T3 fault injection)")


@pytest.mark.asyncio
async def test_t3_otlp_down_agent_run_still_completes(
    supervisor_stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T3: Failing span sink / unreachable OTLP → RunEnd still lands; no hard fail."""
    client = supervisor_stack["client"]
    reader = supervisor_stack["reader"]
    manager = supervisor_stack["manager"]

    # Replace projector sink with hard-failing exporter (soft-fail contract).
    shutdown_tracing()
    tracing_mod._projector = None
    tracing_mod._provider = None
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    setup_tracing(
        tenant_id=TENANT,
        harness_id="kg",
        harness_version="kg-v1",
        sink=_BoomSink(),  # type: ignore[arg-type]
    )
    assert tracing_mod.get_projector() is not None

    created = await create_session(client)
    sid = created["session_id"]

    resp = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers={**HEADERS, "traceparent": w3c_traceparent(TRACE_ID)},
        json={"text": "survive otlp outage", "command_id": new_command_id("t3")},
    )
    assert resp.status_code in (200, 202), resp.text

    events = await wait_for_journal(
        reader,
        sid,
        min_events=1,
        require_types={"RunEnd"},
        timeout_secs=5.0,
    )
    ends = [e for e in events if isinstance(e.payload, RunEnd)]
    assert ends, "agent run must complete (RunEnd) when OTLP/exporter is down"

    # Session must remain usable — not poisoned by projector exceptions.
    status = await manager.session_status(sid)
    assert status is not None
    assert str(status) not in ("failed", "budget_exceeded"), (
        f"OTLP soft-fail must not fail the session; status={status!r}"
    )

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
