"""Step 4 journal integration — echo/Pi round-trip, crash sim, leak check."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from supervisor.agent.events import (
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from supervisor.agent.runtimes.echo.runtime import EchoRuntime
from supervisor.agent.runtimes.harness.runtime import HarnessRuntime
from supervisor.db.engine import open_db
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from tests.support.replay import ReplaySandbox


async def _journal_pair(tmp_path: Path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'j.db'}") as (engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        yield w, r


@pytest.mark.asyncio
async def test_level1_echo_round_trip(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'echo.db'}") as (engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("echo-1", runtime="echo")

        rt = EchoRuntime()
        await rt.start(None, None)

        envs = []

        async def consume() -> None:
            async for env in rt.events():
                envs.append(env)
                await w.append(
                    "echo-1",
                    env.payload,
                    turn_id=env.turn_id,
                    trace_id=env.trace_id,
                )
                if isinstance(env.payload, RunEnd):
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await rt.send("hello journal", command_id="c1")
        await asyncio.wait_for(consumer, timeout=2)
        await rt.stop()

        back = await r.read("echo-1", since=-1)
        assert len(back) == len(envs)
        assert [e.seq for e in back] == list(range(len(back)))
        assert [type(e.payload).__name__ for e in back] == [
            type(e.payload).__name__ for e in envs
        ]
        hist = await r.latest_history("echo-1")
        assert hist == await rt.get_history()


@pytest.mark.asyncio
async def test_level2_crash_simulation_unpaired_tool(tmp_path: Path) -> None:
    db_path = tmp_path / "crash.db"
    async with open_db(f"sqlite+aiosqlite:///{db_path}") as (engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("crash-1", runtime="echo")
        await w.append("crash-1", RunStart(), turn_id=None, trace_id=None)
        await w.append("crash-1", TurnStart(), turn_id="t1", trace_id=None)
        await w.append(
            "crash-1",
            ToolStart(tool_call_id="orphan", tool_name="echo", args={"x": 1}),
            turn_id="t1",
            trace_id=None,
        )
        # Writer dropped here (context exit) — no ToolEnd committed.

    # New reader on the same DB file.
    async with open_db(f"sqlite+aiosqlite:///{db_path}") as (engine, factory):
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        unpaired = await r.unpaired_tool_starts("crash-1")
        assert len(unpaired) == 1
        assert unpaired[0].tool_call_id == "orphan"

        # Negative case: append ToolEnd, nothing unpaired.
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        await w.append(
            "crash-1",
            ToolEnd(
                tool_call_id="orphan",
                tool_name="echo",
                result={"ok": True},
                is_error=False,
            ),
            turn_id="t1",
            trace_id=None,
        )
        assert await r.unpaired_tool_starts("crash-1") == []


@pytest.mark.asyncio
async def test_level3_pi_round_trip(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'pi.db'}") as (engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("harness-1", runtime="harness")

        sandbox = ReplaySandbox()
        rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
        await rt.start(sandbox, None)
        await rt.send("read a.txt", command_id="req-1")

        envs = []
        async for env in rt.events():
            envs.append(env)
            await w.append(
                "harness-1",
                env.payload,
                turn_id=env.turn_id,
                trace_id=env.trace_id,
            )
            if isinstance(env.payload, RunEnd):
                break
        await rt.stop()

        back = await r.read("harness-1", since=-1)
        assert len(back) == len(envs)
        assert [e.seq for e in back] == list(range(len(back)))
        assert [type(e.payload).__name__ for e in back] == [
            type(e.payload).__name__ for e in envs
        ]


@pytest.mark.asyncio
async def test_session_reconstructable_from_read_and_latest_history(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'recon.db'}") as (engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("recon", runtime="echo")
        await w.append_batch(
            "recon",
            [
                (RunStart(), None, None),
                (TurnStart(), "t1", None),
                (ToolStart(tool_call_id="c1", tool_name="echo", args={"text": "hi"}), "t1", None),
                (
                    ToolEnd(
                        tool_call_id="c1",
                        tool_name="echo",
                        result={"text": "hi"},
                        is_error=False,
                    ),
                    "t1",
                    None,
                ),
                (TurnEnd(stop_reason="stop"), "t1", None),
                (
                    RunEnd(
                        messages=[
                            {"role": "user", "content": "hi"},
                            {"role": "assistant", "content": "hi"},
                        ]
                    ),
                    None,
                    None,
                ),
            ],
        )
        events = await r.read("recon", since=-1)
        hist = await r.latest_history("recon")
        assert len(events) == 6
        assert hist == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hi"},
        ]
        assert await r.unpaired_tool_starts("recon") == []
