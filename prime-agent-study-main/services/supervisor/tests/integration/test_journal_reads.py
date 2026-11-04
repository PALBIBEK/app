"""A run reads the journal it needs, not the whole of it: a long session made every run
(and every model step) read every event the session had (#110's gaps)."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from supervisor.journal.reader import JournalReader
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import LS, WRITE, Env, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")

SUPERVISOR = str(Path(__file__).resolve().parents[2] / "supervisor")


async def test_runs_with_tools_never_read_the_whole_journal(
    env: Env, model: Callable[..., ScriptedGateway], monkeypatch: pytest.MonkeyPatch
) -> None:
    model(WRITE, LS, WRITE, LS)
    state = await env.state()
    full_reads: list[str] = []
    turns: list[str] = []
    read, read_turn = JournalReader.read, JournalReader.read_turn

    async def spied(self: JournalReader, session_id: str, *, since: int = -1) -> Any:
        caller = inspect.stack()[1]
        if since < 0 and caller.filename.startswith(SUPERVISOR):  # not the test's own polling
            full_reads.append(f"{Path(caller.filename).name}:{caller.lineno} {caller.function}")
        return await read(self, session_id, since=since)

    async def spied_turn(self: JournalReader, session_id: str, turn_id: str) -> Any:
        turns.append(turn_id)  # the run's own reads: the spies are in the worker's path
        return await read_turn(self, session_id, turn_id)

    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "write and list"))
        await env.wait_for(state, sid, run_ended())
        monkeypatch.setattr(JournalReader, "read", spied)
        monkeypatch.setattr(JournalReader, "read_turn", spied_turn)
        await env.sessions.submit(sid, Message("c2", "again"))
        await env.wait_for(state, sid, run_ended(2))
    assert not full_reads, f"read the whole journal: {full_reads}"
    assert turns, "the spies saw the run's reads"
