"""Tool lookup and execution.

The one invariant worth stating: **`execute` never raises.** A tool that
crashes, a sandbox violation, a hallucinated tool name -- all become ordinary
error results the model reads and can correct on the next turn. Letting any
of them propagate would end a run over a mistyped path, which is both hostile
and unnecessary: the model is perfectly capable of recovering if told what
went wrong.
"""

from __future__ import annotations

import traceback
from typing import Any

from ..errors import GithiError, ToolNotFound
from .ask_user import ask_user_tool
from .bash import bash_tool
from ..types import text_blocks
from .base import Tool, ToolContext, ToolResult, fail
from .edit import edit_tool
from .grep import grep_tool
from .interrupt_agent import interrupt_agent_tool
from .job_kill import job_kill_tool
from .job_output import job_output_tool
from .list_agents import list_agents_tool
from .ls import find_tool, ls_tool
from .read import read_tool
from .send_message import send_message_tool
from .subagent import subagent_tool
from .subagent_continuable import subagent_continuable_tool
from .subagent_fork import subagent_fork_tool
from .webfetch import webfetch_tool
from .websearch import websearch_tool
from .write import write_tool


def default_tools() -> list[Tool]:
    """Pi's built-ins plus web, ask_user, subagent, and control tools."""
    return [bash_tool(), read_tool(), write_tool(), edit_tool(),
            grep_tool(), find_tool(), ls_tool(),
            webfetch_tool(), websearch_tool(),
            ask_user_tool(), subagent_tool(), subagent_fork_tool(),
            subagent_continuable_tool(), send_message_tool(),
            interrupt_agent_tool(), list_agents_tool(),
            job_output_tool(), job_kill_tool()]


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                # Silent shadowing would make which implementation runs depend
                # on list order -- an invisible bug with a visible symptom
                # much later.
                raise ValueError(f"duplicate tool name: {tool.name!r}")
            self._tools[tool.name] = tool
        self._active: list[str] | None = None
        """Which tools are *offered*, or None for all of them.

        Separate from `_tools` because narrowing must be reversible: a phase
        machine turns tools off for one phase and back on for the next, and
        deleting them would mean rebuilding the registry each time."""

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(name) from None

    def names(self) -> list[str]:
        """Every registered tool, offered or not."""
        return list(self._tools)

    def active_names(self) -> list[str]:
        return list(self._active) if self._active is not None else list(self._tools)

    def set_active(self, names: list[str]) -> None:
        """Narrow the offered set.

        Unknown names raise rather than being skipped: a typo would otherwise
        silently shrink the tool set, and the symptom -- a model that
        inexplicably stops acting -- shows up far from the cause.
        """
        unknown = [n for n in names if n not in self._tools]
        if unknown:
            raise ValueError(
                f"unknown tool name(s): {', '.join(sorted(unknown))}. "
                f"Registered: {', '.join(sorted(self._tools))}.")
        self._active = list(names)

    def to_wire(self) -> list[dict[str, Any]]:
        return [self._tools[n].to_wire() for n in self.active_names()]

    def add(self, tool: Tool) -> None:
        """Register a tool discovered at runtime.

        pi's `AgentToolResult.added_tool_names` reports tools a result made
        available; this is how they become callable. Re-registering an
        existing name is ignored rather than raised -- a capability rebuilt
        on a later turn is normal, and failing the run over it would be
        absurd.
        """
        self._tools.setdefault(tool.name, tool)

    def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        available = ", ".join(self.active_names()) or "none"
        try:
            tool = self.get(name)
        except ToolNotFound:
            return fail(f"no tool named {name!r}. Available tools: {available}.")

        if name not in self.active_names():
            # Registered but not offered this phase. Reported the same way as
            # a missing tool, because from the model's side it is one -- and
            # executing it anyway would make narrowing advisory rather than
            # enforced.
            return fail(f"tool {name!r} is not available right now. "
                        f"Available tools: {available}.")

        try:
            return tool.run(args or {}, ctx)
        except GithiError as exc:
            # Anticipated: policy refusals and the like. The message is
            # already written for the model.
            return fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - a tool bug must not end the run
            return fail(
                f"tool {name!r} raised {type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(limit=3),
            )
