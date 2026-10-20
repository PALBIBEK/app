"""Bridge SpanRecord → OpenTelemetry SDK spans (OTLP export)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from opentelemetry.sdk.trace.id_generator import IdGenerator

from supervisor.telemetry_contract.spans import SpanRecord, SpanStatus

logger = logging.getLogger(__name__)
_local = threading.local()


def _hex_to_int(value: str | None, bits: int) -> int | None:
    if not value:
        return None
    try:
        n = int(value, 16)
    except ValueError:
        return None
    mask = (1 << bits) - 1
    return n & mask


class _ForcedIdGenerator(IdGenerator):
    """IdGenerator that yields one forced trace/span id then falls back to random."""

    def __init__(self) -> None:
        from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

        self._random = RandomIdGenerator()
        self._forced_trace = False

    def generate_trace_id(self) -> int:
        forced = getattr(_local, "trace_id", None)
        if forced is not None:
            _local.trace_id = None
            self._forced_trace = True
            return int(forced)
        self._forced_trace = False
        return self._random.generate_trace_id()

    def generate_span_id(self) -> int:
        forced = getattr(_local, "span_id", None)
        if forced is not None:
            _local.span_id = None
            return int(forced)
        return self._random.generate_span_id()

    def is_trace_id_random(self) -> bool:
        # After a forced generate_trace_id, report non-random once.
        if self._forced_trace:
            self._forced_trace = False
            return False
        return True


class OtlpBridgeSink:
    """Export contract SpanRecords via an OTel Tracer. Never raises to caller.

    Open spans (end_ms is None) stay live until a later export with end_ms set.
    """

    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer
        self._contexts: dict[str, Any] = {}
        self._live: dict[str, Any] = {}

    def export(self, spans: list[SpanRecord]) -> None:
        try:
            from supervisor.telemetry_contract.validate import ValidationError, validate_span

            for record in spans:
                try:
                    validate_span(record)
                except ValidationError:
                    logger.warning(
                        "dropping invalid span name=%s session=%s run=%s",
                        record.name,
                        record.yal_session_id,
                        record.yal_run_id,
                        exc_info=True,
                    )
                    continue
                self._emit(record)
        except Exception:
            logger.exception("OTLP bridge export failed; dropping batch")

    def _apply_attrs(self, span: Any, record: SpanRecord) -> None:
        from opentelemetry.trace import Status, StatusCode

        for key, value in record.attributes.items():
            if value is None:
                continue
            if isinstance(value, (str, bool, int, float)):
                span.set_attribute(key, value)
            else:
                span.set_attribute(key, str(value))

        span.set_attribute("yal.tenant.id", record.yal_tenant_id)
        span.set_attribute("yal.session.id", record.yal_session_id)
        span.set_attribute("yal.run.id", record.yal_run_id)
        span.set_attribute("yal.harness.id", record.yal_harness_id)
        span.set_attribute("yal.harness.version", record.yal_harness_version)
        if record.journal_seq is not None:
            span.set_attribute("yal.journal.seq", record.journal_seq)

        if record.status == SpanStatus.ERROR:
            span.set_status(Status(StatusCode.ERROR))
        elif record.status == SpanStatus.OK:
            span.set_status(Status(StatusCode.OK))

    def _emit(self, record: SpanRecord) -> None:
        from opentelemetry.context import Context
        from opentelemetry.trace import NonRecordingSpan, set_span_in_context

        sid = record.span_id or ""
        if sid and sid in self._live:
            span = self._live[sid]
            self._apply_attrs(span, record)
            if record.end_ms is not None:
                span.end(end_time=record.end_ms * 1_000_000)
                del self._live[sid]
                self._contexts[sid] = span.get_span_context()
            return

        parent_ctx: Context = Context()
        if record.parent_span_id and record.parent_span_id in self._contexts:
            parent_ctx = set_span_in_context(
                NonRecordingSpan(self._contexts[record.parent_span_id])
            )
        elif record.parent_span_id and record.parent_span_id in self._live:
            parent_ctx = set_span_in_context(self._live[record.parent_span_id])
        elif record.yal_run_id and len(record.yal_run_id) == 32 and record.parent_span_id is None:
            tid = _hex_to_int(record.yal_run_id, 128)
            if tid:
                _local.trace_id = tid

        forced_sid = _hex_to_int(record.span_id, 64)
        if forced_sid:
            _local.span_id = forced_sid

        start_ns = (record.start_ms * 1_000_000) if record.start_ms else None
        span = self._tracer.start_span(
            record.name,
            context=parent_ctx,
            start_time=start_ns,
        )
        self._apply_attrs(span, record)
        if record.end_ms is not None:
            span.end(end_time=record.end_ms * 1_000_000)
            if sid:
                self._contexts[sid] = span.get_span_context()
        elif sid:
            self._live[sid] = span
            self._contexts[sid] = span.get_span_context()
