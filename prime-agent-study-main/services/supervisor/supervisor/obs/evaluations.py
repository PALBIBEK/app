"""Scores exported to OpenTelemetry as GenAI evaluation events.

Every verdict on a run (a check, a judge, a person's good or bad) is also an OpenTelemetry
event, so the backend that holds the run's spans holds how it was judged. The event is the
GenAI semantic conventions' ``gen_ai.evaluation.result`` (``open-telemetry/semantic-
conventions-genai`` ``model/gen-ai/events.yaml``): ``gen_ai.evaluation.name`` (required),
``gen_ai.evaluation.score.value`` and ``gen_ai.evaluation.score.label`` (when there are
any) and ``gen_ai.evaluation.explanation``, "parented to the GenAI operation span being
evaluated": here, the run's ``invoke_agent`` span.

It is emitted as ``opentelemetry-util-genai`` emits its events
(``open-telemetry/opentelemetry-python-contrib`` ``util/opentelemetry-util-genai``,
``_inference_invocation.py``): a ``LogRecord`` with ``event_name``, its attributes and the
evaluated span's context, through the Logs API. The span's ids are the ones the projector
exported (``supervisor.obs.projector``: the root span hashes ``(session, run, "run")``; the
trace id is the run id when that is 32 hex digits, as ``OtlpBridgeSink`` forces it). With no
log provider configured (no OTLP endpoint) the Logs API drops the event, and emitting never
raises to the caller.
"""

from __future__ import annotations

import logging
from typing import Any

from supervisor.traces.build import span_id as trace_span_id

__all__ = ["EVENT", "emit_evaluation", "evaluation_attributes"]

logger = logging.getLogger(__name__)

EVENT = "gen_ai.evaluation.result"


def evaluation_attributes(
    *, name: str, label: str | None, value: float | None, explanation: str | None, source: str,
    evaluator: str | None, session_id: str, run_id: str,
) -> dict[str, Any]:
    """The event's attributes: the convention's, then which verdict it is (``yal.*``)."""
    attrs: dict[str, Any] = {"gen_ai.evaluation.name": name}
    if value is not None:
        attrs["gen_ai.evaluation.score.value"] = float(value)
    if label:
        attrs["gen_ai.evaluation.score.label"] = label
    if explanation:
        attrs["gen_ai.evaluation.explanation"] = explanation[:1000]
    attrs.update({"yal.evaluation.source": source, "yal.session.id": session_id, "yal.run.id": run_id})
    if evaluator:
        attrs["yal.evaluation.evaluator"] = evaluator
    return attrs


def _context(session_id: str, run_id: str) -> Any:
    """The run's exported root span as a parent context, or None when its trace id is not the
    run id (a run id that is not 32 hex digits gets a random trace id at export)."""
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context

    try:
        trace_id = int(run_id, 16) if len(run_id) == 32 else 0
    except ValueError:
        trace_id = 0
    if not trace_id:
        return None
    span = int(trace_span_id(session_id, run_id, "run"), 16)
    return set_span_in_context(
        NonRecordingSpan(SpanContext(trace_id, span, is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)))
    )


def emit_evaluation(
    *, session_id: str, run_id: str, name: str, label: str | None, value: float | None,
    explanation: str | None, source: str, evaluator: str | None,
) -> None:
    """Emit one verdict as a ``gen_ai.evaluation.result`` event on the run's span."""
    try:
        from opentelemetry._logs import LogRecord, get_logger

        get_logger("yal-supervisor").emit(
            LogRecord(
                event_name=EVENT,
                attributes=evaluation_attributes(
                    name=name, label=label, value=value, explanation=explanation, source=source,
                    evaluator=evaluator, session_id=session_id, run_id=run_id,
                ),
                context=_context(session_id, run_id),
            )
        )
    except Exception:  # telemetry never breaks judging
        logger.warning("could not emit the evaluation of run %s", run_id, exc_info=True)
