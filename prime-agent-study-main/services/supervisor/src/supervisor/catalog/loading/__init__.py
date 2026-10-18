"""Catalog loading layer — bootstrap and verification.

This module contains:
- bootstrap.py: Load catalog from disk at startup
- loader.py: Parse YAML/JSON artifact files
- verify.py: Catalog integrity verification
"""

from supervisor.catalog.loading.bootstrap import (
    bootstrap_composition_catalog,
    resolve_catalog_dir,
)

from supervisor.catalog.loading.loader import (
    load_prompt_pack,
    load_tool_set,
    load_composition,
    load_sandbox_profile,
    load_memory_profile,
)

from supervisor.catalog.loading.verify import (
    composition_problems,
    verify_harness_catalog,
    verify_composition_catalog,
    list_catalog_problems,
    dump_composition_catalog,
)

__all__ = [
    # Bootstrap
    "bootstrap_composition_catalog",
    "resolve_catalog_dir",
    # Loader
    "load_prompt_pack",
    "load_tool_set",
    "load_composition",
    "load_sandbox_profile",
    "load_memory_profile",
    # Verify
    "composition_problems",
    "verify_harness_catalog",
    "verify_composition_catalog",
    "list_catalog_problems",
    "dump_composition_catalog",
]
