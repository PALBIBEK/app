"""D13 §8 T6 — gateway child spans share the same trace as the supervisor run."""

from __future__ import annotations

from typing import Any

import httpx

from supervisor.obs.trace_context import (
    format_traceparent,
    parse_traceparent,
    set_outbound_traceparent,
)
from supervisor.seams.providers.http_gateway import HttpModelGatewayClient

TRACE_ID = "b2c3d4e5f60718293a4b5c6d7e8f90a1"


def _completion_json() -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 1,
        "model": "echo",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_t6_http_gateway_sends_traceparent_same_trace() -> None:
    """T6: HttpModelGatewayClient._headers carries W3C traceparent for the run trace_id."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("traceparent"))
        return httpx.Response(200, json=_completion_json())

    set_outbound_traceparent(format_traceparent(TRACE_ID, span_id="aabbccddeeff0011"))
    try:
        client = HttpModelGatewayClient(
            base_url="http://gateway.test",
            token="svc",
            default_model="echo",
        )
        client.close()
        client._client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://gateway.test",
        )
        from supervisor.seams.types import ChatMessage

        client.complete([ChatMessage(role="user", content="hi")], [], model="echo")
    finally:
        set_outbound_traceparent(None)

    assert seen, "expected gateway HTTP call"
    tp = seen[0]
    assert tp, "HttpModelGatewayClient must send traceparent (D13 §8 T6)"
    assert parse_traceparent(tp) == TRACE_ID, (
        f"gateway child must share run trace_id; got traceparent={tp!r}"
    )


def test_t6_headers_include_traceparent_when_outbound_set() -> None:
    """T6 companion: _headers() itself exposes traceparent (same-trace contract)."""
    set_outbound_traceparent(format_traceparent(TRACE_ID))
    try:
        client = HttpModelGatewayClient(base_url="http://gateway.test")
        headers = client._headers()
        client.close()
    finally:
        set_outbound_traceparent(None)

    assert "traceparent" in headers
    assert parse_traceparent(headers["traceparent"]) == TRACE_ID


def test_t6_compose_model_gateway_otlp_endpoint() -> None:
    """T6 companion: product compose wires OTEL on model-gateway for Jaeger child spans."""
    from pathlib import Path

    from tests.d13_acceptance.conftest import AI_ROOT

    root = (AI_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    staging = AI_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
    combined = root
    if staging.is_file():
        combined += "\n" + staging.read_text(encoding="utf-8")

    # model-gateway service block must set OTEL_EXPORTER_OTLP_ENDPOINT (not only supervisor).
    assert "model-gateway:" in combined
    # After model-gateway heading, OTEL must appear before the next top-level service
    # that is not nested — simple presence of both is the gate used by verify.
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in combined
    # Ensure model-gateway section itself mentions OTEL (not only supervisor).
    mg_idx = combined.find("model-gateway:")
    assert mg_idx >= 0
    # Look at a window after model-gateway for OTEL env.
    window = combined[mg_idx : mg_idx + 2500]
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in window, (
        "compose model-gateway must set OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317 "
        "(D13 B / T6)"
    )


def test_t6_gateway_otel_setup_module_exists() -> None:
    """T6 companion: gateway ships TracerProvider/OTLP setup (soft-fail)."""
    from pathlib import Path

    from tests.d13_acceptance.conftest import AI_ROOT

    otel = (
        AI_ROOT
        / "services"
        / "model-gateway"
        / "src"
        / "model_gateway"
        / "otel.py"
    )
    assert otel.is_file(), "model_gateway.otel setup_tracing missing (D13 B TracerProvider)"
    src = otel.read_text(encoding="utf-8")
    assert "TracerProvider" in src
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in src
    assert "soft" in src.lower() or "except" in src

    app = (
        AI_ROOT
        / "services"
        / "model-gateway"
        / "src"
        / "model_gateway"
        / "app.py"
    ).read_text(encoding="utf-8")
    assert "setup_tracing" in app, "gateway startup must call setup_tracing"
