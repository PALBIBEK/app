"""Pinned ``ToolSet`` → ``RegistryToolProvider``: where catalog meets implementation.

A tool set is published as data (names, descriptions, JSON schemas, digests).
Every tool it names must have an implementation in the tool registry, which
is checked twice: when the tool set is published
(``catalog.services.require_implemented``), and here, when a session binds it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from supervisor.catalog.core.errors import InvalidArtifact
from supervisor.catalog.core.types import ToolSet
from supervisor.seams.core.types import ToolDescriptor
from supervisor.seams.providers.tools.registry_tools import RegistryToolProvider
from supervisor.tools import ToolSpec, UnknownTools

__all__ = ["bind_tool_set", "tool_descriptors"]


def tool_descriptors(tool_set: ToolSet) -> list[ToolDescriptor]:
    """What the model is shown: the pinned definitions, in name order."""
    return [
        ToolDescriptor(name=td.name, description=td.description, parameters=dict(td.parameters))
        for _, td in sorted(tool_set.definitions.items())
        if td.name in tool_set.tools
    ]


def bind_tool_set(
    tool_set: ToolSet,
    registry: Mapping[str, ToolSpec[Any]] | None = None,
) -> RegistryToolProvider:
    """The session's tool seam: exactly the pinned tools, backed by the registry."""
    try:
        return RegistryToolProvider(tool_descriptors(tool_set), registry=registry)
    except UnknownTools as exc:
        raise InvalidArtifact(
            "tool_set",
            tool_set.name,
            f"no implementation registered for: {', '.join(exc.names)}",
        ) from exc
