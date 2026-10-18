"""In-memory span sink for downstream development (Phase1 §5.1)."""

from __future__ import annotations

from supervisor.telemetry_contract.spans import SpanRecord


class FakeSpanSink:
    """Accepts contract-shaped spans; optional validation on export.

    Re-exports of the same span_id update the stored record in place so
    open→close mutations stay visible to tests (and match OTLP live-span behaviour).
    """

    def __init__(self, *, validate: bool = True) -> None:
        self.validate = validate
        self.spans: list[SpanRecord] = []
        self._by_id: dict[str, SpanRecord] = {}

    def export(self, spans: list[SpanRecord]) -> None:
        for s in spans:
            if self.validate and s.end_ms is not None:
                # Only validate completed spans — open starts may lack end attrs.
                from supervisor.telemetry_contract.validate import validate_span

                validate_span(s)
            elif self.validate and s.attributes.get("gen_ai.operation.name") == "chat":
                from supervisor.telemetry_contract.validate import validate_span

                validate_span(s)
            elif self.validate and s.end_ms is None:
                # light check: must have an operation attr
                if (
                    "gen_ai.operation.name" not in s.attributes
                    and "yal.operation.name" not in s.attributes
                ):
                    from supervisor.telemetry_contract.validate import ValidationError

                    raise ValidationError("missing operation")
            sid = s.span_id
            if sid and sid in self._by_id:
                existing = self._by_id[sid]
                existing.end_ms = s.end_ms
                existing.status = s.status
                existing.attributes.update(s.attributes)
                continue
            self.spans.append(s)
            if sid:
                self._by_id[sid] = s

    def clear(self) -> None:
        self.spans.clear()
        self._by_id.clear()
