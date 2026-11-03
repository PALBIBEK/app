"""Every event the supervisor journals takes one path: written, then announced (published
live, projected into tracing) as it was stored. The tool outbox, memory compaction and
recall, a job's end and a turn's failure used to write to the journal directly, so live
streams and traces never saw them."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from supervisor.agent.events import CompactionEnd, CompactionStart, ToolEnd, ToolStart
from supervisor.db.models import EventRow
from supervisor.journal.writer import JournalWriter, Written
from supervisor.seams import CompactionPreparation, ToolResult
from supervisor.session.memory_ops import MemoryOps
from supervisor.session.tool_outbox import ToolOutbox
from tests.support.journal_db import Factory

SUPERVISOR = Path(__file__).resolve().parents[2] / "supervisor"
_WRITES = {"append", "append_batch", "append_once", "write", "append_in_session", "append_missing"}
# Writers allowed to call the journal writer: the one path itself, and the outbox,
# which writes in its own transaction and announces once it commits.
_ALLOWED = {
    "journal",  # the package itself
    "session/manager_base.py",  # _record, _record_once
    "session/tool_outbox.py",
    "session/manager.py",  # a fork's copy: history, not new events (nothing to announce)
}


def test_nothing_writes_to_the_journal_around_the_one_path() -> None:
    offenders = []
    for path in SUPERVISOR.rglob("*.py"):
        rel = path.relative_to(SUPERVISOR).as_posix()
        if rel.split("/")[0] in _ALLOWED or rel in _ALLOWED:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _WRITES
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_writer"
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"journal writes that skip SessionManagerBase._record: {offenders}"


class _Announced:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any, Written]] = []

    async def __call__(self, session_id: str, payload: Any, written: Written, *, turn_id: Any, trace_id: Any) -> None:
        self.events.append((session_id, payload, written))


@pytest.mark.asyncio
async def test_the_outbox_announces_what_it_journalled_as_stored(factory: Factory) -> None:
    writer, announced = JournalWriter(factory), _Announced()
    outbox = ToolOutbox(factory, writer, announce=announced)
    await writer.ensure_session("s1")
    await outbox.begin("s1", tool_call_id="c1", tool_name="read", arguments={"path": "a"}, trace_id=None)
    await outbox.complete("s1", tool_call_id="c1", tool_name="read", result=ToolResult(ok=True, payload={"t": 1}), trace_id=None)
    async with factory() as db:
        rows = (await db.execute(select(EventRow).order_by(EventRow.seq))).scalars().all()
    assert [type(p).__name__ for _, p, _ in announced.events] == ["ToolStart", "ToolEnd"]
    assert [(w.seq, w.event_id, w.ts_ms) for _, _, w in announced.events] == [
        (r.seq, r.event_id, r.ts_ms) for r in rows
    ]
    assert isinstance(announced.events[0][1], ToolStart) and isinstance(announced.events[1][1], ToolEnd)

    # A retried completion finds it done: nothing journalled, nothing announced again.
    await outbox.complete("s1", tool_call_id="c1", tool_name="read", result=ToolResult(ok=True, payload={"t": 1}), trace_id=None)
    assert len(announced.events) == 2


@pytest.mark.asyncio
async def test_memory_compaction_records_through_the_session(factory: Factory) -> None:
    recorded: list[Any] = []

    async def record(session_id: str, payload: Any, *, turn_id: Any, trace_id: Any) -> int:
        recorded.append(payload)
        return len(recorded)

    class Refuses:
        def before_compact(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("no")

    ops = MemoryOps(session_id="s1", memory=Refuses(), record=record)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await ops.compact(CompactionPreparation(
        first_kept_entry_id="e1", messages_to_summarize=[], tokens_before=10,
        previous_summary=None, context_window=1000, reserve_tokens=100,
    ), reason="manual")
    assert [type(p) for p in recorded] == [CompactionStart, CompactionEnd]
