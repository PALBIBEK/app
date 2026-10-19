"""Catalog providers layer — runtime binding and assembly.

This module contains:
- binder.py: Bind session pins to runtime seams
- catalog_prompt.py: CatalogPromptAssembler
- catalog_tools.py: pinned ToolSet → RegistryToolProvider
- model_route.py: Model routing configuration
- memory_resolve.py: Memory profile resolution
"""

from supervisor.catalog.providers.binder import bind_runtime_seams
from supervisor.catalog.providers.catalog_prompt import CatalogPromptAssembler
from supervisor.catalog.providers.catalog_tools import (
    bind_tool_set,
    tool_descriptors,
)
from supervisor.catalog.providers.memory_resolve import load_pinned_memory_profile
from supervisor.catalog.providers.model_route import apply_runtime_model_route

__all__ = [
    "CatalogPromptAssembler",
    "apply_runtime_model_route",
    "bind_runtime_seams",
    "bind_tool_set",
    "load_pinned_memory_profile",
    "tool_descriptors",
]
