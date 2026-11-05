from __future__ import annotations

from typing import Any, Final

from pydantic import Field

from supervisor.genui import problems, surface_of
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

__all__ = ["MAX_MESSAGES", "show_ui"]

MAX_MESSAGES: Final = 50


class ShowUiArgs(ToolArgs):
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_MESSAGES)
    """A2UI v0.9 messages, in order (createSurface first for a new surface)"""


@tool(tool_class=ToolClass.PURE, needs_workspace=False)
async def show_ui(args: ShowUiArgs, ctx: ToolContext) -> dict[str, Any]:
    """Show the user a structured view in the chat (a report, a comparison, a summary card) as A2UI v0.9 messages drawn from a fixed catalog; use it for results a person will scan, and plain replies for the rest. Send, in order: {"version": "v0.9", "createSurface": {"surfaceId": "report", "catalogId": "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"}}; then {"version": "v0.9", "updateComponents": {"surfaceId": "report", "components": [...]}}, a flat list of {"id", "component", ...properties}, one with id "root", children named by id (Column, Row, List: "children": ["a", "b"]; Card: "child": "a"; Tabs: "tabs": [{"title": "...", "child": "a"}]); optionally {"version": "v0.9", "updateDataModel": {"surfaceId": "report", "path": "/", "value": {...}}}, a property then reading it as {"path": "/key"}. Calling again for the same surfaceId with updateComponents or updateDataModel updates it in place; deleteSurface removes it. Text takes simple Markdown (headings, lists, bold; no links or images) and variant h1-h5, caption or body: write each source's URL in the text. Components (* required): AudioPlayer(description, url*); Card(child*); Column(align, children*, justify); Divider(axis); Icon(name*); Image(description, fit, url*, variant); List(align, children*, direction); Row(align, children*, justify); Tabs(tabs*); Text(text*, variant); Video(url*).

    Declarative generative UI (A2UI, ``supervisor.genui``): the messages are checked against
    A2UI's own schemas and refused with what is wrong, so the model can correct them; nothing
    runs, and the frontend (or any AG-UI client, ``supervisor.agui``) draws them from its
    catalog. The view lives in the session's journal, as this call's arguments, so it is
    drawn again wherever the conversation is shown.
    """
    if found := problems(args.messages):
        raise InvalidArguments("the messages are not A2UI the chat can draw: " + "; ".join(found))
    surfaces = list(dict.fromkeys(s for m in args.messages if (s := surface_of(m)) is not None))
    return {"shown": surfaces, "messages": len(args.messages)}
