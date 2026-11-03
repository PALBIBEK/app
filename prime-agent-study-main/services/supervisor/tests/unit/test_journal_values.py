"""What tools, models and providers hand over is journalled as plain JSON, on SQLite and on
Postgres (whose ``json`` refuses NaN): one odd value used to fail the whole write, and the
step's retry ran its tool again."""

from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from supervisor.agent.events import Envelope, MessageEnd, ToolEnd, ToolStart, Usage
from supervisor.api.wire import envelope_to_wire
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import decode_event, encode_event, jsonable
from supervisor.journal.writer import JournalWriter
from supervisor.seams import ToolResult
from supervisor.session.tool_outbox import result_to_wire
from tests.support.journal_db import Factory


class Color(enum.Enum):
    RED = "red"


@dataclass
class Point:
    x: int


WHEN = datetime(2026, 11, 4, 9, 30, tzinfo=UTC)
CASES: list[tuple[str, Any, Any]] = [
    ("timestamp", WHEN, "2026-11-04T09:30:00+00:00"),
    ("date", date(2026, 11, 4), "2026-11-04"),
    ("Decimal", Decimal("1.10"), 1.1),
    ("huge Decimal", Decimal("1e400"), "1E+400"),
    ("set", {3, 1, 2}, [1, 2, 3]),
    ("set of mixed items", {1, "a"}, None),  # a list of both, in no fixed order
    ("tuple", (1, "a"), [1, "a"]),
    ("text bytes", b"hello", "hello"),
    ("binary bytes", b"\x89PNG", {"base64": "iVBORw=="}),
    ("NaN", float("nan"), "NaN"),
    ("infinity", float("inf"), "Infinity"),
    ("minus infinity", -math.inf, "-Infinity"),
    ("enum", Color.RED, "red"),
    ("dataclass", Point(1), {"x": 1}),
    ("int dict keys", {1: "one"}, {"1": "one"}),
    ("path", Path("/tmp/a.txt"), "/tmp/a.txt"),
    ("nested", {"rows": [{"at": WHEN, "n": Decimal(2)}]}, {"rows": [{"at": "2026-11-04T09:30:00+00:00", "n": 2.0}]}),
]


def test_plain_json_is_left_as_it_is() -> None:
    plain = {"text": "héllo 🌍 \x00", "n": 2**70, "f": 0.5, "ok": True, "none": None, "list": [1, [2, {"k": "v"}]]}
    assert jsonable(plain) == plain
    event = MessageEnd(role="assistant", content=[{"type": "text", "text": "hi"}], usage=Usage(input_tokens=3, output_tokens=1))
    assert decode_event(encode_event(event)) == event


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "value", "stored"), CASES, ids=[c[0] for c in CASES])
async def test_a_value_a_tool_returns_is_journalled_as_json(factory: Factory, name: str, value: Any, stored: Any) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    await writer.append("s1", ToolStart(tool_call_id="c1", tool_name="calc", args={"at": value}), turn_id="r1", trace_id=None)
    await writer.append(
        "s1", ToolEnd(tool_call_id="c1", tool_name="calc", result={"ok": True, "payload": value}), turn_id="r1", trace_id=None
    )
    start, end = await reader.read("s1")
    got = end.payload.result["payload"]
    if stored is None:
        assert sorted(map(str, got)) == ["1", "a"]
    else:
        assert got == stored and start.payload.args == {"at": stored}
    json.dumps(encode_event(end.payload), allow_nan=False)  # and it is plain JSON on the wire


def test_the_wire_sends_what_the_journal_keeps() -> None:
    live = Envelope("s1", 0, "e", 0, None, None, ToolEnd(tool_call_id="c", tool_name="t", result={"at": WHEN, "x": math.nan}))
    assert envelope_to_wire(live).data["result"] == {"at": "2026-11-04T09:30:00+00:00", "x": "NaN"}


def test_a_direct_tool_call_is_kept_in_the_outbox_as_json() -> None:
    wire = result_to_wire(ToolResult(ok=True, payload={"when": WHEN, "score": math.inf}))
    assert json.dumps(wire, allow_nan=False)
    assert wire["payload"] == {"when": "2026-11-04T09:30:00+00:00", "score": "Infinity"}
