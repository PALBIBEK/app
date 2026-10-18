"""OpenTelemetry tracing setup + per-session journal projector wiring (Phase1 §5.2b–c)."""

from __future__ import annotations

import logging
import os
from typing import Any

from supervisor.agent.events import Envelope
from supervisor.obs.projector import JournalProjector, SpanSink
from supervisor.telemetry_contract.fakes import FakeSpanSink
from supervisor.telemetry_contract.spans import SpanRecord

_log = logging.getLogger(__name__)
_projector: "ProjectorHub | JournalProjector | None" = None
_provider: Any = None
_shared_sink: SpanSink | None = None
_default_tenant: str = "default"
_default_harness_id: str = "unknown"
_default_harness_version: str = "unknown"


class _LoggingSink:
    """Default sink when OTLP is not configured — never raises to callers."""

    def export(self, spans: list[SpanRecord]) -> None:
        for s in spans:
            _log.debug(
                "span name=%s op=%s session=%s run=%s",
                s.name,
                s.attributes.get("gen_ai.operation.name")
                or s.attributes.get("yal.operation.name"),
                s.yal_session_id,
                s.yal_run_id,
            )


class ProjectorHub:
    """Per-session/run projector state — no global open-span map cross-talk (D13 T4)."""

    def __init__(
        self,
        sink: SpanSink,
        *,
        tenant_id: str,
        harness_id: str,
        harness_version: str,
    ) -> None:
        self._sink = sink
        self._default_tenant = tenant_id
        self._default_harness_id = harness_id
        self._default_harness_version = harness_version
        self._by_session: dict[str, JournalProjector] = {}
        self.closed_by_counts: dict[str, int] = {}

    def ensure_session(
        self,
        session_id: str,
        *,
        tenant_id: str | None = None,
        harness_id: str | None = None,
        harness_version: str | None = None,
    ) -> JournalProjector:
        proj = self._by_session.get(session_id)
        if proj is None:
            proj = JournalProjector(
                sink=self._sink,
                tenant_id=tenant_id or self._default_tenant,
                harness_id=harness_id or self._default_harness_id,
                harness_version=harness_version or self._default_harness_version,
            )
            self._by_session[session_id] = proj
            return proj
        # Refresh pins from control plane when provided (never trust agent IDs).
        if tenant_id:
            proj.tenant_id = tenant_id
        if harness_id:
            proj.harness_id = harness_id
        if harness_version:
            proj.harness_version = harness_version
        return proj

    def project(self, env: Envelope) -> None:
        proj = self.ensure_session(env.session_id)
        proj.project(env)
        self._merge_counts(proj)

    def project_many(self, envs: list[Envelope]) -> None:
        for env in envs:
            self.project(env)

    @property
    def has_open_run(self) -> bool:
        return any(p.has_open_run for p in self._by_session.values())

    @property
    def open_run_id(self) -> str | None:
        for p in self._by_session.values():
            if p.has_open_run:
                return p.open_run_id
        return None

    @property
    def run_start_ms(self) -> int | None:
        for p in self._by_session.values():
            if p.has_open_run:
                return p.run_start_ms
        return None

    @property
    def last_event_ms(self) -> int | None:
        last: int | None = None
        for p in self._by_session.values():
            ms = p.last_event_ms
            if ms is not None and (last is None or ms > last):
                last = ms
        return last

    def force_close(self, *, end_ms: int, closed_by: str) -> bool:
        closed_any = False
        for _sid, proj in list(self._by_session.items()):
            if proj.force_close(end_ms=end_ms, closed_by=closed_by):
                closed_any = True
                self.closed_by_counts[closed_by] = (
                    self.closed_by_counts.get(closed_by, 0) + 1
                )
        return closed_any

    def force_close_session(
        self, session_id: str, *, end_ms: int, closed_by: str
    ) -> bool:
        proj = self._by_session.get(session_id)
        if proj is None:
            return False
        ok = proj.force_close(end_ms=end_ms, closed_by=closed_by)
        if ok:
            self.closed_by_counts[closed_by] = (
                self.closed_by_counts.get(closed_by, 0) + 1
            )
        return ok

    def reconstruct(
        self,
        envs: list[Envelope],
        *,
        session_id: str | None = None,
        tenant_id: str | None = None,
        harness_id: str | None = None,
        harness_version: str | None = None,
    ) -> None:
        if not envs and session_id is None:
            return
        sid = session_id or (envs[0].session_id if envs else None)
        if sid is None:
            return
        proj = self.ensure_session(
            sid,
            tenant_id=tenant_id,
            harness_id=harness_id,
            harness_version=harness_version,
        )
        proj.reconstruct(envs)
        self._merge_counts(proj)

    def drop_session(self, session_id: str) -> None:
        self._by_session.pop(session_id, None)

    def session_projector(self, session_id: str) -> JournalProjector | None:
        return self._by_session.get(session_id)

    def _merge_counts(self, proj: JournalProjector) -> None:
        for key, val in proj.closed_by_counts.items():
            # Keep hub totals at least as high as per-session (idempotent bump).
            self.closed_by_counts[key] = max(self.closed_by_counts.get(key, 0), val)


