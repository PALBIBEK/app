"""Clients are sent an event's fields less the supervisor's own: a run's end used to send
the whole conversation again (every message, already sent as it happened), and the text
injected into the model's context went to every client too."""

from __future__ import annotations

import json

from supervisor.agent.events import (
    CompactionEnd,
    Envelope,
    MemoryContext,
    MemoryInjected,
    MemoryRecall,
    MessageEnd,
    ProjectContext,
    RunEnd,
    Usage,
)
from supervisor.api.wire import WIRE_VERSION, envelope_to_wire, wire_data
from supervisor.journal.serde import decode_event
from tests.support.event_samples import samples


def _wire(payload: object) -> dict:
    return envelope_to_wire(Envelope("s", 1, "e", 0, "r1", None, payload)).model_dump()  # type: ignore[arg-type]


def test_a_run_end_carries_its_usage_not_the_conversation() -> None:
    conversation = [{"role": "user", "content": "x" * 10_000}] * 50
    wire = _wire(RunEnd(messages=conversation, total_usage=Usage(1, 2)))
    assert wire["type"] == "run_end" and wire["v"] == WIRE_VERSION == 2
    assert "messages" not in wire["data"] and wire["data"]["total_usage"]["output_tokens"] == 2
    assert len(json.dumps(wire)) < 500


def test_injected_context_and_fingerprints_stay_in_the_supervisor() -> None:
    assert wire_data(MemoryInjected(run_id="r1", text="secret recall", shown=[{"h": 1}, {"h": 2}])) == {
        "run_id": "r1",
        "items": 2,
    }
    assert "text" not in wire_data(MemoryRecall(block_id="b", source="semantic", token_estimate=3, injected=True, text="t"))
    assert wire_data(ProjectContext(path="AGENTS.md", text="instructions", digest="d")) == {"path": "AGENTS.md", "digest": "d"}
    assert wire_data(MemoryContext(audience="fingerprint", through_seq=4)) == {"through_seq": 4}
    end = wire_data(CompactionEnd(compaction_id="c", stop_reason="ok", tokens_after=9, messages=[{"role": "user"}]))
    assert "messages" not in end and end["tokens_after"] == 9


def test_what_clients_read_is_sent_as_it_was() -> None:
    reply = MessageEnd(role="assistant", content=[{"type": "text", "text": "hi"}], usage=Usage(1, 2))
    assert _wire(reply)["data"]["content"] == [{"type": "text", "text": "hi"}]


def test_every_event_type_reaches_the_wire() -> None:
    for encoded in samples().values():
        wire = _wire(decode_event(encoded))
        assert wire["v"] == WIRE_VERSION and isinstance(wire["data"], dict)
