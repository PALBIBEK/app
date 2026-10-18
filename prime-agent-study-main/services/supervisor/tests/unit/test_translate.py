"""Pi wire → canonical membrane tests (fixture-driven, no I/O)."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from supervisor.agent.events import (
    AgentEvent,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    Usage,
)
from supervisor.agent.runtimes.harness.translate import translate

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "harness-events.jsonl"
CAMEL = re.compile(r"[a-z][A-Z]")
# Opaque Pi history blobs — membrane maps the event shape, not nested content.
OPAQUE = frozenset({"content", "messages", "args", "result"})


def _load_fixture() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


def test_every_fixture_line_translates_without_raising() -> None:
    for raw in _load_fixture():
        translate(raw)  # must not raise


def test_tool_execution_start_and_end_share_tool_call_id() -> None:
    start = end = None
    for raw in _load_fixture():
        ev = translate(raw)
        if isinstance(ev, ToolStart):
            start = ev
        elif isinstance(ev, ToolEnd):
            end = ev
    assert start is not None and end is not None
    assert start.tool_call_id == end.tool_call_id == "toolu_bdrk_01DZisUPVW4XQoDoi9XQKN6J"


def test_exactly_one_run_end_with_full_message_list() -> None:
    ends = [translate(r) for r in _load_fixture()]
    run_ends = [e for e in ends if isinstance(e, RunEnd)]
    assert len(run_ends) == 1
    assert len(run_ends[0].messages) == 4


def test_all_message_update_return_none() -> None:
    for raw in _load_fixture():
        if raw.get("type") == "message_update":
            assert translate(raw) is None


def test_unknown_type_returns_none() -> None:
    assert translate({"type": "future_thing"}) is None


def test_usage_from_fixture_message_end() -> None:
    usages: list[Usage] = []
    for raw in _load_fixture():
        ev = translate(raw)
        if isinstance(ev, MessageEnd) and ev.usage is not None:
            usages.append(ev.usage)
    assert usages[0] == Usage(
        input_tokens=10,
        output_tokens=96,
        cache_read=0,
        cache_write=1801,
        cost_total=0.00822375,
    )
    assert usages[1] == Usage(
        input_tokens=72,
        output_tokens=17,
        cache_read=0,
        cache_write=1778,
        cost_total=0.0071385,
    )


def test_no_camel_case_keys_in_translated_output() -> None:
    def walk(obj: object, *, path: str) -> None:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            for f in dataclasses.fields(obj):
                assert CAMEL.search(f.name) is None, f"{path}.{f.name}"
                if f.name in OPAQUE:
                    continue
                walk(getattr(obj, f.name), path=f"{path}.{f.name}")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                assert isinstance(k, str)
                assert CAMEL.search(k) is None, f"{path}.{k}"
                walk(v, path=f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, path=f"{path}[{i}]")

    for raw in _load_fixture():
        ev = translate(raw)
        if ev is not None:
            walk(ev, path=type(ev).__name__)


def test_fixture_replay_yields_clean_canonical_sequence() -> None:
    events: list[AgentEvent] = []
    for raw in _load_fixture():
        ev = translate(raw)
        if ev is not None:
            events.append(ev)
    assert isinstance(events[0], RunStart)
    assert isinstance(events[-1], RunEnd)
    assert sum(1 for e in events if isinstance(e, TurnEnd)) == 2
    none_types = {"message_update", "message_start", "response"}
    expected = sum(1 for r in _load_fixture() if r.get("type") not in none_types)
    assert len(events) == expected
