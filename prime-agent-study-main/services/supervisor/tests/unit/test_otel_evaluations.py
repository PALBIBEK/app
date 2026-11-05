"""Scores exported to OpenTelemetry: each check, judge verdict and person's mark is a
``gen_ai.evaluation.result`` event on the run's exported ``invoke_agent`` span."""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor

from supervisor.agent.events import (
    Envelope,
    MessageEnd,
    RunEnd,
    RunStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.journal.reader import JournalReader
from supervisor.obs.evaluations import EVENT, emit_evaluation
from supervisor.obs.projector import JournalProjector
from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.traces.after_run import after_run
from supervisor.traces.judges import save_judge
from tests.support.telemetry_fakes import FakeSpanSink
from tests.support.trace_api import ALICE, Api


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> InMemoryLogRecordExporter:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    monkeypatch.setattr("opentelemetry._logs.get_logger", lambda name, *a, **k: provider.get_logger(name))
    return exporter


def _emitted(exporter: InMemoryLogRecordExporter) -> list[Any]:
    return [r.log_record for r in exporter.get_finished_logs()]


def test_the_event_is_on_the_span_the_projector_exported(events: InMemoryLogRecordExporter) -> None:
    run = "9" * 32
    sink = FakeSpanSink(validate=False)
    proj = JournalProjector(sink=sink, tenant_id="t", harness_id="coding", harness_version="v1")
    for e in (
        Envelope("s-e", 0, "e0", 1000, None, run, RunStart()),
        Envelope("s-e", 1, "e1", 1001, "t1", run, TurnStart()),
        Envelope("s-e", 2, "e2", 1002, "t1", run, MessageEnd(role="assistant", content=[{"type": "text", "text": "hi"}],
                                                            usage=Usage(1, 1, 0, 0, 0.0))),
        Envelope("s-e", 3, "e3", 1003, "t1", run, TurnEnd(stop_reason="end")),
        Envelope("s-e", 4, "e4", 1004, None, run, RunEnd(messages=[])),
    ):
        proj.project(e)
    root = next(s for s in sink.spans if s.parent_span_id is None)

    emit_evaluation(session_id="s-e", run_id=root.prime_run_id, name="answers", label="fail", value=0.0,
                    explanation="it guessed", source="judge", evaluator="judge:answers@v1")
    (record,) = _emitted(events)
    assert record.event_name == EVENT
    assert (f"{record.trace_id:032x}", f"{record.span_id:016x}") == (root.prime_run_id, root.span_id)
    assert dict(record.attributes) == {
        "gen_ai.evaluation.name": "answers", "gen_ai.evaluation.score.value": 0.0,
        "gen_ai.evaluation.score.label": "fail", "gen_ai.evaluation.explanation": "it guessed",
        "prime.evaluation.source": "judge", "prime.session.id": "s-e", "prime.run.id": root.prime_run_id,
        "prime.evaluation.evaluator": "judge:answers@v1",
    }


def test_a_run_id_that_is_not_a_trace_id_still_names_its_run(events: InMemoryLogRecordExporter) -> None:
    emit_evaluation(session_id="s", run_id="r1", name="feedback", label="good", value=1.0, explanation=None,
                    source="person", evaluator="person")
    (record,) = _emitted(events)
    assert not record.trace_id and record.attributes["prime.run.id"] == "r1"
    assert "gen_ai.evaluation.explanation" not in record.attributes, "only what there is"


async def test_checks_judges_and_people_each_emit_one(api: Api, events: InMemoryLogRecordExporter) -> None:
    sid = await api.session()
    await api.run(sid, "r1", "fix it", start=1_000, tool_ok=False)
    await save_judge(api.factory, tenant_id="t1", composition_id="githi-v1", name="answers",
                     rubric="Answers the question that was asked.", sample_rate=1.0, model=None,
                     enabled=True, created_by="alice")
    await after_run(api.factory, JournalReader(api.factory), sid, "r1",
                    lambda s: FakeModelGatewayClient(assistant_text='{"verdict": "pass", "reason": "fine"}'))
    await api.client.put(f"/v1/sessions/{sid}/runs/r1/feedback", json={"good": False, "comment": "slow"}, headers=ALICE)
    by = [(r.attributes["prime.evaluation.source"], r.attributes["gen_ai.evaluation.name"],
           r.attributes.get("gen_ai.evaluation.score.label")) for r in _emitted(events)]
    assert ("check", "tool_errors", "fail") in by
    assert by[-2:] == [("judge", "answers", "pass"), ("person", "feedback", "bad")]
    assert _emitted(events)[-1].attributes["gen_ai.evaluation.explanation"] == "slow"
