"""Optional OTLP TracerProvider for model-gateway (D13 B — soft-fail)."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)
_provider: Any | None = None


def setup_tracing(service_name: str = "yal-model-gateway") -> None:
    """Configure TracerProvider + OTLP exporter when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.

    Soft-fail: missing deps, bad endpoint, or export errors never raise — agent
    traffic must not depend on the collector.
    """
    global _provider
    endpoint = (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    if not endpoint:
        return
    name = os.environ.get("OTEL_SERVICE_NAME", service_name)
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        sample_ratio = float(os.environ.get("YAL_OTEL_SAMPLE_RATIO", "1.0"))
        sample_ratio = max(0.0, min(1.0, sample_ratio))
        resource = Resource.create({"service.name": name})
        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
        )
        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=5)
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=int(os.environ.get("YAL_OTEL_MAX_QUEUE", "2048")),
                max_export_batch_size=512,
                schedule_delay_millis=5000,
                export_timeout_millis=10000,
            )
        )
        trace.set_tracer_provider(provider)
        _provider = provider
        logger.info(
            "model-gateway OTLP configured endpoint=%s sample_ratio=%s",
            endpoint,
            sample_ratio,
        )
    except Exception:
        logger.exception("model-gateway OTLP setup failed; continuing without export")
        _provider = None


def shutdown_tracing() -> None:
    global _provider
    if _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=5000)
        _provider.shutdown()
    except Exception:
        logger.exception("model-gateway tracer provider shutdown failed")
    _provider = None