def setup_tracing(
    service_name: str = "yal-supervisor",
    *,
    tenant_id: str = "default",
    harness_id: str = "unknown",
    harness_version: str = "unknown",
    sink: SpanSink | None = None,
) -> ProjectorHub:
    """Configure per-session projector hub. OTLP when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.

    Soft-fail: missing deps / setup errors → logging sink. Never raises.
    """
    global _projector, _shared_sink, _default_tenant, _default_harness_id
    global _default_harness_version
    name = os.environ.get("OTEL_SERVICE_NAME", service_name)
    chosen: SpanSink
    if sink is not None:
        chosen = sink
    elif os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            chosen = _build_otlp_sink(name)
        except Exception:
            _log.exception("OTLP setup failed; using logging sink")
            chosen = _LoggingSink()
    else:
        chosen = _LoggingSink()

    _shared_sink = chosen
    _default_tenant = tenant_id
    _default_harness_id = harness_id
    _default_harness_version = harness_version
    _projector = ProjectorHub(
        sink=chosen,
        tenant_id=tenant_id,
        harness_id=harness_id,
        harness_version=harness_version,
    )
    return _projector


def get_projector() -> ProjectorHub | JournalProjector | None:
    return _projector


def force_flush_tracing(timeout_millis: int = 10000) -> bool:
    """Flush pending OTLP batches. Safe no-op if no provider."""
    if _provider is None:
        return True
    try:
        return bool(_provider.force_flush(timeout_millis=timeout_millis))
    except Exception:
        _log.exception("tracer provider force_flush failed")
        return False


def shutdown_tracing() -> None:
    """Flush/shutdown TracerProvider if one was created."""
    global _provider, _projector, _shared_sink
    if _provider is not None:
        try:
            force_flush_tracing()
            _provider.shutdown()
        except Exception:
            _log.exception("tracer provider shutdown failed")
        _provider = None
    _projector = None
    _shared_sink = None


def _build_otlp_sink(service_name: str) -> SpanSink:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    from supervisor.obs.otlp_sink import OtlpBridgeSink, _ForcedIdGenerator

    global _provider

    endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
    max_queue = int(os.environ.get("YAL_OTEL_MAX_QUEUE", "2048"))
    sample_ratio = float(os.environ.get("YAL_OTEL_SAMPLE_RATIO", "1.0"))
    sample_ratio = max(0.0, min(1.0, sample_ratio))

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
        id_generator=_ForcedIdGenerator(),
    )
    exporter = OTLPSpanExporter(endpoint=endpoint, timeout=5)
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_queue_size=max_queue,
            max_export_batch_size=min(512, max_queue),
            schedule_delay_millis=5000,
            export_timeout_millis=10000,
        )
    )
    trace.set_tracer_provider(provider)
    _provider = provider
    tracer = trace.get_tracer("yal-supervisor")
    _log.info("OTLP exporter configured endpoint=%s sample_ratio=%s", endpoint, sample_ratio)
    return OtlpBridgeSink(tracer)


def make_test_projector(**kwargs: Any) -> JournalProjector:
    sink = kwargs.pop("sink", None) or FakeSpanSink()
    return JournalProjector(
        sink=sink,
        tenant_id=kwargs.get("tenant_id", "test"),
        harness_id=kwargs.get("harness_id", "test"),
        harness_version=kwargs.get("harness_version", "test-v1"),
    )


def make_inmemory_otlp_projector(
    *,
    tenant_id: str = "t1",
    harness_id: str = "coding",
    harness_version: str = "coding-v1",
) -> tuple[JournalProjector, Any]:
    """Test helper: projector → OTel InMemorySpanExporter (no network)."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from supervisor.obs.otlp_sink import OtlpBridgeSink, _ForcedIdGenerator

    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create({"service.name": "yal-supervisor-test"}),
        id_generator=_ForcedIdGenerator(),
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("yal-test")
    sink = OtlpBridgeSink(tracer)
    proj = JournalProjector(
        sink=sink,
        tenant_id=tenant_id,
        harness_id=harness_id,
        harness_version=harness_version,
    )
    return proj, exporter
