"""Services layer for harness - business logic.

This module provides the main service classes:
- HarnessResolver: Version resolution with caching
- HarnessCatalog: In-memory catalog for testing
- CompatibilityChecker: Runtime compatibility validation
"""

from __future__ import annotations

from supervisor.harness.services.compat_checker import (
    CompatibilityChecker,
    load_supported_protocol_versions,
)
from supervisor.harness.services.resolver import (
    HarnessCatalog,
    HarnessResolver,
)

__all__ = [
    # Compatibility
    "CompatibilityChecker",
    # Resolver
    "HarnessCatalog",
    "HarnessResolver",
    "load_supported_protocol_versions",
]
