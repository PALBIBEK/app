"""``note_for_session``: the agent keeps a lesson for the rest of its session.

When the agent learns something this session's later runs should know (a command that does
not work here, how this project runs its tests), it notes it. From the session's next run
on, its notes are in the system prompt (``<session_notes>``); they never reach another
session and never change the agent's version.

This is Prime Agent's local refinement (``crates/pa-core/src/refinement/planner.rs``): the
default editable store "is local to the current Prime Agent session"; use it for "session-
specific progress, active task state, current-run coordination notes, temporary blockers".
As there, an edit applies when the current turn ends, never mid-run ("Refinement never runs
mid-cell"; the harness "applies changes and rebuilds the system prompt"), so a run's prompt,
and its cached prefix, stays the same from step to step. A lesson worth keeping for every
session goes through the improvement loop instead: the reflection reads session notes as
unverified evidence, and only a gated, approved change makes one part of the agent.

The note is the tool's result, so it lives in the session's journal: it outlasts
compaction, and erasing the session erases it.
"""

from __future__ import annotations

from typing import Final

from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

__all__ = ["MAX_NOTE", "NAME", "note_for_session"]

NAME: Final = "note_for_session"
MAX_NOTE: Final = 500


class NoteArgs(ToolArgs):
    content: str
    """The lesson, as an instruction to yourself (e.g. "Run tests with make test, not pytest")"""


@tool(tool_class=ToolClass.IDEMPOTENT, needs_workspace=False)
async def note_for_session(args: NoteArgs, ctx: ToolContext) -> dict[str, str]:
    """Keep a lesson for the rest of this session: it is in your instructions from the next run on.

    Use it for what this session's later runs should know: a command that fails here, how the
    project builds or tests, a decision the user made. It does not reach other sessions.
    """
    note = " ".join(args.content.split())
    if not note:
        raise InvalidArguments("content must not be empty")
    if len(note) > MAX_NOTE:
        raise InvalidArguments(f"a note is at most {MAX_NOTE} characters")
    return {"note": note}
