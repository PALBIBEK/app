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
    dump_composition_catalog,
    list_catalog_problems,
    verify_composition_catalog,
    verify_harness_catalog,
)

__all__ = [
    # Bootstrap
    "agent_platform_root",
    "bootstrap_composition_catalog",
    # Verify
    "composition_problems",
    "default_catalog_dir",
    "dump_composition_catalog",
    "list_catalog_problems",
    # Loader
    "load_composition_catalog",
    "resolve_catalog_dir",
    "verify_composition_catalog",
    "verify_harness_catalog",
]
