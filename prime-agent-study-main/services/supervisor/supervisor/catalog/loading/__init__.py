"""The shipped catalog: loaded from ``harness/`` at boot (``loader``), checked (``verify``),
and a composition that cannot run is withdrawn rather than failing the boot (``bootstrap``)."""

from supervisor.catalog.loading.bootstrap import (
    agent_platform_root,
    bootstrap_composition_catalog,
    default_catalog_dir,
    resolve_catalog_dir,
)
from supervisor.catalog.loading.loader import load_composition_catalog
from supervisor.catalog.loading.verify import (
    composition_problems,
    list_catalog_problems,
    verify_composition_catalog,
)

__all__ = [
    # Bootstrap
    "agent_platform_root",
    "bootstrap_composition_catalog",
    # Verify
    "composition_problems",
    "default_catalog_dir",
    "list_catalog_problems",
    # Loader
    "load_composition_catalog",
    "resolve_catalog_dir",
    "verify_composition_catalog",
]
