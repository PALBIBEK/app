"""The replay gate: histories from earlier releases replay on the current workflow code."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from supervisor.temporal.rollout import file_histories, replay
from tests.support.temporal_env import patch_ids

HISTORIES = Path(__file__).resolve().parents[1] / "fixtures" / "histories"
WORKFLOWS = Path(__file__).resolve().parents[2] / "supervisor" / "temporal"


def _patches_in_code() -> set[str]:
    """Every patch id the workflows branch on (``workflow.patched`` or ``_handled_here``)."""
    ids: set[str] = set()
    for path in WORKFLOWS.glob("*workflow*.py"):
        code = path.read_text()
        for name, pid in re.findall(r"^(_[A-Z_]+) = \"([a-z0-9-]+)\"", code, re.MULTILINE):
            if re.search(rf"(workflow\.patched|_handled_here)\({name}\b", code):
                ids.add(pid)
    return ids


@pytest.mark.asyncio
async def test_every_release_history_replays_on_this_code() -> None:
    releases = sorted(p.name for p in HISTORIES.iterdir() if p.is_dir())
    assert releases, "no recorded release histories"
    report = await replay(file_histories(HISTORIES))
    assert report.checked >= 20, report.checked
    assert report.ok, "\n".join(f"{f.workflow_id}: {f.error}" for f in report.failures)


@pytest.mark.asyncio
async def test_every_patched_path_has_a_recording() -> None:
    """A path behind a patch is new code that running sessions take. Without a recorded
    history that took it, a later change could break those sessions unseen. Record one
    (tests/fixtures/histories/README.md) when you add a patch."""
    in_code = _patches_in_code()
    assert len(in_code) >= 10, in_code  # the scan itself works
    recorded: set[str] = set()
    async for history in file_histories(HISTORIES):
        recorded |= patch_ids(history)
    assert not in_code - recorded, f"patched paths with no recorded history: {sorted(in_code - recorded)}"
