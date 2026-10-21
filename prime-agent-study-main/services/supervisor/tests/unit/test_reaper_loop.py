"""C1 — span reaper loop behaviour (Phase1-TrackA-closeout)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from supervisor.agent.events import Envelope, RunStart
from supervisor.obs.projector import JournalProjector
from supervisor.obs.reaper import ReaperConfig, SpanReaper, run_reaper_loop
from tests.support.telemetry_fakes import FakeSpanSink
from tests.support.waits import until


@pytest.mark.asyncio
async def test_reaper_loop_survives_tick_failure(caplog: pytest.LogCaptureFixture) -> None:
    class BoomReaper(SpanReaper):
        def tick(self, *, now_ms: int | None = None) -> str | None:
            raise RuntimeError("boom")

    sink = FakeSpanSink(validate=False)
    proj = JournalProjector(
        sink=sink, tenant_id="t", harness_id="h", harness_version="v"
    )
    reaper = BoomReaper(projector=proj, config=ReaperConfig(run_idle_timeout_ms=1))
    stop = asyncio.Event()
    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(run_reaper_loop(reaper, 0.05, stop))

        async def failures() -> int:
            return sum("span_reaper tick failed" in r.message for r in caplog.records)

        # The loop survives a failing tick and keeps sweeping: wait for a second one.
        await until(failures, lambda n: n >= 2)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
    assert any("span_reaper tick failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_reaper_loop_stops_promptly() -> None:
    sink = FakeSpanSink(validate=False)
    proj = JournalProjector(
        sink=sink, tenant_id="t", harness_id="h", harness_version="v"
    )
    reaper = SpanReaper(projector=proj, config=ReaperConfig())
    stop = asyncio.Event()
    task = asyncio.create_task(run_reaper_loop(reaper, 60.0, stop))
    await asyncio.sleep(0)  # let the loop start its first 60 s wait
    stop.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_reaper_loop_ticks_and_closes_idle() -> None:
    sink = FakeSpanSink(validate=False)
    proj = JournalProjector(
        sink=sink, tenant_id="t", harness_id="h", harness_version="v"
    )
    proj.project(
        Envelope(
            session_id="s",
            seq=0,
            event_id="e",
            ts_ms=1,
            turn_id=None,
            trace_id="c" * 32,
            payload=RunStart(),
        )
    )
    cfg = ReaperConfig(run_idle_timeout_ms=10, span_max_age_ms=10**12)
    reaper = SpanReaper(projector=proj, config=cfg)
    # Force last_event old by re-projecting with past ts already done; tick with now
    assert reaper.tick(now_ms=1000) == "idle_timeout"
    assert not proj.has_open_run
