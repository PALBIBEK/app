"""Deep-link: span attrs → journal row (Phase1 §6 / §4)."""

from __future__ import annotations

from supervisor.agent.events import (
    Envelope,
    MessageEnd,
    RunEnd,
    RunStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.obs.projector import JournalProjector
from tests.support.telemetry_fakes import FakeSpanSink


def test_span_coords_resolve_to_journal_row() -> None:
    journal: list[Envelope] = []
    sink = FakeSpanSink(validate=False)
    proj = JournalProjector(
        sink=sink, tenant_id="t", harness_id="coding", harness_version="v1"
    )
    tid = "9" * 32
    events = [
        Envelope("s-dl", 0, "e0", 1000, None, tid, RunStart()),
        Envelope("s-dl", 1, "e1", 1001, "t1", tid, TurnStart()),
        Envelope(
            "s-dl",
            2,
            "e2",
            1002,
            "t1",
            tid,
            MessageEnd(
                role="assistant",
                content=[{"type": "text", "text": "hello"}],
                usage=Usage(1, 1, 0, 0, 0.0),
            ),
        ),
        Envelope("s-dl", 3, "e3", 1003, "t1", tid, TurnEnd(stop_reason="end")),
        Envelope("s-dl", 4, "e4", 1004, None, tid, RunEnd(messages=[])),
    ]
    for e in events:
        journal.append(e)
        proj.project(e)

    chat = next(s for s in sink.spans if s.name == "chat")
    sid = chat.attributes["yal.session.id"]
    seq = chat.attributes["yal.journal.seq"]
    row = next(e for e in journal if e.session_id == sid and e.seq == seq)
    assert isinstance(row.payload, MessageEnd)
    assert row.event_id == "e2"
