"""Services layer for harness - business logic.

This module provides the main service classes:
- HarnessResolver: Version resolution with caching
- HarnessCatalog: In-memory catalog for testing
- CompatibilityChecker: Runtime compatibility validation
- SpawnPlanner: Subprocess spawn plan building
"""

from __future__ import annotations

from supervisor.harness.services.resolver import (
    HarnessCatalog,
    HarnessResolver,
)
from supervisor.harness.services.compat_checker import (
    CompatibilityChecker,
    load_supported_protocol_versions,
)
from supervisor.harness.services.spawn_planner import (
    SpawnPlanner,
    build_spawn_plan,
)

__all__ = [
    # Resolver
    "HarnessCatalog",
    "HarnessResolver",
    # Compatibility
    "CompatibilityChecker",
    "load_supported_protocol_versions",
    # Spawn
    "SpawnPlanner",
    "build_spawn_plan",
]
