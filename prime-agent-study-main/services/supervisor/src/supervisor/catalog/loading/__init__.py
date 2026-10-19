"""Catalog loading layer — bootstrap and verification.

This module contains:
- bootstrap.py: Load catalog from disk at startup
- loader.py: Parse YAML/JSON artifact files
- verify.py: Catalog integrity verification
"""

from supervisor.catalog.loading.bootstrap import (
    agent_platform_root,
    bootstrap_composition_catalog,
    default_catalog_dir,
    resolve_catalog_dir,
)

from supervisor.catalog.loading.loader import load_composition_catalog

from supervisor.catalog.loading.verify import (
    composition_problems,
    verify_harness_catalog,
    verify_composition_catalog,
    list_catalog_problems,
    dump_composition_catalog,
)

__all__ = [
    # Bootstrap
    "agent_platform_root",
    "bootstrap_composition_catalog",
    "default_catalog_dir",
    "resolve_catalog_dir",
    # Loader
    "load_composition_catalog",
    # Verify
    "composition_problems",
    "verify_harness_catalog",
    "verify_composition_catalog",
    "list_catalog_problems",
    "dump_composition_catalog",
]
