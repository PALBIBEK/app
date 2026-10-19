"""Dynamic routing system for sandboxes.

This package implements QM's routing pattern:
    - routes.py: SandboxRoute dataclass + RouteStore protocol
    - route_store.py: Memory and Postgres route storage (15s cache)
    - router.py: SandboxRouter with security guards
    - capabilities.py: Capability detection and loss tracking
    - advisory_lock.py: Postgres locks for migration serialization
    - sandbox_migration.py: Migrate scopes between backends

Routing Flow:
    1. Router receives create(scope_id)
    2. Looks up route in RouteStore (cached 15s)
    3. Falls back to default backend if no route
    4. Runs security guards (multi-tenant, image, env)
    5. Delegates to selected backend provider
"""

# Routes
# Advisory Locks
from supervisor.sandbox.routing.advisory_lock import (
    AdvisoryLock,
    AdvisoryLockProtocol,
    MemoryAdvisoryLock,
)

# Capabilities
from supervisor.sandbox.routing.capabilities import (
    PROVIDER_CAPABILITIES,
    SandboxCapability,
    aggregate_capabilities,
    capabilities_lost_for_backends,
    capabilities_lost_moving_to,
    detect_capabilities,
    detect_provider_capabilities,
)

# Route Stores
from supervisor.sandbox.routing.route_store import (
    MemoryRouteStore,
    PostgresRouteStore,
)

# Router
from supervisor.sandbox.routing.router import (
    BackendUnavailableError,
    CapabilityUnsupportedError,
    RouterError,
    SandboxRouter,
    SandboxRouterImpl,
    create_sandbox_router,
)
from supervisor.sandbox.routing.routes import (
    RouteNotFoundError,
    RouteStore,
    SandboxRoute,
)

# Migration
from supervisor.sandbox.routing.sandbox_migration import (
    MigrationBlockedError,
    MigrationResult,
    SandboxMigrationRunner,
)

__all__ = [
    # Routes
    "SandboxRoute",
    "RouteStore",
    "RouteNotFoundError",
    # Route Stores
    "MemoryRouteStore",
    "PostgresRouteStore",
    # Advisory Locks
    "AdvisoryLock",
    "AdvisoryLockProtocol",
    "MemoryAdvisoryLock",
    # Capabilities
    "SandboxCapability",
    "PROVIDER_CAPABILITIES",
    "detect_capabilities",
    "detect_provider_capabilities",
    "capabilities_lost_moving_to",
    "capabilities_lost_for_backends",
    "aggregate_capabilities",
    # Router
    "SandboxRouter",
    "SandboxRouterImpl",
    "create_sandbox_router",
    "RouterError",
    "BackendUnavailableError",
    "CapabilityUnsupportedError",
    # Migration
    "SandboxMigrationRunner",
    "MigrationResult",
    "MigrationBlockedError",
]
