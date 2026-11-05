"""``update_task``: the agent keeps the task it is working on where the person can see it.

On a long task (a search across shops, a form filled step by step) the agent states the
goal, its steps with their status, and what it has found so far; the chat shows it as a
panel beside the conversation, and an AG-UI client receives it as the session's state
(``STATE_SNAPSHOT``, ``supervisor.agui``).

Each call sends the whole task, never a change to it: the latest successful call is the
task (``task_of``), so it needs no store of its own, lives in the session's
journal, survives a restart and replays like the rest, and goes when the session is erased.
The schema bounds it, so it stays a summary a person scans.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from supervisor.agent.events import ToolEnd
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

__all__ = ["MAX_FOUND", "MAX_STEPS", "NAME", "task_of", "update_task"]

NAME: Final = "update_task"
MAX_STEPS: Final = 20
MAX_FOUND: Final = 30

type StepStatus = Literal["todo", "doing", "done", "failed", "skipped"]


class _Part(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="ignore", frozen=True)


class Step(_Part):
    title: str = Field(min_length=1, max_length=120)
    """The step in a few words (e.g. "Search Shop A")"""
    status: StepStatus = "todo"
    note: str | None = Field(default=None, max_length=200)
    """Optional: what came of it, or why it failed"""


class Found(_Part):
    label: str = Field(min_length=1, max_length=120)
    """What it is (e.g. "Shop A, 6 rolls")"""
    value: str = Field(min_length=1, max_length=200)
    """What was found (e.g. "£3.00")"""
    source: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    """Optional: the page it was found on"""


class UpdateTaskArgs(ToolArgs):
    goal: str = Field(min_length=1, max_length=200)
    """The task, as the user asked it, in one line"""
    steps: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)
    """Your plan, in order, each with its status"""
    found: list[Found] = Field(default_factory=list, max_length=MAX_FOUND)
    """What you have found so far that the answer depends on"""


@tool(tool_class=ToolClass.PURE, needs_workspace=False)
async def update_task(args: UpdateTaskArgs, ctx: ToolContext) -> dict[str, Any]:
    """Show the user the task you are working on, beside the conversation: its goal, your steps with their status (todo, doing, done, failed, skipped) and what you have found so far. Use it on tasks of several steps: once when you have a plan, then whenever a step starts or ends or you find something. Send the whole task every time (it replaces the last one); keep it short, it is for the user to scan."""
    return {"task": args.model_dump(mode="json", exclude_none=True)}


def task_of(event: object) -> dict[str, Any] | None:
    """The task a journal event holds: an ``update_task`` call that succeeded."""
    if not isinstance(event, ToolEnd) or event.tool_name != NAME or event.is_error:
        return None
    payload = event.result.get("payload") if isinstance(event.result, dict) else None
    task = payload.get("task") if isinstance(payload, dict) else None
    return task if isinstance(task, dict) else None
