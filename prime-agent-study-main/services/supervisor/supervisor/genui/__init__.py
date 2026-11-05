"""Generative UI: what the agent shows a person, as A2UI v0.9 (https://a2ui.org) data.

The agent describes a surface (a report, a comparison, a summary card) as A2UI messages:
``createSurface``, then ``updateComponents`` (a flat list of components, one with id
``root``) and ``updateDataModel``. They are checked against A2UI's own JSON schemas, vendored
unchanged in ``a2ui_v0_9/`` from ``@a2ui/web_core`` 0.15.0 (Apache-2.0, its LICENSE beside
them; ``catalog.json`` is filed as ``basic_catalog.json``), and drawn by A2UI's own renderer
in the frontend, or by any AG-UI client that draws A2UI (``supervisor.agui``). No code the
agent writes runs: the client draws components from its catalog, in its own styling.

A surface may use the basic catalog's components except ``Modal``: display ones, and inputs
(text fields, pickers, check boxes, sliders, dates) whose values a ``Button`` sends. A
button's action must be an event, which reaches the agent as the person's answer
(``supervisor.genui.actions``); a client-side function call would act without the agent
knowing.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Iterator, Mapping
from importlib import resources
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

__all__ = [
    "CATALOG_ID",
    "COMPONENTS",
    "DISPLAY_COMPONENTS",
    "INPUT_COMPONENTS",
    "SHOW_UI",
    "SPEC_VERSION",
    "catalog_summary",
    "problems",
    "surface_of",
    "validator",
]

SHOW_UI: Final = "show_ui"
"""The tool that shows A2UI surfaces (``supervisor.tools.builtin.show_ui``)."""
SPEC_VERSION: Final = "v0.9"
CATALOG_ID: Final = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
DISPLAY_COMPONENTS: Final = frozenset(
    {"Text", "Image", "Icon", "Video", "AudioPlayer", "Row", "Column", "List", "Card", "Tabs", "Divider"}
)
"""The basic catalog's components that only show something."""
INPUT_COMPONENTS: Final = frozenset({"Button", "TextField", "CheckBox", "ChoicePicker", "Slider", "DateTimeInput"})
"""The ones a person acts with: a button sends the agent an event, with the inputs' values."""
COMPONENTS: Final = DISPLAY_COMPONENTS | INPUT_COMPONENTS
"""What a surface may use: the basic catalog but ``Modal`` (a dialog over the chat)."""
_SPEC: Final = "https://a2ui.org/specification/v0_9/"
_KINDS: Final = ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")
MAX_PROBLEMS: Final = 5


def _schema(name: str) -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("a2ui_v0_9", name).read_text(encoding="utf-8"))


@functools.cache
def _registry() -> Registry:
    """The spec's schemas by their ids. ``catalog.json`` is the slot they leave for a surface's
    catalog: here, always the basic one."""
    catalog = Resource.from_contents(_schema("basic_catalog.json"))
    return Registry().with_resources(
        [
            (f"{_SPEC}server_to_client.json", Resource.from_contents(_schema("server_to_client.json"))),
            (f"{_SPEC}common_types.json", Resource.from_contents(_schema("common_types.json"))),
            # The one schema that names no draft: it is written to 2020-12, as the others say they are.
            (f"{_SPEC}client_to_server.json", DRAFT202012.create_resource(_schema("client_to_server.json"))),
            (CATALOG_ID, catalog),
            (f"{_SPEC}catalog.json", catalog),
        ]
    )


@functools.cache
def validator(ref: str) -> Draft202012Validator:
    """A validator for the schema at ``ref``: a vendored schema's id, with a JSON pointer."""
    return Draft202012Validator({"$ref": ref}, registry=_registry())


_MESSAGE: Final = f"{_SPEC}server_to_client.json"


def _component(name: str) -> str:
    return f"{CATALOG_ID}#/components/{name}"


def surface_of(message: Mapping[str, Any]) -> str | None:
    """The surface an A2UI message is about, or None for one that is not a message."""
    for kind in _KINDS:
        body = message.get(kind)
        if isinstance(body, Mapping) and isinstance(body.get("surfaceId"), str):
            return str(body["surfaceId"])
    return None


def problems(messages: list[Any]) -> list[str]:
    """What is wrong with ``messages`` as A2UI the platform draws (at most ``MAX_PROBLEMS``);
    none when every one is a valid v0.9 message on the basic catalog's ``COMPONENTS``."""
    found: list[str] = []
    for at, message in enumerate(messages):
        found += [f"messages[{at}]: {p}" for p in _message_problems(message)]
        if len(found) >= MAX_PROBLEMS:
            break
    return found[:MAX_PROBLEMS]


def _message_problems(message: Any) -> Iterator[str]:
    """A message's problems, most specific first: its kind and version, then each component
    against its own schema in the catalog (the message schema can only say that a component
    matched none of them), then the rest of the message."""
    if not isinstance(message, Mapping) or not any(kind in message for kind in _KINDS):
        yield f"not an A2UI {SPEC_VERSION} message: one of {', '.join(_KINDS)}, with \"version\": \"{SPEC_VERSION}\""
        return
    if message.get("version") != SPEC_VERSION:
        yield f"version is {message.get('version')!r}: it must be {SPEC_VERSION!r}"
        return
    components = message.get("updateComponents", {}).get("components")
    wrong = [p for c in components if isinstance(c, Mapping) for p in _component_problems(c)] if isinstance(components, list) else []
    if wrong:
        yield from wrong
        return
    if (error := _first(_MESSAGE, message)) is not None:
        yield _said(error)
        return
    if (catalog := message.get("createSurface", {}).get("catalogId")) != CATALOG_ID and "createSurface" in message:
        yield f"catalogId is {catalog!r}: use the basic catalog, {CATALOG_ID!r}"


def _component_problems(component: Mapping[str, Any]) -> Iterator[str]:
    name, ident = component.get("component"), component.get("id")
    if name not in COMPONENTS:
        yield f"component {ident!r} is {name!r}: this platform draws {', '.join(sorted(COMPONENTS))}"
    elif (error := _first(_component(str(name)), component)) is not None:
        yield f"component {ident!r} ({name}): {_said(error)}"
    elif name == "Button" and "event" not in component["action"]:
        yield f"component {ident!r} (Button): its action must be an event (\"action\": {{\"event\": {{\"name\": ...}}}}), which reaches you"


def _first(ref: str, instance: Any) -> ValidationError | None:
    """The most relevant error of ``instance`` against ``ref``. An error elsewhere leaves the
    properties around it unevaluated, so "unevaluated properties" is the cause only when
    nothing else is wrong."""
    errors = list(validator(ref).iter_errors(instance))
    causes = [e for e in errors if e.validator != "unevaluatedProperties"]
    return best_match(causes or errors)


def _said(error: ValidationError) -> str:
    """An error, briefly: where in the message (when not the whole of it), and what."""
    where = "/".join(str(p) for p in error.absolute_path)
    return (f"{where}: {error.message}" if where else error.message)[:300]


@functools.cache
def catalog_summary() -> str:
    """The components a surface may use and their properties (required ones marked *), from
    the vendored catalog itself, so what the model is told cannot drift from what is checked."""
    components = _schema("basic_catalog.json")["components"]
    lines = []
    for name in sorted(COMPONENTS):
        parts = components[name].get("allOf", [components[name]])
        properties = {k for part in parts for k in part.get("properties", {}) if k != "component"}
        required = {k for part in parts for k in part.get("required", [])}
        lines.append(f"{name}(" + ", ".join(f"{p}*" if p in required else p for p in sorted(properties)) + ")")
    return "; ".join(lines)
