"""Every build reads what earlier builds journalled (tests/fixtures/events/README.md), and
a type it does not know (a newer build wrote it) is passed through, not refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.agent.events import Envelope, RunEnd, RunStart, ToolEnd, UnknownEvent
from supervisor.api.wire import envelope_to_wire
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import _EVENT_TYPES, RETIRED_TYPES, decode_event, encode_event
from supervisor.journal.writer import JournalWriter
from tests.support.event_samples import samples
from tests.support.journal_db import Factory

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "events"
RECORDED = sorted(SAMPLES.glob("r*.json"))


@pytest.mark.parametrize("path", RECORDED, ids=[p.stem for p in RECORDED])
def test_every_recorded_event_still_reads(path: Path) -> None:
    for name, payload in json.loads(path.read_text()).items():
        event = decode_event(payload)
        if name in RETIRED_TYPES:  # no build writes it now: it passes through as it was
            assert event == UnknownEvent(name, {k: v for k, v in payload.items() if k != "_type"})
            continue
        assert not isinstance(event, UnknownEvent), f"{path.name}: {name} is no longer a known type"
        assert type(event) is _EVENT_TYPES[name]
        assert encode_event(event)["_type"] == name


def test_the_newest_recording_covers_every_event_type() -> None:
    newest = max(RECORDED, key=lambda p: p.stat().st_mtime)
    recorded = set().union(*(json.loads(p.read_text()) for p in RECORDED))
    missing = set(_EVENT_TYPES) - recorded
    assert not missing, (
        f"no recorded sample of {sorted(missing)}: record one (tests/fixtures/events/README.md); newest is {newest.name}"
    )
    assert set(samples()) == set(_EVENT_TYPES)


def test_an_event_type_this_build_does_not_know_passes_through() -> None:
    stored = {"_type": "FutureEvent", "x": 1, "nested": {"y": [2]}}
    event = decode_event(stored)
    assert event == UnknownEvent("FutureEvent", {"x": 1, "nested": {"y": [2]}})
    assert encode_event(event) == stored, "a fork copies it as it was"
    wire = envelope_to_wire(Envelope("s", 3, "e", 0, "r1", None, event))
    assert (wire.type, wire.data) == ("future_event", {"x": 1, "nested": {"y": [2]}})
    assert Envelope("s", 3, "e", 0, "r1", None, event).event_type == "FutureEvent"


@pytest.mark.asyncio
async def test_a_session_holding_one_still_reads_and_continues(factory: Factory) -> None:
    """What a rolling deploy leaves behind: every read path goes on past it, and the session
    takes new events after it."""
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    await writer.append("s1", RunStart(), turn_id="r1", trace_id=None)
    await writer.append("s1", UnknownEvent("FutureEvent", {"x": 1}), turn_id="r1", trace_id=None)
    await writer.append("s1", ToolEnd(tool_call_id="c", tool_name="t", result={}), turn_id="r1", trace_id=None)
    await writer.append("s1", RunEnd(messages=[{"role": "user", "content": "hi"}]), turn_id="r1", trace_id=None)
    for events in (await reader.read("s1"), await reader.read_turn("s1", "r1"), await reader.read("s1", since=0)):
        assert any(isinstance(e.payload, UnknownEvent) for e in events)
    assert [e.event_type for e in await reader.read("s1")] == ["RunStart", "FutureEvent", "ToolEnd", "RunEnd"]
    assert await reader.latest_history("s1") == [{"role": "user", "content": "hi"}]
