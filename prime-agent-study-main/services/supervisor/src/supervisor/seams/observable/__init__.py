"""
Observable seams with OpenTelemetry tracing and resilience.

This module provides decorator-based observability for seam operations:
- Distributed tracing (OpenTelemetry)
- Metrics collection (Prometheus)
- Circuit breaker integration
- Structured logging

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │  Observable Wrapper                                          │
    │  - Traces all method calls                                   │
    │  - Records latency, errors, success rate                    │
    │  - Propagates context (trace_id, span_id)                   │
    └─────────────────────────────────────────────────────────────┘
                              │
    ┌─────────────────────────────────────────────────────────────┐
    │  Resilient Wrapper                                           │
    │  - Circuit breaker per provider                              │
    │  - Retry with backoff                                        │
    │  - Fallback to secondary provider                           │
    └─────────────────────────────────────────────────────────────┘
                              │
    ┌─────────────────────────────────────────────────────────────┐
    │  Underlying Seam (Protocol Implementation)                   │
    │  - ToolProvider, ModelGateway, etc.                         │
    └─────────────────────────────────────────────────────────────┘

Usage:
    ```python
    from supervisor.seams.observable import (
        traced,
        metered,
        resilient,
        TracedToolProvider,
        ResilientModelGateway,
    )

    # Decorator-based
    class MyProvider:
        @traced("tool.invoke")
        @metered("tool_invocations")
        async def invoke(self, ctx, name, args):
            ...

    # Wrapper-based
    provider = TracedToolProvider(inner_provider)
    gateway = ResilientModelGateway(primary, fallback)
    ```
"""

from supervisor.seams.observable.tracing import (
    traced,
    span_attributes,
    get_tracer,
    get_current_span,
    inject_context,
    extract_context,
)
from supervisor.seams.observable.metrics import (
    metered,
    counter,
    histogram,
    gauge,
    get_meter,
)
from supervisor.seams.observable.wrappers import (
    TracedToolProvider,
    TracedModelGateway,
    ResilientModelGateway,
    ResilientToolProvider,
)

__all__ = [
    # Tracing
    "traced",
    "span_attributes",
    "get_tracer",
    "get_current_span",
    "inject_context",
    "extract_context",
    # Metrics
    "metered",
    "counter",
    "histogram",
    "gauge",
    "get_meter",
    # Wrappers
    "TracedToolProvider",
    "TracedModelGateway",
    "ResilientModelGateway",
    "ResilientToolProvider",
]
