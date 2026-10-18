"""Catalog providers layer — runtime binding and assembly.

This module contains:
- binder.py: Bind session pins to runtime seams
- catalog_prompt.py: CatalogPromptAssembler
- catalog_tools.py: CatalogToolProvider
- model_route.py: Model routing configuration
- memory_resolve.py: Memory profile resolution
"""

from supervisor.catalog.providers.catalog_prompt import CatalogPromptAssembler
from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.catalog.providers.binder import bind_runtime_seams
from supervisor.catalog.providers.model_route import apply_runtime_model_route
from supervisor.catalog.providers.memory_resolve import load_pinned_memory_profile

__all__ = [
    "CatalogPromptAssembler",
    "CatalogToolProvider",
    "bind_runtime_seams",
    "apply_runtime_model_route",
    "load_pinned_memory_profile",
]
