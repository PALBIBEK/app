"""The replay gate: histories from earlier releases replay on the current workflow code."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.temporal.rollout import file_histories, replay

HISTORIES = Path(__file__).resolve().parents[1] / "fixtures" / "histories"


@pytest.mark.asyncio
async def test_every_release_history_replays_on_this_code() -> None:
    releases = sorted(p.name for p in HISTORIES.iterdir() if p.is_dir())
    assert releases, "no recorded release histories"
    report = await replay(file_histories(HISTORIES))
    assert report.checked >= 20, report.checked
    assert report.ok, "\n".join(f"{f.workflow_id}: {f.error}" for f in report.failures)
