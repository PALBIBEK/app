"""A ToolSet may only pin tools the supervisor can run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from supervisor.catalog.core.errors import InvalidArtifact
from supervisor.catalog.core.types import ToolSet
from supervisor.tools import ToolSpec, default_registry

__all__ = ["require_implemented"]


def require_implemented(
    tool_set: ToolSet,
    registry: Mapping[str, ToolSpec[Any]] | None = None,
) -> None:
    """Raise ``InvalidArtifact`` (422) naming every pinned tool without an implementation."""
    specs = registry if registry is not None else default_registry()
    if missing := sorted(name for name in tool_set.tools if name not in specs):
        raise InvalidArtifact(
            "tool_set",
            tool_set.name,
            f"no implementation registered for: {', '.join(missing)}",
        )
