"""G1 at the platform's concurrency (``docs/memory-gaps.md``): many sessions' captures read
and condense their transcripts at once, on SQLite and on Postgres (where they really run
side by side: set SUPERVISOR_TEST_PG_URL; CI does).

Each capture reads only its own runs, through the journal's (session, run, seq) index,
so fifty of them at once each get exactly their own steps, in order, and nothing of
another session's or of a run journalled meanwhile.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from supervisor.agent.events import MessageEnd, RunStart, ToolEnd, TurnEnd
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.memory.transcript import TranscriptBudget, condense, run_items
from tests.support.journal_db import Factory
from tests.support.memory_pg import run_workers

pytestmark = pytest.mark.asyncio

SESSIONS = 50
STEPS = 40


@pytest.fixture(autouse=True)
def _bounded_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fifty captures on ten connections, as a worker's captures share its pool: the reads
    queue for a connection, and four test workers stay inside a default server's 100
    connections (a worker's own pool, 20 + 10, would not). Set before ``factory`` makes its
    engine: an autouse fixture is set up first."""
    monkeypatch.setenv("DB_POOL_SIZE", "5")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "5")


async def _write_run(w: JournalWriter, sid: str, run_id: str, steps: int, *, stopped: bool = False) -> None:
    await w.ensure_session(sid)
    await w.append(sid, RunStart(), turn_id=run_id, trace_id=None)
    await w.append(sid, MessageEnd(role="user", content=[{"type": "text", "text": f"{sid} asks"}]), turn_id=run_id,
                   trace_id=None)
    for step in range(steps):
        call = f"{sid}-c{step}"
        await w.append(sid, MessageEnd(role="assistant", content=[
            {"type": "tool_call", "id": call, "name": "bash", "arguments": {"command": f"step {step}"}}]),
            turn_id=run_id, trace_id=None)
        await w.append(sid, ToolEnd(tool_call_id=call, tool_name="bash",
                                    result={"ok": True, "payload": f"{sid} result {step} " + "x" * 2000}),
                       turn_id=run_id, trace_id=None)
    if stopped:
        await w.append(sid, TurnEnd(stop_reason="aborted"), turn_id=run_id, trace_id=None)
    else:
        await w.append(sid, MessageEnd(role="assistant", content=[{"type": "text", "text": f"{sid} answered"}]),
                       turn_id=run_id, trace_id=None)
        await w.append(sid, TurnEnd(stop_reason="stop"), turn_id=run_id, trace_id=None)


async def test_concurrent_captures_each_read_exactly_their_own_runs(factory: Factory) -> None:
    w, reader = JournalWriter(factory), JournalReader(factory)
    await asyncio.gather(*(_write_run(w, f"s{i:02d}", "r1", STEPS, stopped=i % 5 == 0) for i in range(SESSIONS)))

    async def capture(i: int) -> list[dict]:
        sid = f"s{i:02d}"
        # The session's next run is being journalled while this one is captured, as a live
        # session's is: the capture reads its burst's run, never what came after.
        noisy = asyncio.create_task(_write_run(w, sid, "r2", 3))
        items = run_items(await reader.read_turn(sid, "r1"))
        await noisy
        return json.loads(condense(items, TranscriptBudget.for_attempt(1)).json)

    transcripts = await run_workers(SESSIONS, capture)

    for i, items in enumerate(transcripts):
        sid = f"s{i:02d}"
        text = json.dumps(items)
        assert items[0] == {"type": "user", "content": [{"type": "text", "text": f"{sid} asks"}]}
        results = [item["content"] for item in items if item["type"] == "tool_result"]
        assert [r.split(" ")[2] for r in results] == [str(n) for n in range(STEPS)], "every step, in order"
        assert all(r.startswith(f"{sid} result") for r in results)
        others = [f"s{j:02d} " for j in range(SESSIONS) if j != i]
        assert not any(o in text for o in others), f"{sid}'s transcript holds another session's events"
        assert text.count(f"{sid} asks") == 1, "its burst's run only, not the run written meanwhile"
        assert (items[-1]["content"] == "[... the user stopped this turn here, before it finished ...]") == (i % 5 == 0)
