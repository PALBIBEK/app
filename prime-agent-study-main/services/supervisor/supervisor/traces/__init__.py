"""Run traces: each run of a session as a tree of typed spans, built from its journal.

``build`` turns a run's events into spans (``model``) of a few kinds (``kinds``); ``store``
keeps each run's rollup in ``run_traces`` for lists and filters. The journal stays the
source of truth: a trace can always be rebuilt from it, with the same span ids.
"""

from supervisor.traces.build import build_run_trace, span_id, trace_id
from supervisor.traces.kinds import EXPORT, Export, SpanKind
from supervisor.traces.model import Marker, Rollup, RunStatus, RunTrace, Span, SpanStatus, TimeSplit

__all__ = [
    "EXPORT",
    "Export",
    "Marker",
    "Rollup",
    "RunStatus",
    "RunTrace",
    "Span",
    "SpanKind",
    "SpanStatus",
    "TimeSplit",
    "build_run_trace",
    "span_id",
    "trace_id",
]
