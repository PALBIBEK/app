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
    """Show the user a structured view in the chat (a report, a comparison, a summary card, or a choice or short form for them to answer) as A2UI v0.9 messages drawn from a fixed catalog; use it for results a person will scan or a decision you need from them, and plain replies for the rest. Send, in order: {"version": "v0.9", "createSurface": {"surfaceId": "report", "catalogId": "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"}}; then {"version": "v0.9", "updateComponents": {"surfaceId": "report", "components": [...]}}, a flat list of {"id", "component", ...properties}, one with id "root", children named by id (Column, Row, List: "children": ["a", "b"]; Card, Button: "child": "a"; Tabs: "tabs": [{"title": "...", "child": "a"}]); optionally {"version": "v0.9", "updateDataModel": {"surfaceId": "report", "path": "/", "value": {...}}}, a property then reading it as {"path": "/key"}. Calling again for the same surfaceId with updateComponents or updateDataModel updates it in place; deleteSurface removes it. Text takes simple Markdown (headings, lists, bold; no links or images) and variant h1-h5, caption or body: write each source's URL in the text. To ask: bind each input's value to the data model (ChoicePicker "value": {"path": "/pick"} with "options": [{"label": "...", "value": "a"}], TextField "value": {"path": "/note"}; give them initial values with updateDataModel) and add a Button whose "action": {"event": {"name": "chosen", "context": {"pick": {"path": "/pick"}}}} sends them; when the person clicks it you get a user message "UI action: {...}" with the event name and its context, and end your turn to wait for it. A drawing takes one answer: draw it again to ask again. Components (* required): AudioPlayer(description, url*); Button(action*, child*, variant); Card(child*); CheckBox(label*, value*); ChoicePicker(displayStyle, filterable, label, options*, value*, variant); Column(align, children*, justify); DateTimeInput(enableDate, enableTime, label, max, min, value*); Divider(axis); Icon(name*); Image(description, fit, url*, variant); List(align, children*, direction); Row(align, children*, justify); Slider(label, max*, min, value*); Tabs(tabs*); Text(text*, variant); TextField(label*, validationRegexp, value, variant); Video(url*).

    Declarative generative UI (A2UI, ``supervisor.genui``): the messages are checked against
    A2UI's own schemas and refused with what is wrong, so the model can correct them; nothing
    runs, and the frontend (or any AG-UI client, ``supervisor.agui``) draws them from its
    catalog. The view lives in the session's journal, as this call's arguments, so it is
    drawn again wherever the conversation is shown. A button's event comes back as the
    person's answer (``supervisor.genui.actions``), worded as the description says.
    """
    if found := problems(args.messages):
        raise InvalidArguments("the messages are not A2UI the chat can draw: " + "; ".join(found))
    surfaces = list(dict.fromkeys(s for m in args.messages if (s := surface_of(m)) is not None))
    return {"shown": surfaces, "messages": len(args.messages)}
