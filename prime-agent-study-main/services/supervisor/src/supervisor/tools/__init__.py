"""Tool implementations and the registry a pinned tool set resolves against.

Layering, outermost last::

    supervisor.tools                    implementations + registry (this package)
    seams.providers.tools.registry_tools  RegistryToolProvider: the ToolProvider seam
    catalog.providers.binder            pinned ToolSet → RegistryToolProvider

This package depends on neither the catalog nor the seams' providers, so any
seam provider or test can use a tool without dragging in a session.
"""

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import DEFAULT_TIMEOUT_S, ToolContext
from supervisor.tools.errors import (
    FileMissing,
    InvalidArguments,
    PathEscape,
    ToolError,
    ToolNotFound,
    ToolTimeout,
    UnknownTools,
    UnsupportedCapability,
    WorkspaceUnavailable,
)
from supervisor.tools.registry import ToolRegistry, default_registry, tool
from supervisor.tools.spec import ToolClass, ToolSpec, json_schema
from supervisor.tools.workspace import (
    CommandResult,
    Entry,
    HostWorkspace,
    TreeWorkspace,
    Workspace,
)

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "CommandResult",
    "Entry",
    "FileMissing",
    "HostWorkspace",
    "InvalidArguments",
    "PathEscape",
    "ToolArgs",
    "ToolClass",
    "ToolContext",
    "ToolError",
    "ToolNotFound",
    "ToolRegistry",
    "ToolSpec",
    "ToolTimeout",
    "TreeWorkspace",
    "UnknownTools",
    "UnsupportedCapability",
    "Workspace",
    "WorkspaceUnavailable",
    "default_registry",
    "json_schema",
    "path_field",
    "tool",
]
