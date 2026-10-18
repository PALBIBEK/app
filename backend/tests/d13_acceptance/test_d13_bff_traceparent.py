"""D13 §5 B — BFF AiClient injects W3C traceparent on supervisor calls when context present."""

from __future__ import annotations

import inspect
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient

TRACE_ID = "aa11bb22cc33dd44ee55ff6677889900"
W3C = f"00-{TRACE_ID}-1122334455667788-01"
PRIME = Path(__file__).resolve().parents[2] / "prime_agent"


def _ai_src() -> str:
    import prime_agent.services.ai_client as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


def test_ai_client_source_injects_traceparent() -> None:
    """AiClient must propagate W3C traceparent toward supervisor when context is present."""
    src = _ai_src()
    assert "traceparent" in src, (
        "AiClient must inject traceparent on supervisor calls (D13 §5 B) — missing today"
    )
    assert (
        '["traceparent"]' in src
        or "['traceparent']" in src
        or '"traceparent":' in src
        or "'traceparent':" in src
        or 'headers["traceparent"]' in src
        or "headers['traceparent']" in src
    ), "AiClient must assign the traceparent header, not only mention the word"


def test_auth_or_request_headers_include_traceparent_hook() -> None:
    """Header assembly (_auth_headers / request path) must carry traceparent."""
    src_auth = inspect.getsource(AiClient._auth_headers)
    src_json = inspect.getsource(AiClient._json_request)
    assert "traceparent" in (src_auth + "\n" + src_json + "\n" + _ai_src()), (
        "_auth_headers / _json_request path must include traceparent propagation"
    )


@pytest.mark.asyncio
async def test_post_message_sends_traceparent_when_context_present(
    d6_settings,
) -> None:
    """Behavioral: with an active trace context, POST /messages carries traceparent."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages") and request.method == "POST":
            seen.append(request.headers.get("traceparent"))
            return httpx.Response(202, json={"turn_id": "t-d13", "status": "accepted"})
        return httpx.Response(404, json={"detail": "not mocked"})

    client = AiClient(d6_settings, transport=httpx.MockTransport(handler))
    identity = TenantPrincipal(tenant_id="d13-tenant", principal_id="principal-d13")

    token = None
    seeded = False
    for name in (
        "set_outbound_traceparent",
        "inject_traceparent",
        "with_traceparent",
        "set_trace_context",
    ):
        fn = getattr(client, name, None)
        if callable(fn):
            try:
                fn(W3C)
            except TypeError:
                fn(TRACE_ID)
            seeded = True
            break

    if not seeded:
        try:
            from opentelemetry import context
            from opentelemetry.trace import (
                NonRecordingSpan,
                SpanContext,
                TraceFlags,
                set_span_in_context,
            )
            from opentelemetry.trace.span import TraceState

            span_ctx = SpanContext(
                trace_id=int(TRACE_ID, 16),
                span_id=int("1122334455667788", 16),
                is_remote=False,
                trace_flags=TraceFlags(0x01),
                trace_state=TraceState(),
            )
            token = context.attach(set_span_in_context(NonRecordingSpan(span_ctx)))
            seeded = True
        except Exception:
            token = None

    try:
        await client.post_message(
            identity,
            f"ai-{uuid4().hex[:12]}",
            text="d13-bff-trace",
            command_id=f"d13-{uuid4().hex}",
        )
    except Exception as exc:
        if not seen:
            pytest.fail(
                f"AiClient.post_message never reached supervisor with traceparent "
                f"(seeded={seeded}, error={exc!r}). Implement D13 §5 B header injection."
            )
    finally:
        if token is not None:
            from opentelemetry import context as otel_context

            otel_context.detach(token)

    assert seen, "POST /messages must be issued to supervisor"
    tp = seen[-1]
    assert tp, (
        "traceparent header missing on supervisor message call when context present "
        "(D13 §5 B — BFF AiClient injects traceparent)"
    )
    assert TRACE_ID in str(tp).lower(), f"expected trace_id in traceparent, got {tp!r}"


def test_bff_routes_do_not_hardcode_jaeger_or_supervisor_hosts() -> None:
    routes = PRIME / "web" / "routes"
    combined = ""
    for path in sorted(routes.glob("*.py")):
        combined += path.read_text(encoding="utf-8") + "\n"
    assert "localhost:16686" not in combined
    assert "localhost:8080" not in combined
