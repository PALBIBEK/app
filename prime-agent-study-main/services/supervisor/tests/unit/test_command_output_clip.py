"""A command's output reaches the model bounded, keeping its start and its end.

Found live: ``seq 1 300000`` sent 76,650 characters (the next prompt went from 2.4k
to 39k tokens), and the kept part was the start, so the last number was lost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.tools import HostWorkspace
from supervisor.tools.workspace import _MAX_STREAM_CHARS, _clip


def test_short_output_is_untouched() -> None:
    assert _clip(b"hello\n") == ("hello\n", False)


def test_long_output_keeps_its_start_and_end_within_the_cap() -> None:
    text = "".join(f"{i}\n" for i in range(1, 300_001))
    clipped, cut = _clip(text.encode())
    assert cut
    assert clipped.startswith("1\n2\n3\n") and clipped.endswith("299999\n300000\n")
    assert "characters cut from the middle" in clipped
    assert len(clipped) < _MAX_STREAM_CHARS + 100


@pytest.mark.asyncio
async def test_bash_says_how_to_see_the_rest(tmp_path: Path) -> None:
    from supervisor.agent.runtimes.githi.render import tool_result_text
    from supervisor.tools import default_registry
    from supervisor.tools.context import ToolContext

    ctx = ToolContext(session_id="s1", bound_workspace=HostWorkspace(tmp_path), timeout_s=30)
    payload = await default_registry()["bash"].run({"command": "seq 1 300000"}, ctx)
    assert payload["output"].endswith("299999\n300000")  # the tail, as pi keeps it
    text = tool_result_text({"ok": True, "payload": payload}, "bash")
    assert text.endswith(
        f"[Showing lines 298001-300000 of 300000. Full output: {payload['full_output']}]"
    )  # where the whole output is kept
    assert (tmp_path / payload["full_output"]).read_text().endswith("300000\n")
