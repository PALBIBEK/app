"""Step 2/3 integration — Level 1 fixture replay through Pi runtime."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from supervisor.agent.events import MessageEnd, RunEnd, RunStart, ToolEnd, ToolStart
from supervisor.agent.runtimes.harness.framing import read_frames
from supervisor.agent.runtimes.harness.runtime import HarnessRuntime
from supervisor.agent.runtimes.harness.translate import translate
from tests.support.replay import ReplaySandbox

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "harness-events.jsonl"
NONE_TYPES = frozenset({"message_update", "message_start", "response"})


class _Bytes:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._done = False

    async def read(self, n: int) -> bytes:
        if self._done:
            return b""
        self._done = True
        return self._data


@pytest.mark.asyncio
async def test_level1_fixture_replay_framing_translate_runtime() -> None:
    data = FIXTURE.read_bytes()
    wc_l = data.count(b"\n")
    frames = [f async for f in read_frames(_Bytes(data))]
    assert len(frames) == wc_l

    canonical = [e for e in (translate(f) for f in frames) if e is not None]
    expected_n = sum(1 for f in frames if f.get("type") not in NONE_TYPES)
    assert len(canonical) == expected_n

    sandbox = ReplaySandbox()
    rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
    await rt.start(sandbox, None)
    await rt.send("read a.txt and tell me what it says", command_id="req-1")

    envs = []
    async for env in rt.events():
        envs.append(env)
        if isinstance(env.payload, RunEnd):
            break

    assert [e.seq for e in envs] == list(range(len(envs)))
    assert sum(1 for e in envs if isinstance(e.payload, RunStart)) == 1
    assert sum(1 for e in envs if isinstance(e.payload, RunEnd)) == 1

    starts = {e.payload.tool_call_id for e in envs if isinstance(e.payload, ToolStart)}
    ends = {e.payload.tool_call_id for e in envs if isinstance(e.payload, ToolEnd)}
    assert starts == ends and len(starts) == 1

    fixture_cost = 0.0
    for f in frames:
        if f.get("type") != "message_end":
            continue
        usage = (f.get("message") or {}).get("usage") or {}
        cost = (usage.get("cost") or {}).get("total") or 0
        fixture_cost += float(cost)
    summed = sum(
        (e.payload.usage.cost_total if e.payload.usage else 0.0)
        for e in envs
        if isinstance(e.payload, MessageEnd)
    )
    assert abs(summed - fixture_cost) < 1e-12
    await rt.stop()


@pytest.mark.skipif(os.environ.get("RUN_PI_LIVE") != "1", reason="set RUN_PI_LIVE=1 for live Pi")
@pytest.mark.asyncio
async def test_level2_live_harness() -> None:
    pytest.skip("live Pi harness not wired in this environment yet")
