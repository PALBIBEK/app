"""Parse / generate W3C trace ids for Envelope.trace_id + outbound traceparent."""

from __future__ import annotations

import re
from contextvars import ContextVar
from uuid import uuid4

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
# version-traceid-spanid-flags
_TRACEPARENT = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$",
    re.IGNORECASE,
)

# D13 B: supervisor → gateway W3C propagation (set on RunStart, cleared on RunEnd).
_outbound_traceparent: ContextVar[str | None] = ContextVar(
    "yal_outbound_traceparent", default=None
)


def new_trace_id() -> str:
    return uuid4().hex


def parse_traceparent(header: str | None) -> str | None:
    """Return 32-hex trace id from a W3C traceparent header, or None."""
    if not header:
        return None
    m = _TRACEPARENT.match(header.strip())
    if not m:
        # bare 32-hex also accepted
        bare = header.strip().lower()
        if _HEX32.match(bare):
            return bare
        return None
    return m.group(2).lower()


def format_traceparent(trace_id: str, *, span_id: str | None = None) -> str:
    """Build a valid W3C traceparent for the given 32-hex trace id."""
    tid = trace_id.lower()
    sid = (span_id or uuid4().hex[:16]).lower()
    return f"00-{tid}-{sid}-01"


def set_outbound_traceparent(value: str | None) -> None:
    """Seed W3C traceparent for subsequent HttpModelGatewayClient calls."""
    _outbound_traceparent.set(value)


def current_outbound_traceparent() -> str | None:
    """Explicit ContextVar, else active OTel span context, else None."""
    explicit = _outbound_traceparent.get()
    if explicit:
        return explicit
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is not None and getattr(ctx, "is_valid", False):
            return (
                f"00-{format(ctx.trace_id, '032x')}-"
                f"{format(ctx.span_id, '016x')}-01"
            )
    except ImportError:
        pass
    return None
