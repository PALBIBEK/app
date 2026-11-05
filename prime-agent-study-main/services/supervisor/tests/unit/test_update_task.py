"""The task the agent shows beside the conversation: bounded, and replaced whole each time."""

from __future__ import annotations

import pytest

from supervisor.agent.events import ToolEnd
from supervisor.tools import InvalidArguments, default_registry
from supervisor.tools.builtin.update_task import MAX_STEPS, task_of
from supervisor.tools.context import ToolContext

TASK = {
    "goal": "Find the cheapest paper towels",
    "steps": [{"title": "Search Shop A", "status": "done", "note": "£3.00 for 6"}, {"title": "Search Shop B"}],
    "found": [{"label": "Shop A, 6 rolls", "value": "£3.00", "source": "https://shop-a.example/towels"}],
}


@pytest.mark.asyncio
async def test_the_task_is_what_was_sent_with_its_defaults() -> None:
    shown = await default_registry()["update_task"].run(TASK, ToolContext())

    assert shown == {
        "task": {
            "goal": "Find the cheapest paper towels",
            "steps": [{"title": "Search Shop A", "status": "done", "note": "£3.00 for 6"}, {"title": "Search Shop B", "status": "todo"}],
            "found": [{"label": "Shop A, 6 rolls", "value": "£3.00", "source": "https://shop-a.example/towels"}],
        }
    }


@pytest.mark.parametrize(
    "task",
    [
        {"steps": []},
        {**TASK, "steps": [{"title": "x", "status": "maybe"}]},
        {**TASK, "steps": [{"title": f"step {n}"} for n in range(MAX_STEPS + 1)]},
        {**TASK, "found": [{"label": "a", "value": "b", "source": "javascript:alert(1)"}]},
        {**TASK, "goal": "g" * 201},
    ],
)
@pytest.mark.asyncio
async def test_a_task_outside_its_bounds_is_refused(task: dict) -> None:
    with pytest.raises(InvalidArguments):
        await default_registry()["update_task"].run(task, ToolContext())


def test_the_journal_holds_the_task_of_a_call_that_succeeded() -> None:
    ok = ToolEnd("c1", "update_task", {"ok": True, "payload": {"task": TASK}})

    assert task_of(ok) == TASK
    assert task_of(ToolEnd("c2", "update_task", {"ok": False, "error": "bad"}, is_error=True)) is None
    assert task_of(ToolEnd("c3", "note_for_session", {"ok": True, "payload": {"task": TASK}})) is None
