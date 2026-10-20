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
from supervisor.sandbox.routing.advisory_lock import AdvisoryLockProtocol

# Capabilities
from supervisor.sandbox.routing.capabilities import PROVIDER_CAPABILITIES, SandboxCapability

# Route Stores
from supervisor.sandbox.routing.route_store import MemoryRouteStore

# Router
from supervisor.sandbox.routing.router import SandboxRouter
from supervisor.sandbox.routing.routes import RouteStore, SandboxRoute

# Migration
from supervisor.sandbox.routing.sandbox_migration import MigrationResult

__all__ = [
    "PROVIDER_CAPABILITIES",
    "AdvisoryLockProtocol",
    "MemoryRouteStore",
    "MigrationResult",
    "RouteStore",
    "SandboxCapability",
    "SandboxRoute",
    "SandboxRouter",
]
