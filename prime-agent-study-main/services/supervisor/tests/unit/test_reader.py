"""JournalReader tests — replay, unpaired tools, latest history."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.events import (
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnStart,
)
from supervisor.db.engine import open_db
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter


@pytest.fixture
async def journal(tmp_path: Path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'j.db'}") as (engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("s1", runtime="echo")
        yield w, r


@pytest.mark.asyncio
async def test_read_since_minus_one_returns_all_in_order(journal) -> None:
    w, r = journal
    await w.append("s1", RunStart(), turn_id=None, trace_id=None)
    await w.append("s1", TurnStart(), turn_id="t1", trace_id=None)
    await w.append(
        "s1",
        ToolStart(tool_call_id="c1", tool_name="echo", args={}),
        turn_id="t1",
        trace_id=None,
    )
    envs = await r.read("s1", since=-1)
    assert [e.seq for e in envs] == [0, 1, 2]
    assert [type(e.payload).__name__ for e in envs] == [
        "RunStart",
        "TurnStart",
        "ToolStart",
    ]


@pytest.mark.asyncio
async def test_read_since_n_excludes_n(journal) -> None:
    w, r = journal
    for _ in range(3):
        await w.append("s1", RunStart(), turn_id=None, trace_id=None)
    envs = await r.read("s1", since=1)
    assert [e.seq for e in envs] == [2]


@pytest.mark.asyncio
async def test_unpaired_tool_start_detected(journal) -> None:
    w, r = journal
    await w.append(
        "s1",
        ToolStart(tool_call_id="c1", tool_name="echo", args={"a": 1}),
        turn_id="t",
        trace_id=None,
    )
    unpaired = await r.unpaired_tool_starts("s1")
    assert len(unpaired) == 1
    assert unpaired[0].tool_call_id == "c1"


@pytest.mark.asyncio
async def test_paired_start_end_not_reported(journal) -> None:
    w, r = journal
    await w.append(
        "s1",
        ToolStart(tool_call_id="c1", tool_name="echo", args={}),
        turn_id="t",
        trace_id=None,
    )
    await w.append(
        "s1",
        ToolEnd(tool_call_id="c1", tool_name="echo", result={}, is_error=False),
        turn_id="t",
        trace_id=None,
    )
    assert await r.unpaired_tool_starts("s1") == []


@pytest.mark.asyncio
async def test_two_tools_one_unpaired(journal) -> None:
    w, r = journal
    await w.append(
        "s1",
        ToolStart(tool_call_id="ok", tool_name="echo", args={}),
        turn_id="t",
        trace_id=None,
    )
    await w.append(
        "s1",
        ToolStart(tool_call_id="bad", tool_name="echo", args={}),
        turn_id="t",
        trace_id=None,
    )
    await w.append(
        "s1",
        ToolEnd(tool_call_id="ok", tool_name="echo", result={}, is_error=False),
        turn_id="t",
        trace_id=None,
    )
    unpaired = await r.unpaired_tool_starts("s1")
    assert [u.tool_call_id for u in unpaired] == ["bad"]


@pytest.mark.asyncio
async def test_latest_history_returns_last_run_end_not_first(journal) -> None:
    w, r = journal
    await w.append(
        "s1",
        RunEnd(messages=[{"role": "user", "content": "first"}]),
        turn_id=None,
        trace_id=None,
    )
    await w.append(
        "s1",
        RunEnd(messages=[{"role": "user", "content": "second"}]),
        turn_id=None,
        trace_id=None,
    )
    hist = await r.latest_history("s1")
    assert hist == [{"role": "user", "content": "second"}]


@pytest.mark.asyncio
async def test_empty_session_returns_empty_not_error(journal) -> None:
    w, r = journal
    assert await r.read("s1", since=-1) == []
    assert await r.unpaired_tool_starts("s1") == []
    assert await r.latest_history("s1") is None
    assert await r.last_seq("s1") == -1


@pytest.mark.asyncio
async def test_blob_payload_round_trips(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}") as (engine, factory):
        blobs = tmp_path / "blobs"
        w = JournalWriter(factory, blob_dir=blobs, blob_threshold=64)
        r = JournalReader(factory, blob_dir=blobs)
        await w.ensure_session("s1", runtime="echo")
        big = "z" * 5000
        await w.append(
            "s1",
            ToolStart(tool_call_id="c1", tool_name="echo", args={"text": big}),
            turn_id=None,
            trace_id=None,
        )
        envs = await r.read("s1", since=-1)
        assert isinstance(envs[0].payload, ToolStart)
        assert envs[0].payload.args["text"] == big
